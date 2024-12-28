from fastapi import FastAPI
from pydantic import BaseModel, EmailStr
from confluent_kafka import Producer, Consumer, KafkaError
from dotenv import load_dotenv
import json
import os
import requests
import uuid
import time
import threading
import logging
import asyncio

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI(title="API Gateway")

# Global variables
running = True
consumer_thread = None

# Kafka configuration
KAFKA_BOOTSTRAP = os.getenv('KAFKA_BOOTSTRAP')

# Topic names from environment variables
KAFKA_LOGIN_TOPIC = os.getenv('KAFKA_LOGIN_TOPIC')
KAFKA_SIGNUP_TOPIC = os.getenv('KAFKA_SIGNUP_TOPIC')
KAFKA_E2A_TOPIC = os.getenv('KAFKA_E2A_TOPIC')
KAFKA_A2E_TOPIC = os.getenv('KAFKA_A2E_TOPIC')
KAFKA_SUMMARIZATION_TOPIC = os.getenv('KAFKA_SUMMARIZATION_TOPIC')
KAFKA_LOGIN_RESPONSE_TOPIC = os.getenv('KAFKA_LOGIN_RESPONSE_TOPIC')
KAFKA_SIGNUP_RESPONSE_TOPIC = os.getenv('KAFKA_SIGNUP_RESPONSE_TOPIC')
KAFKA_E2A_RESPONSE_TOPIC = os.getenv('KAFKA_E2A_RESPONSE_TOPIC')
KAFKA_A2E_RESPONSE_TOPIC = os.getenv('KAFKA_A2E_RESPONSE_TOPIC')
KAFKA_SUMMARIZATION_RESPONSE_TOPIC = os.getenv('KAFKA_SUMMARIZATION_RESPONSE_TOPIC')

response_topics = [
    KAFKA_LOGIN_RESPONSE_TOPIC,
    KAFKA_SIGNUP_RESPONSE_TOPIC,
    KAFKA_E2A_RESPONSE_TOPIC,
    KAFKA_A2E_RESPONSE_TOPIC,
    KAFKA_SUMMARIZATION_RESPONSE_TOPIC
]

# Kafka configurations
producer_conf = {
    'bootstrap.servers': KAFKA_BOOTSTRAP,
    'client.id': 'gateway_producer'
}

consumer_conf = {
    'bootstrap.servers': KAFKA_BOOTSTRAP,
    'group.id': 'gateway_consumers',
    'auto.offset.reset': 'earliest',
    'enable.auto.commit': True,
    'max.poll.interval.ms': 300000,
    'session.timeout.ms': 30000,
    'heartbeat.interval.ms': 10000
}

# Initialize producer
producer = Producer(producer_conf)

# Pydantic models
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str

class E2ATranslationRequest(BaseModel):
    user_id: int
    input_text: str

class A2ETranslationRequest(BaseModel):
    user_id: int
    input_text: str

class SummarizationRequest(BaseModel):
    user_id: int
    input_text: str
    formality: str

class HistoryRequest(BaseModel):
    user_id: int
    type: str = None

def delivery_callback(err, msg):
    if err:
        logger.error(f'Message delivery failed: {err}')
    else:
        logger.info(f'Message delivered to {msg.topic()} [{msg.partition()}] at offset {msg.offset()}')

def handle_kafka_response(topic, message_value):
    try:
        response = json.loads(message_value)
        
        if topic == KAFKA_LOGIN_RESPONSE_TOPIC:
            url = f"http://{os.getenv('FRONTEND_ROUTE')}/{response.get('email')}/login"
            requests.post(url, json=response).raise_for_status()
            logger.info("Login response forwarded to frontend")
            
        elif topic == KAFKA_SIGNUP_RESPONSE_TOPIC:
            url = f"http://{os.getenv('FRONTEND_ROUTE')}/{response.get('email')}/signup"
            requests.post(url, json=response).raise_for_status()
            logger.info("Signup response forwarded to frontend")
            
        elif topic in [KAFKA_E2A_RESPONSE_TOPIC, KAFKA_A2E_RESPONSE_TOPIC]:
            service_type = "e2a" if topic == KAFKA_E2A_RESPONSE_TOPIC else "a2e"
            url = f"http://{os.getenv('FRONTEND_ROUTE')}/{response.get('user_id')}/{service_type}"
            payload = {
                'chat_id': response.get('chat_id'),
                'input_text': response.get('input_text'),
                'output_text': response.get('output_text')
            }
            requests.post(url, json=payload).raise_for_status()
            logger.info(f"{service_type.upper()} translation response forwarded to frontend")
            
        elif topic == KAFKA_SUMMARIZATION_RESPONSE_TOPIC:
            url = f"http://{os.getenv('FRONTEND_ROUTE')}/{response.get('user_id')}/summarization"
            payload = {
                'chat_id': response.get('chat_id'),
                'input_text': response.get('input_text'),
                'output_text': response.get('output_text'),
                'formality': response.get('formality')
            }
            requests.post(url, json=payload).raise_for_status()
            logger.info("Summarization response forwarded to frontend")
            
    except Exception as e:
        logger.error(f"Error handling Kafka response: {e}")

