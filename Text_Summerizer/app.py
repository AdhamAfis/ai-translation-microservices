from fastapi import FastAPI, HTTPException, BackgroundTasks, APIRouter, Request
from fastapi.middleware.gzip import GZipMiddleware
from cachetools import TTLCache
from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import KafkaError
from pydantic import BaseModel
from dotenv import load_dotenv
import hashlib
import ollama
import logging
import asyncio
import json
import os
from contextlib import asynccontextmanager
from shared.base_service import BaseService
from circuitbreaker import circuit
import uuid

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.kafka_producer = await init_kafka_producer()
    app.state.kafka_consumer = await init_kafka_consumer()
    logger.info("Kafka producer and consumer initialized")
    yield
    # Shutdown
    if hasattr(app.state, 'kafka_producer'):
        app.state.kafka_producer.close()
        logger.info("Kafka producer closed")
    if hasattr(app.state, 'kafka_consumer'):
        app.state.kafka_consumer.close()
        logger.info("Kafka consumer closed")

service = BaseService("text_summarizer")
app = service.app
app.lifespan = lifespan  # Set lifespan for the service app
app.add_middleware(GZipMiddleware, minimum_size=1000)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cache configuration
summary_cache = TTLCache(maxsize=100, ttl=3600)

class SummaryRequest(BaseModel):
    correlation_id: str
    user_id: str
    chat_id: str
    text: str
    style: str = "formal"
    max_length: int = 500
    bullet_points: bool = False

class SummaryStatus(BaseModel):
    id: str
    status: str
    result: dict = None
    error: str = None

summary_status = {}

STYLE_PROMPTS = {
    "formal": """I will now provide a formal academic summary:

Provide a formal academic summary that:
- Uses scholarly language and precise terminology
- Maintains objective, third-person perspective
- Emphasizes key research findings and methodologies
- Structures information in a logical, systematic manner
- Avoids colloquialisms and informal expressions""",

    "informal": """I will now provide a conversational summary:

Create a conversational summary that:
- Uses everyday language and natural expressions
- Explains concepts as if talking to a friend
- Includes relatable examples where appropriate
- Breaks down complex ideas into simple terms
- Maintains a friendly, approachable tone""",

    "technical": """I will now provide a technical summary:

Generate a technical summary that:
- Focuses on specifications, metrics, and technical details
- Preserves essential technical terminology
- Outlines system architectures or methodologies
- Includes relevant technical parameters and measurements
- Maintains precision in technical descriptions""",

    "executive": """I will now provide an executive summary:

Provide an executive summary that:
- Leads with key business implications and ROI
- Highlights strategic insights and recommendations
- Focuses on actionable conclusions
- Quantifies impacts and outcomes
- Structures information in order of business priority""",

    "creative": """I will now provide a creative narrative summary:

Craft an engaging narrative summary that:
- Uses vivid analogies and metaphors
- Incorporates storytelling elements
- Creates memorable visual descriptions
- Makes complex ideas relatable through creative comparisons
- Maintains reader engagement through varied language"""
}

@circuit(failure_threshold=5, recovery_timeout=60)
def generate_completion(prompt: str) -> str:
    with service.request_latency.time():
        try:
            client = ollama.Client(host=os.getenv('OLLAMA_API_URL', 'http://ollama:11434'))
            response = client.generate(
                model=os.getenv('MODEL_NAME', 'llama3.1:3b'),
                prompt=prompt,
                options={
                    'temperature': 0.7,
                    'top_p': 0.9,
                    'num_ctx': 4096,
                    'num_predict': 2048,
                    'num_gpu': int(os.getenv('NUM_GPU', 1)),
                    'num_thread': int(os.getenv('NUM_THREAD', 4))
                }
            )
            return response['response']
        except Exception as e:
            logger.error(f"Circuit breaker: {str(e)}")
            raise

async def init_kafka_producer():
    try:
        return KafkaProducer(
            bootstrap_servers=os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092'),
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            acks='all',
            retries=3
        )
    except KafkaError as e:
        logger.error(f"Kafka initialization error: {e}")
        return None

async def init_kafka_consumer():
    try:
        consumer = KafkaConsumer(
            'summarizer_requests',  # Consumer listens to this topic
            bootstrap_servers=os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092'),
            group_id='summarizer-group',
            value_deserializer=lambda v: json.loads(v.decode('utf-8')),
            auto_offset_reset='earliest'
        )
        asyncio.create_task(consume_summarization_requests(consumer))
        return consumer
    except KafkaError as e:
        logger.error(f"Kafka initialization error: {e}")
        return None

