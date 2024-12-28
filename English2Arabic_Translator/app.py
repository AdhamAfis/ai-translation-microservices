from fastapi import FastAPI, APIRouter, BackgroundTasks, HTTPException, Request
from shared.base_service import BaseService
from fastapi.middleware.gzip import GZipMiddleware
from cachetools import TTLCache
from confluent_kafka import Producer, Consumer, KafkaError  # Updated imports
from pydantic import BaseModel
from dotenv import load_dotenv
import hashlib
import logging
import asyncio
import json
import time
import os
from contextlib import asynccontextmanager
from circuitbreaker import circuit
import uuid
from threading import Thread
from transformers import MarianMTModel, MarianTokenizer

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup Script
    app.state.kafka_producer = await init_kafka_producer()
    logger.info("[SUCCESS:E2A] Kafka producer initialized")
    yield

    # Shutdown Script
    if hasattr(app.state, 'kafka_producer'):
        app.state.kafka_producer.close()
        logger.info("[SUCCESS:E2A] Kafka producer closed")

api_router = APIRouter(prefix="/api/v1")
service = BaseService("e2a_translator")
app = service.app
app.lifespan = lifespan
app.add_middleware(GZipMiddleware, minimum_size=1000)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

translation_cache = TTLCache(maxsize=100, ttl=3600)

model = MarianMTModel.from_pretrained(os.getenv('MODEL_DIRECTORY', './model'))
tokenizer = MarianTokenizer.from_pretrained(os.getenv('MODEL_DIRECTORY', './model'))

class TranslationRequest(BaseModel):
    correlation_id: str
    user_id: str
    chat_id: str
    text: str

class TranslationStatus(BaseModel):
    id: str
    status: str
    result: dict = None
    error: str = None

# TODO: Replace with Redis
translation_status = {}

@circuit(failure_threshold=5, recovery_timeout=60)
def generate_translation(prompt: str) -> str:
    with service.request_latency.time():
        try:
            inputs = tokenizer([prompt], return_tensors="pt", padding=True)
            outputs = model.generate(**inputs)
            translation = tokenizer.decode(outputs[0], skip_special_tokens=True)
            return translation
        except Exception as e:
            logger.error(f"Translation generation error: {str(e)}")
            raise

async def init_kafka_producer():
    try:
        # Initialize KafkaProducer from confluent_kafka
        conf = {
            'bootstrap.servers': os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092'),
            'acks': 'all',
            'retries': 3
        }
        producer = Producer(conf)
        return producer
    except KafkaError as e:
        logger.error(f"Kafka producer initialization error: {e}")
        return None

async def init_kafka_consumer():
    try:
        conf = {
            'bootstrap.servers': os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092'),
            'group.id': os.getenv('KAFKA_CONSUMER_GROUP', 'e2a-consumer-group'),
            'auto.offset.reset': 'earliest',
        }
        consumer = Consumer(conf)
        consumer.subscribe([os.getenv('KAFKA_E2A_TRANSLATION_TOPIC', 'e2a-translation')])
        return consumer
    except KafkaError as e:
        logger.error(f"Kafka consumer initialization error: {e}")
        return None

async def send_to_kafka(producer, message):
    if not producer:
        return
    try:
        producer.produce(os.getenv('KAFKA_E2A_TRANSLATION_RESPONSE', 'e2a-translation-response'), value=json.dumps(message), callback=delivery_report)
        producer.flush()
    except Exception as e:
        logger.error(f"Kafka send error: {e}")

def delivery_report(err, msg):
    if err is not None:
        logger.error(f"Message delivery failed: {err}")
    else:
        logger.info(f"Message delivered to {msg.topic()} [{msg.partition()}]")

def consume_and_process_kafka_messages(consumer, producer):
    while True:
        try:
            msg = consumer.poll(timeout=1.0)
            if msg is None:  # No message
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    logger.error(f"Kafka consumer error: {msg.error()}")
                continue

            # Process the consumed message
            message = json.loads(msg.value().decode('utf-8'))
            logger.info(f"Consumed message: {message}")

            # Extract data
            request_data = message.value
            request = TranslationRequest(**request_data)

            if not user_id or not chat_id or not text:
                logger.error("Invalid message format received, skipping.")
                continue

            # Generate translation
            prompt = f">>ara<< {request.text}"
            translation = generate_translation(prompt)

            # Send the result to the response topic
            result = {
                'correlation_id': request.correlation_id,
                "user_id": request.user_id,
                "chat_id": request.chat_id,
                "text": request.text,
                "translation": translation,
            }
            send_to_kafka(producer, result)
            logger.info(f"Produced response for message: {result}")

        except Exception as e:
            logger.error(f"Error processing Kafka message: {e}")