def consume_messages():
    global running
    logger.info("Starting Kafka consumer")
    
    consumer = Consumer(consumer_conf)
    consumer.subscribe(response_topics)
    logger.info(f"Subscribed to topics: {response_topics}")
    
    try:
        while running:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error(f"Consumer error: {msg.error()}")
                continue
            
            try:
                message_value = msg.value().decode('utf-8')
                logger.info(f"Received message from topic: {msg.topic()}")
                handle_kafka_response(msg.topic(), message_value)
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                
    except Exception as e:
        logger.error(f"Fatal consumer error: {e}")
    finally:
        consumer.close()
        logger.info("Consumer closed")

# FastAPI endpoints
@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": time.time()}

@app.post("/login")
async def login(login_request: LoginRequest):
    try:
        correlation_id = str(uuid.uuid4())
        login_data = login_request.dict()
        login_data['correlation_id'] = correlation_id
        producer.produce(
            KAFKA_LOGIN_TOPIC,
            json.dumps(login_data).encode('utf-8'),
            callback=delivery_callback
        )
        producer.flush()
        return {"status": "success", "correlation_id": correlation_id}
    except Exception as e:
        logger.error(f"Login error: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/signup")
async def signup(signup_request: SignupRequest):
    try:
        correlation_id = str(uuid.uuid4())
        signup_data = signup_request.dict()
        signup_data['correlation_id'] = correlation_id
        producer.produce(
            KAFKA_SIGNUP_TOPIC,
            json.dumps(signup_data).encode('utf-8'),
            callback=delivery_callback
        )
        producer.flush()
        return {"status": "success", "correlation_id": correlation_id}
    except Exception as e:
        logger.error(f"Signup error: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/e2a")
async def e2a_translation(e2a_request: E2ATranslationRequest):
    try:
        correlation_id = str(uuid.uuid4())
        e2a_data = e2a_request.dict()
        e2a_data['correlation_id'] = correlation_id
        producer.produce(
            KAFKA_E2A_TOPIC,
            json.dumps(e2a_data).encode('utf-8'),
            callback=delivery_callback
        )
        producer.flush()
        return {"status": "success", "correlation_id": correlation_id}
    except Exception as e:
        logger.error(f"E2A translation error: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/a2e")
async def a2e_translation(a2e_request: A2ETranslationRequest):
    try:
        correlation_id = str(uuid.uuid4())
        a2e_data = a2e_request.dict()
        a2e_data['correlation_id'] = correlation_id
        producer.produce(
            KAFKA_A2E_TOPIC,
            json.dumps(a2e_data).encode('utf-8'),
            callback=delivery_callback
        )
        producer.flush()
        return {"status": "success", "correlation_id": correlation_id}
    except Exception as e:
        logger.error(f"A2E translation error: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/summarization")
async def summarization(summarization_request: SummarizationRequest):
    try:
        correlation_id = str(uuid.uuid4())
        summarization_data = summarization_request.dict()
        summarization_data['correlation_id'] = correlation_id
        producer.produce(
            KAFKA_SUMMARIZATION_TOPIC,
            json.dumps(summarization_data).encode('utf-8'),
            callback=delivery_callback
        )
        producer.flush()
        return {"status": "success", "correlation_id": correlation_id}
    except Exception as e:
        logger.error(f"Summarization error: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/history/{user_id}")
async def get_history(user_id: int, type: str = None):
    try:
        url = f"{os.getenv('USER_SERVICE_URL', 'http://user-service:3001')}/history/{user_id}"
        if type:
            url += f"?type={type}"
        response = requests.get(url)
        response.raise_for_status()
        return {"status": "success", "data": response.json()}
    except Exception as e:
        logger.error(f"History fetch error: {e}")
        return {"status": "error", "message": str(e)}

@app.on_event("startup")
async def startup_event():
    global consumer_thread
    consumer_thread = threading.Thread(target=consume_messages, daemon=True)
    consumer_thread.start()
    logger.info("Kafka consumer thread started")

@app.on_event("shutdown")
async def shutdown_event():
    global running, consumer_thread
    running = False
    if consumer_thread:
        consumer_thread.join(timeout=5)
    producer.flush()
    logger.info("Application shutdown complete")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)