async def consume_summarization_requests(consumer: KafkaConsumer):
    while True:
        try:
            message = consumer.poll(timeout=1.0)
            if message is None:
                continue
            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    logger.info(f"Reached end of partition: {message.partition()}")
                    continue
                else:
                    logger.error(f"Consumer error: {message.error()}")
                    # Implement backoff retry strategy
                    await asyncio.sleep(5)
                    continue

            try:
                request_data = message.value
                logger.info(f"Received request from Kafka: {request_data}")
                
                # Validate request data
                if not isinstance(request_data, dict):
                    logger.error(f"Invalid message format: {request_data}")
                    continue

                request = SummaryRequest(**request_data)
                request_id = str(uuid.uuid4())
                cache_key = hashlib.md5(f"{request.text}{request.style}{request.max_length}{request.bullet_points}".encode()).hexdigest()
                
                # Process summary request with retry logic
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        style_prompt = STYLE_PROMPTS.get(request.style, STYLE_PROMPTS["formal"])
                        max_length_instruction = f"\nLimit the summary to approximately {request.max_length} characters."
                        bullet_points_instruction = "\nUse bullet points for main ideas." if request.bullet_points else ""
                        
                        prompt = f"""Based on the following style guide:
{style_prompt}

{max_length_instruction}
{bullet_points_instruction}

Text to summarize:
{request.text}

Provide a concise summary:"""
                        
                        summary = generate_completion(prompt)
                        
                        if len(summary) > request.max_length:
                            summary = summary[:request.max_length].rsplit(' ', 1)[0] + '...'
                        
                        result = {
                            "summary": summary,
                            "style": request.style,
                            "bullet_points": request.bullet_points,
                            "length": len(summary),
                            "truncated": len(summary) < len(generate_completion(prompt))
                        }
                        
                        summary_cache[cache_key] = result
                        summary_status[request_id] = {"status": "completed", "result": result}
                        
                        # Send the result to the response topic with retry
                        await send_to_kafka(
                            app.state.kafka_producer,
                            {
                                'correlation_id': request.correlation_id,
                                'user_id': request.user_id,
                                'chat_id': request.chat_id,
                                'input_text': request.text,
                                'output_text': result['summary'],
                                'status': 'completed',
                                'formality': request.style
                            },
                            'summarization-response'
                        )
                        break
                    except Exception as e:
                        if attempt == max_retries - 1:
                            logger.error(f"Failed to process message after {max_retries} attempts: {e}")
                            raise
                        logger.warning(f"Attempt {attempt + 1}/{max_retries} failed: {e}")
                        await asyncio.sleep(1)
                        
            except Exception as e:
                logger.error(f"Error processing Kafka message: {e}")
                # Consider implementing a dead letter queue here
                
        except Exception as e:
            logger.error(f"Critical consumer error: {e}")
            await asyncio.sleep(5)  # Wait before retrying

async def send_to_kafka(producer, message, topic):
    if not producer:
        return
    try:
        future = producer.send(topic, message)
        await asyncio.get_event_loop().run_in_executor(None, future.get, 60)
    except Exception as e:
        logger.error(f"Kafka send error: {e}")

api_router = APIRouter(prefix="/api/v1")

@api_router.post("/summarize")
async def summarize_text(request: SummaryRequest, background_tasks: BackgroundTasks):
    request_id = str(uuid.uuid4())
    try:
        service.request_count.inc()
        cache_key = hashlib.md5(f"{request.text}{request.style}{request.max_length}{request.bullet_points}".encode()).hexdigest()
        
        # Check cache first
        if cache_key in summary_cache:
            result = summary_cache[cache_key]
            summary_status[request_id] = {"status": "completed", "result": result}
            return {
                "id": request_id, 
                "status": "completed", 
                "result": result,
                "cache_hit": True
            }
        
        # Start processing immediately and return result if fast enough
        try:
            style_prompt = STYLE_PROMPTS.get(request.style, STYLE_PROMPTS["formal"])
            max_length_instruction = f"\nLimit the summary to approximately {request.max_length} characters."
            bullet_points_instruction = "\nUse bullet points for main ideas." if request.bullet_points else ""
            
            prompt = f"""Based on the following style guide:
{style_prompt}

{max_length_instruction}
{bullet_points_instruction}

Text to summarize:
{request.text}

Provide a concise summary:"""
            
            summary = generate_completion(prompt)
            
            # Trim summary if it exceeds max_length
            if len(summary) > request.max_length:
                summary = summary[:request.max_length].rsplit(' ', 1)[0] + '...'
            
            result = {
                "summary": summary,
                "style": request.style,
                "bullet_points": request.bullet_points,
                "length": len(summary),
                "truncated": len(summary) < len(generate_completion(prompt))
            }
            
            summary_cache[cache_key] = result
            summary_status[request_id] = {"status": "completed", "result": result}
            
            # Process in background for Kafka and other async operations
            background_tasks.add_task(process_summary, request, request_id, cache_key)
            
            return {
                "id": request_id,
                "status": "completed",
                "result": result,
                "cache_hit": False
            }
            
        except Exception as e:
            # If immediate processing fails, fall back to background processing
            summary_status[request_id] = {"status": "processing"}
            background_tasks.add_task(process_summary, request, request_id, cache_key)
            return {"id": request_id, "status": "processing"}
        
    except Exception as e:
        summary_status[request_id] = {"status": "error", "error": str(e)}
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/summarize/status/{request_id}")
async def get_summary_status(request: Request, request_id: str):  # Added Request parameter
    if request_id not in summary_status:
        raise HTTPException(status_code=404, detail="Summary request not found")
    
    return summary_status[request_id]

app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn
    import multiprocessing
    
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,  # Disable reload in production
        workers=multiprocessing.cpu_count(),
        log_level="info"
    )