@api_router.post("/translate/en2ar")
async def translate_text(request: TranslationRequest, background_tasks: BackgroundTasks):
    request_id = str(uuid.uuid4())
    try:
        service.request_count.inc()
        cache_key = hashlib.md5(f"{request.text}".encode()).hexdigest()

        if cache_key in translation_cache:
            result = translation_cache[cache_key]
            translation_status[request_id] = {"status": "completed", "result": result}
            return {
                'id': request_id,
                'status': 'completed',
                'result': result,
                'cache_hit': True
            }

        try:
            prompt = f">>ara<< {request.text}"
            translation = generate_translation(prompt)

            result = {"translation": translation}
            translation_cache[cache_key] = result
            translation_status[request_id] = {"status": "completed", "result": result}

            background_tasks.add_task(process_translation, request, request_id, cache_key)

            return {
                "id": request_id,
                "status": "completed",
                "result": result,
                "cache_hit": False
            }
        except Exception as e:
            # Fall back to background processing if immediate translation fails
            translation_status[request_id] = {"status": "processing"}
            background_tasks.add_task(process_translation, request, request_id, cache_key)
            return {"id": request_id, "status": "processing"}

    except Exception as e:
        translation_status[request_id] = {"status": "error", "error": str(e)}
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": time.time()}

async def process_translation(request: TranslationRequest, request_id: str, cache_key: str):
    try:
        prompt = f">>ara<< {request.text}"
        translation = generate_translation(prompt)
        result = {"translation": translation}
        translation_cache[cache_key] = result

        if hasattr(app.state, 'kafka_producer'):
            await send_to_kafka(
                app.state.kafka_producer,
                {
                    'user_id': request.user_id,
                    'chat_id': request.chat_id,
                    'text': request.text,
                    'translation': result["translation"],
                }
            )
        translation_status[request_id] = {"status": "completed", "result": result}
    except Exception as e:
        translation_status[request_id] = {"status": "error", "error": str(e)}

@app.on_event("startup")
async def startup_event():
    logger.info("Starting Kafka consumers...")

    consumer = await init_kafka_consumer()
    producer = app.state.kafka_producer

    if consumer and producer:
        consumer_thread = Thread(target=consume_and_process_kafka_messages, args=(consumer, producer), daemon=True)
        consumer_thread.start()
    else:
        logger.error("Failed to start Kafka consumers due to initialization errors.")

app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn
    import multiprocessing

    uvicorn.run(
        "app:app",  # Use module:app format
        host="0.0.0.0",
        port=8000,
        reload=False,  # Disable reload in production
        workers=multiprocessing.cpu_count(),
        log_level="info"
    )

def handle_translation_or_summarization(data, topic):
    try:
        type_map = {
            'e2a-translation-response': 'e2a-translation',
            'a2e-translation-response': 'a2e-translation',
            'summarization-response': 'summarization'
        }

        message_type = type_map.get(topic)
        if not message_type:
            logger.error(f"Unknown topic: {topic}")
            return

        user_id = data.get('user_id')
        chat_id = data.get('chat_id')
        in_text = data.get('in_text')
        out_text = data.get('out_text')
        formality = data.get('formality') if topic == 'summarization-response' else None

        if not all([user_id, chat_id, in_text, out_text]):
            logger.error(f"Missing required fields in message: {data}")
            return

        max_retries = 3
        retry_count = 0
        while retry_count < max_retries:
            try:
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            'INSERT INTO chat (user_id, chat_id, in_text, out_text, type, formality, timestamp) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)',
                            (user_id, chat_id, in_text, out_text, message_type, formality)
                        )
                        conn.commit()
                logger.info(f"Successfully stored message for topic {topic}: {data}")
                break
            except Exception as e:
                retry_count += 1
                if retry_count == max_retries:
                    logger.error(f"Failed to store message after {max_retries} attempts for topic {topic}: {e}")
                    raise
                logger.warning(f"Retry {retry_count}/{max_retries} for topic {topic}: {e}")
                time.sleep(1)  # Wait before retrying
    except Exception as e:
        logger.error(f"Error processing message for topic {topic}: {e}")
        # Consider implementing a dead letter queue here
