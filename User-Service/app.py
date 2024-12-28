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
from supertokens_python import init, InputAppInfo, SupertokensConfig
from supertokens_python.recipe import session, emailpassword
from supertokens_python.recipe.session.framework.flask import verify_session
from supertokens_python.recipe.emailpassword import EmailPasswordRecipe
import psycopg2
from psycopg2.extras import RealDictCursor
import uvicorn 

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
KAFKA_LOGIN_TOPIC = os.getenv('KAFKA_LOGIN_TOPIC', 'login')
KAFKA_SIGNUP_TOPIC = os.getenv('KAFKA_SIGNUP_TOPIC', 'signup')
KAFKA_E2A_RESPONSE_TOPIC = os.getenv('KAFKA_E2A_RESPONSE_TOPIC', 'e2a-translator-response')
KAFKA_A2E_RESPONSE_TOPIC = os.getenv('KAFKA_A2E_RESPONSE_TOPIC', 'a2e-translator-response')
KAFKA_SUMMARIZATION_RESPONSE_TOPIC = os.getenv('KAFKA_SUMMARIZATION_RESPONSE_TOPIC', 'summarization-response')

KAFKA_LOGIN_RESPONSE_TOPIC = os.getenv('KAFKA_LOGIN_RESPONSE_TOPIC', 'login-response')
KAFKA_SIGNUP_RESPONSE_TOPIC = os.getenv('KAFKA_SIGNUP_RESPONSE_TOPIC', 'signup-response')
KAFKA_E2A_TOPIC = os.getenv('KAFKA_E2A_TOPIC', 'e2a-translator')
KAFKA_A2E_TOPIC = os.getenv('KAFKA_A2E_TOPIC', 'a2e-translator')
KAFKA_SUMMARIZATION_TOPIC = os.getenv('KAFKA_SUMMARIZATION_TOPIC', 'summarization')

response_topics = [
    KAFKA_LOGIN_TOPIC,
    KAFKA_SIGNUP_TOPIC,
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

def get_db_connection():
    return psycopg2.connect(os.getenv(DATABASE_URL), cursor_factory=RealDictCursor)

init(
    app_info=InputAppInfo(
        app_name="Cloud Project",
        api_domain="http://kafka:8000",
        website_domain="http://frontend:4200",
    ),
    supertokens_config=SupertokensConfig(
        connection_uri="http://supertokens:3567",
        api_key=os.getenv("SUPERTOKENS_API_KEY",'default')
    ),
    framework="flask",
    recipe_list=[
        emailpassword.init(),
        session.init(),
    ],
)


# Initialize producer
producer = Producer(producer_conf)

# Pydantic models
class LoginRequest(BaseModel):
    correlation_id: str
    email: EmailStr
    password: str

class SignupRequest(BaseModel):
    correlation_id: str
    email: EmailStr
    password: str
    name: str

class E2ATranslationRequest(BaseModel):
    correlation_id: str
    user_id: str
    chat_id: str
    input_text: str
    output_text: str

class A2ETranslationRequest(BaseModel):
    correlation_id: str
    user_id: str
    chat_id: str
    input_text: str
    output_text: str

class SummarizationRequest(BaseModel):
    correlation_id: str
    user_id: str
    chat_id: str
    input_text: str
    output_text: str
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
    message_dict = json.loads(message_value.decode('utf-8')) if isinstance(message_value, bytes) else message_value
    form_fields = message_dict.get('formFields', {})
    response = {}
        
    if topic == KAFKA_LOGIN_TOPIC:
            try:
                email = form_fields['email']
                password = form_fields['password']

                user = emailpassword.EmailPasswordRecipe.get_instance().sign_in(email, password)
                response = {"status": "success", "userId": user.user_id, "correlation_id": message_dict.get('correlation_id')}
            except Exception as e:
                response = {"status": "error", "message": str(e)}

                producer.produce(
                KAFKA_LOGIN_RESPONSE_TOPIC,
                key=None,
                value=json.dumps(response),
                callback=delivery_callback
            )
            producer.flush()
            
    elif topic == KAFKA_SIGNUP_TOPIC:
            try:
                email = form_fields['email']
                password = form_fields['password']

                emailpassword.EmailPasswordRecipe.get_instance().sign_up(email, password)
                user = emailpassword.EmailPasswordRecipe.get_instance().sign_in(email, password)

                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            'INSERT INTO user_profiles (user_id, email) VALUES (%s, %s)',
                            (user.user_id, email)
                        )
                        conn.commit()

                response = {"status": "success", "userId": user.user_id, "correlation_id": message_dict.get('correlation_id')}
            except Exception as e:
                response = {"status": "error", "message": str(e)}

            producer.produce(
                KAFKA_SIGNUP_RESPONSE_TOPIC,
                key=None,
                value=json.dumps(response),
                callback=delivery_callback
            )
            producer.flush()
            
    elif topic in [KAFKA_E2A_RESPONSE_TOPIC, KAFKA_A2E_RESPONSE_TOPIC, KAFKA_SUMMARIZATION_RESPONSE_TOPIC]:
            type_map = {
            'e2a-translation-response': 'e2a-translation',
            'a2e-translation-response': 'a2e-translation',
            'summarization-response': 'summarization'
            }

            message_type = type_map.get(topic)
            user_id = message_value.get('user_id')
            chat_id = message_value.get('chat_id')
            in_text = message_value.get('in_text')
            out_text = message_value.get('out_text')
            formality = message_value.get('formality') if topic == 'summarization-response' else None

            try:
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            'INSERT INTO chat (user_id, chat_id, in_text, out_text, type, formality, timestamp) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)',
                            (user_id, chat_id, in_text, out_text, message_type, formality)
                        )
                        conn.commit()
                print(f"Stored message for topic {topic}: {message_value}")
            except Exception as e:
                print(f"Failed to store message for topic {topic}: {e}")

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
                handle_kafka_response(msg.topic(), msg.value())
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

