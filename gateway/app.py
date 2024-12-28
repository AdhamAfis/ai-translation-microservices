from fastapi import FastAPI
from pydantic import BaseModel, EmailStr
from confluent_kafka import Producer, Consumer, KafkaError
from dotenv import load_dotenv
import json
import os
import threading
import requests
import uuid
import time

load_dotenv()

app = FastAPI(title="API Gateway")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": time.time()}
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

producer_conf = {'bootstrap.servers': KAFKA_BOOTSTRAP}
producer = Producer(producer_conf)

def delivery_callback(err, msg):
    if err:
        print(f'Message delivery failed: {err}')
    else:
        print(f'Message delivered to {msg.topic()} [{msg.partition()}] at offset {msg.offset()}')

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
    type: str = None  # Optional filter by type (e2a, a2e, summarization)

@app.post("/login")
async def login(login_request: LoginRequest):
    try:
        correlation_id = str(uuid.uuid4())
        login_data = login_request.model_dump()
        login_data['correlation_id'] = correlation_id
        login_request_json = json.dumps(login_data)

        producer.produce(KAFKA_LOGIN_TOPIC, login_request_json.encode('utf-8'), callback=delivery_callback)
        producer.flush()

        return {"status": "success", "message": "Login request sent", "correlation_id": correlation_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/signup")
async def signup(signup_request: SignupRequest):
    try:
        correlation_id = str(uuid.uuid4())
        signup_data = signup_request.model_dump()
        signup_data['correlation_id'] = correlation_id
        signup_request_json = json.dumps(signup_data)

        producer.produce(KAFKA_SIGNUP_TOPIC, signup_request_json.encode('utf-8'), callback=delivery_callback)
        producer.flush()

        return {"status": "success", "message": "Signup request sent", "correlation_id": correlation_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/e2a")
async def e2a_translation(e2a_request: E2ATranslationRequest):
    try:
        correlation_id = str(uuid.uuid4())
        e2a_data = e2a_request.model_dump()
        e2a_data['correlation_id'] = correlation_id
        e2a_request_json = json.dumps(e2a_data)

        producer.produce(KAFKA_E2A_TOPIC, e2a_request_json.encode('utf-8'), callback=delivery_callback)
        producer.flush()

        return {"status": "success", "message": "Translation request sent", "correlation_id": correlation_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/a2e")
async def a2e_translation(a2e_request: A2ETranslationRequest):
    try:
        correlation_id = str(uuid.uuid4())
        a2e_data = a2e_request.model_dump()
        a2e_data['correlation_id'] = correlation_id
        a2e_request_json = json.dumps(a2e_data)

        producer.produce(KAFKA_A2E_TOPIC, a2e_request_json.encode('utf-8'), callback=delivery_callback)
        producer.flush()

        return {"status": "success", "message": "Translation request sent", "correlation_id": correlation_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/summarization")
async def summarization(summarization_request: SummarizationRequest):
    try:
        correlation_id = str(uuid.uuid4())
        summarization_data = summarization_request.model_dump()
        summarization_data['correlation_id'] = correlation_id
        summarization_request_json = json.dumps(summarization_data)

        producer.produce(KAFKA_SUMMARIZATION_TOPIC, summarization_request_json.encode('utf-8'), callback=delivery_callback)
        producer.flush()

        return {"status": "success", "message": "Summarization request sent", "correlation_id": correlation_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    


def consume_responses():
    def run_consumer():
        # Initialize the consumer inside the thread
        consumer_conf = {
            'bootstrap.servers': KAFKA_BOOTSTRAP,
            'group.id': 'gateway_consumers',
            'auto.offset.reset': 'earliest'
        }
        consumer = Consumer(consumer_conf)
        consumer.subscribe(response_topics)
        try:
            print("Subscribed to respone topics")
            while True:
                msg = consumer.poll(1.0)  # Timeout in seconds
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        # End of partition event
                        continue
                    else:
                        # Handle other errors
                        print(f"Consumer error: {msg.error()}")
                        continue

                # Dispatch message to appropriate handler based on topic
                topic = msg.topic()
                message_value = msg.value().decode('utf-8')

                if topic == KAFKA_LOGIN_RESPONSE_TOPIC:
                    handle_login_response(message_value)
                elif topic == KAFKA_SIGNUP_RESPONSE_TOPIC:
                    handle_signup_response(message_value)
                elif topic == KAFKA_E2A_RESPONSE_TOPIC:
                    handle_e2a_translation_response(message_value)
                elif topic == KAFKA_A2E_RESPONSE_TOPIC:
                    handle_a2e_translation_response(message_value)
                elif topic == KAFKA_SUMMARIZATION_RESPONSE_TOPIC:
                    handle_summarization_response(message_value)
                else:
                    print(f"Received message from unknown topic {topic}: {message_value}")

        except Exception as e:
            print(f"Error in consumer: {e}")
        finally:
            consumer.close()

    threading.Thread(target=run_consumer, daemon=True).start()

# Login Handling

def handle_login_response(message_value):
    # Parse the message
    response = json.loads(message_value)
    correlation_id = response.get('correlation_id')

    if not correlation_id:
        print("Correlation ID missing in login response")
        return

    # Send the response as JSON to http://os.getenv('FRONTEND_ROUTE')/exampleemail@xyz/login
    url = f"http://{os.getenv('FRONTEND_ROUTE')}/{response.get('email')}/login"
    print("attempting to send login response to frontend")
    try:
        r = requests.post(url, json=response)
        r.raise_for_status()
        print(f"Login response forwarded to frontend: {r.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to forward login response to frontend: {e}")

# Signup Handling

def handle_signup_response(message_value):
    # Parse the message
    response = json.loads(message_value)
    correlation_id = response.get('correlation_id')
    if not correlation_id:
        print("Correlation ID missing in signup response")
        return

    # Send the response as JSON to  http://os.getenv('FRONTEND_ROUTE')/exampleemail@xyz/signup
    url = f"http://{os.getenv('FRONTEND_ROUTE')}/{response.get('email')}/signup"
    try:
        r = requests.post(url, json=response)
        r.raise_for_status()
        print(f"Signup response forwarded to frontend: {r.status_code}")

    except requests.exceptions.RequestException as e:

        print(f"Failed to forward signup response to frontend: {e}")

#E2A Translation Handling

def handle_e2a_translation_response(message_value):
    # Parse the message
    response = json.loads(message_value)
    user_id = response.get('user_id')
    chat_id = response.get('chat_id')
    input_text = response.get('input_text')
    output_text = response.get('output_text')

    if not user_id:
        print("user_id missing in E2A translation response")
        return

    # Construct the URL
    url = f"http://{os.getenv('FRONTEND_ROUTE')}/{user_id}/e2a"

    # Prepare the payload
    payload = {
        'chat_id': chat_id,
        'input_text': input_text,
        'output_text': output_text
    }

    # Send the output_text to the frontend
    try:
        r = requests.post(url, json=payload)
        r.raise_for_status()
        print(f"E2A translation response forwarded to frontend: {r.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to forward E2A translation response to frontend: {e}")

# A2E Translation Handling

def handle_a2e_translation_response(message_value):
    # Parse the message
    response = json.loads(message_value)
    user_id = response.get('user_id')
    chat_id = response.get('chat_id')
    input_text = response.get('input_text')
    output_text = response.get('output_text')

    if not user_id:
        print("user_id missing in A2E translation response")
        return

    # Construct the URL
    url = f"http://{os.getenv('FRONTEND_ROUTE')}/{user_id}/a2e"

    # Prepare the payload
    payload = {
        'chat_id': chat_id,
        'input_text': input_text,
        'output_text': output_text
    }

    # Send the output_text to the frontend
    try:
        r = requests.post(url, json=payload)
        r.raise_for_status()
        print(f"A2E translation response forwarded to frontend: {r.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to forward A2E translation response to frontend: {e}")

#Summarization Handling

def handle_summarization_response(message_value):
    # Parse the message
    response = json.loads(message_value)
    user_id = response.get('user_id')
    chat_id = response.get('chat_id')
    input_text = response.get('input_text')
    output_text = response.get('output_text')
    formality = response.get('formality')

    if not user_id:
        print("user_id missing in summarization response")
        return

    # Construct the URL
    url = f"http://{os.getenv('FRONTEND_ROUTE')}/{user_id}/summarization"

    # Prepare the payload
    payload = {
        'chat_id': chat_id,
        'input_text': input_text,
        'output_text': output_text,
        'formality': formality
    }

    # Send the output_text and formality to the frontend
    try:
        r = requests.post(url, json=payload)
        r.raise_for_status()
        print(f"Summarization response forwarded to frontend: {r.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"Failed to forward summarization response to frontend: {e}")

@app.get("/history/{user_id}")
async def get_history(user_id: int, type: str = None):
    try:
        # Connect to user service to fetch history
        url = f"{os.getenv('USER_SERVICE_URL', 'http://user-service:3001')}/history/{user_id}"
        if type:
            url += f"?type={type}"
            
        response = requests.get(url)
        if response.status_code != 200:
            return {"status": "error", "message": "Failed to fetch history"}
            
        history_data = response.json()
        return {"status": "success", "data": history_data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.on_event("startup")
def startup_event():
    consume_responses()

@app.on_event("shutdown")
def shutdown_event():
    producer.flush()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