# @app.post("/login")
# async def login(login_request: LoginRequest):
#     try:
#         correlation_id = str(uuid.uuid4())
#         login_data = login_request.dict()
#         login_data['correlation_id'] = correlation_id
#         producer.produce(
#             KAFKA_LOGIN_TOPIC,
#             json.dumps(login_data).encode('utf-8'),
#             callback=delivery_callback
#         )
#         producer.flush()
#         return {"status": "success", "correlation_id": correlation_id}
#     except Exception as e:
#         logger.error(f"Login error: {e}")
#         return {"status": "error", "message": str(e)}

# @app.post("/signup")
# async def signup(signup_request: SignupRequest):
#     try:
#         correlation_id = str(uuid.uuid4())
#         signup_data = signup_request.dict()
#         signup_data['correlation_id'] = correlation_id
#         producer.produce(
#             KAFKA_SIGNUP_TOPIC,
#             json.dumps(signup_data).encode('utf-8'),
#             callback=delivery_callback
#         )
#         producer.flush()
#         return {"status": "success", "correlation_id": correlation_id}
#     except Exception as e:
#         logger.error(f"Signup error: {e}")
#         return {"status": "error", "message": str(e)}

# @app.post("/e2a")
# async def e2a_translation(e2a_request: E2ATranslationRequest):
#     try:
#         correlation_id = str(uuid.uuid4())
#         e2a_data = e2a_request.dict()
#         e2a_data['correlation_id'] = correlation_id
#         producer.produce(
#             KAFKA_E2A_TOPIC,
#             json.dumps(e2a_data).encode('utf-8'),
#             callback=delivery_callback
#         )
#         producer.flush()
#         return {"status": "success", "correlation_id": correlation_id}
#     except Exception as e:
#         logger.error(f"E2A translation error: {e}")
#         return {"status": "error", "message": str(e)}

# @app.post("/a2e")
# async def a2e_translation(a2e_request: A2ETranslationRequest):
#     try:
#         correlation_id = str(uuid.uuid4())
#         a2e_data = a2e_request.dict()
#         a2e_data['correlation_id'] = correlation_id
#         producer.produce(
#             KAFKA_A2E_TOPIC,
#             json.dumps(a2e_data).encode('utf-8'),
#             callback=delivery_callback
#         )
#         producer.flush()
#         return {"status": "success", "correlation_id": correlation_id}
#     except Exception as e:
#         logger.error(f"A2E translation error: {e}")
#         return {"status": "error", "message": str(e)}

# @app.post("/summarization")
# async def summarization(summarization_request: SummarizationRequest):
#     try:
#         correlation_id = str(uuid.uuid4())
#         summarization_data = summarization_request.dict()
#         summarization_data['correlation_id'] = correlation_id
#         producer.produce(
#             KAFKA_SUMMARIZATION_TOPIC,
#             json.dumps(summarization_data).encode('utf-8'),
#             callback=delivery_callback
#         )
#         producer.flush()
#         return {"status": "success", "correlation_id": correlation_id}
#     except Exception as e:
#         logger.error(f"Summarization error: {e}")
#         return {"status": "error", "message": str(e)}

# Start consumer in a separate thread when the server starts
@app.on_event("startup")
async def startup():
    global consumer_thread
    consumer_thread = threading.Thread(target=consume_messages)
    consumer_thread.start()

# Stop consumer gracefully when the server stops
@app.on_event("shutdown")
async def shutdown():
    global running
    running = False
    consumer_thread.join()
    logger.info("Server shutdown complete")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)