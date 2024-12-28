from flask import Flask, request, jsonify
from flask_cors import CORS
from supertokens_python import init, InputAppInfo, SupertokensConfig
from supertokens_python.recipe import session, emailpassword
from supertokens_python.recipe.session.framework.flask import verify_session
from supertokens_python.recipe.emailpassword import EmailPasswordRecipe
import psycopg2
from psycopg2.extras import RealDictCursor
from kafka import KafkaConsumer, KafkaProducer, TopicPartition
from dotenv import load_dotenv
import os
import time
import json
import logging
import threading
import uuid
from datetime import datetime
import atexit

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
CORS(app, supports_credentials=True, origins=[os.getenv('ALLOWED_ORIGINS')])

# Global variables for Kafka
producer = None
consumer = None
consumer_thread = None
running = True

def create_kafka_producer():
    """Create and return a Kafka producer with retries"""
    for _ in range(5):
        try:
            producer = KafkaProducer(
                bootstrap_servers=os.getenv('KAFKA_BOOTSTRAP'),
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                acks='all',
                retries=3,
            )
            logger.info("Kafka producer created successfully")
            return producer
        except Exception as e:
            logger.error(f"Failed to create Kafka producer: {e}")
            time.sleep(5)
    raise Exception("Failed to create Kafka producer after multiple attempts")

def create_kafka_consumer(topics):
    """Create and return a Kafka consumer with retries"""
    for _ in range(5):
        try:
            consumer = KafkaConsumer(
                *topics,  # Pass topics directly here
                bootstrap_servers=os.getenv('KAFKA_BOOTSTRAP'),
                value_deserializer=lambda v: json.loads(v.decode('utf-8')),
                group_id='gateway_consumers',  # Use the same group as gateway
                auto_offset_reset='earliest',
                enable_auto_commit=True,
                session_timeout_ms=30000,
                heartbeat_interval_ms=10000
            )
            logger.info(f"Kafka consumer created and subscribed to topics: {topics}")
            logger.info(f"Current subscription: {consumer.subscription()}")
            return consumer
        except Exception as e:
            logger.error(f"Failed to create Kafka consumer: {e}")
            time.sleep(5)
    raise Exception("Failed to create Kafka consumer after multiple attempts")

def get_db_connection():
    """Create and return a database connection"""
    try:
        return psycopg2.connect(os.getenv('DATABASE_URL'), cursor_factory=RealDictCursor)
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        raise

def initialize_supertokens():
    """Initialize SuperTokens"""
    init(
        app_info=InputAppInfo(
            app_name=os.getenv('SUPERTOKENS_APP_NAME'),
            api_domain=os.getenv('SUPERTOKENS_API_DOMAIN'),
            website_domain=os.getenv('SUPERTOKENS_WEBSITE_DOMAIN'),
        ),
        supertokens_config=SupertokensConfig(
            connection_uri=os.getenv('SUPERTOKENS_CONNECTION_URI'),
            api_key=os.getenv('SUPERTOKENS_API_KEY'),
        ),
        framework="flask",
        recipe_list=[
            emailpassword.init(),
            session.init(),
        ],
    )

def send_kafka_message(topic, message):
    """Send a message to Kafka with error handling"""
    try:
        future = producer.send(topic, message)
        future.get(timeout=10)
        logger.info(f"Message sent to topic {topic}")
    except Exception as e:
        logger.error(f"Failed to send message to topic {topic}: {e}")
        raise

def handle_login(data):
    """Handle login requests"""
    try:
        email = data.get('email')
        password = data.get('password')
        correlation_id = data.get('correlation_id')

        user = emailpassword.EmailPasswordRecipe.get_instance().sign_in(email, password)
        response = {
            "status": "success",
            "user_id": user.user_id,
            "email": email,
            "correlation_id": correlation_id
        }
    except Exception as e:
        logger.error(f"Login error: {e}")
        response = {
            "status": "error",
            "message": str(e),
            "correlation_id": correlation_id
        }

    send_kafka_message(os.getenv('KAFKA_LOGIN_RESPONSE_TOPIC'), response)

def handle_signup(data):
    """Handle signup requests"""
    try:
        email = data.get('email')
        password = data.get('password')
        name = data.get('name')
        correlation_id = data.get('correlation_id')

        user = emailpassword.EmailPasswordRecipe.get_instance().sign_up(email, password)
        
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'INSERT INTO user_profiles (user_id, email, name) VALUES (%s, %s, %s)',
                    (user.user_id, email, name)
                )
                conn.commit()

        response = {
            "status": "success",
            "user_id": user.user_id,
            "email": email,
            "correlation_id": correlation_id
        }
    except Exception as e:
        logger.error(f"Signup error: {e}")
        response = {
            "status": "error",
            "message": str(e),
            "correlation_id": correlation_id
        }

    send_kafka_message(os.getenv('KAFKA_SIGNUP_RESPONSE_TOPIC'), response)

def handle_translation_or_summarization(data, topic):
    """Handle translation and summarization requests"""
    try:
        user_id = data.get('user_id')
        input_text = data.get('input_text')
        output_text = data.get('output_text')
        chat_id = str(uuid.uuid4())
        formality = data.get('formality') if 'summarization' in topic else None
        
        message_type = topic.replace('-response', '')

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    '''INSERT INTO chat 
                    (user_id, chat_id, in_text, out_text, type, formality, timestamp) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING *''',
                    (user_id, chat_id, input_text, output_text, message_type, 
                     formality, datetime.now())
                )
                result = cur.fetchone()
                conn.commit()

        response = {
            "status": "success",
            "user_id": user_id,
            "chat_id": chat_id,
            "input_text": input_text,
            "output_text": output_text,
            "type": message_type
        }
        
        if formality:
            response["formality"] = formality

        response_topic = f"{message_type}-response"
        send_kafka_message(response_topic, response)

    except Exception as e:
        logger.error(f"Error handling {topic}: {e}")
        response = {
            "status": "error",
            "message": str(e),
            "user_id": data.get('user_id')
        }
        send_kafka_message(f"{message_type}-response", response)

def consume_messages():
    """Consume messages from Kafka topics"""
    global running, consumer
    
    topics = [
        os.getenv('KAFKA_LOGIN_TOPIC'),
        os.getenv('KAFKA_SIGNUP_TOPIC'),
        os.getenv('KAFKA_E2A_TOPIC'),
        os.getenv('KAFKA_A2E_TOPIC'),
        os.getenv('KAFKA_SUMMARIZATION_TOPIC')
    ]
    
    logger.info(f"Starting Kafka consumer with topics: {topics}")
    
    try:
        consumer = create_kafka_consumer(topics)
        
        while running:
            try:
                messages = consumer.poll(timeout_ms=1000)
                if not messages:
                    continue
                    
                for tp, msgs in messages.items():
                    for message in msgs:
                        logger.info(f"Received message from topic: {message.topic}")
                        logger.info(f"Message value: {message.value}")
                        
                        if message.topic == os.getenv('KAFKA_LOGIN_TOPIC'):
                            handle_login(message.value)
                        elif message.topic == os.getenv('KAFKA_SIGNUP_TOPIC'):
                            handle_signup(message.value)
                        else:
                            handle_translation_or_summarization(message.value, message.topic)
                            
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                time.sleep(1)
                
    except Exception as e:
        logger.error(f"Fatal consumer error: {e}")
    finally:
        if consumer:
            consumer.close()
            logger.info("Consumer closed")

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

@app.route('/debug/consumer-status')
def consumer_status():
    if consumer:
        return jsonify({
            "status": "active",
            "subscription": list(consumer.subscription()),
            "assignment": [(tp.topic, tp.partition) for tp in consumer.assignment()],
            "group_id": consumer.config['group_id']
        })
    return jsonify({
        "status": "inactive",
        "error": "Consumer not initialized"
    })

@app.route('/debug/restart-consumer')
def restart_consumer():
    global consumer, consumer_thread, running
    try:
        # Stop existing consumer
        running = False
        if consumer_thread:
            consumer_thread.join(timeout=5)
        if consumer:
            consumer.close()
            
        # Reset and restart
        running = True
        consumer_thread = threading.Thread(target=consume_messages, daemon=True)
        consumer_thread.start()
        
        return jsonify({
            "status": "success",
            "message": "Consumer restarted"
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        })

@app.route('/history/<user_id>', methods=['GET'])
def get_user_history(user_id):
    """Get user history endpoint"""
    try:
        type_filter = request.args.get('type')
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if type_filter:
                    cur.execute(
                        'SELECT * FROM chat WHERE user_id = %s AND type = %s ORDER BY timestamp DESC',
                        (user_id, type_filter)
                    )
                else:
                    cur.execute(
                        'SELECT * FROM chat WHERE user_id = %s ORDER BY timestamp DESC',
                        (user_id,)
                    )
                history = cur.fetchall()
                return jsonify({"status": "success", "data": history})
    except Exception as e:
        logger.error(f"Error fetching history: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

def cleanup():
    """Cleanup function for graceful shutdown"""
    global running, producer, consumer
    logger.info("Shutting down user service...")
    running = False
    
    if producer:
        try:
            producer.flush(timeout=5)
            producer.close(timeout=5)
            logger.info("Producer closed")
        except Exception as e:
            logger.error(f"Error closing producer: {e}")
    
    if consumer_thread:
        try:
            consumer_thread.join(timeout=5)
            logger.info("Consumer thread joined")
        except Exception as e:
            logger.error(f"Error joining consumer thread: {e}")

    logger.info("Shutdown complete")

def initialize_services():
    """Initialize all services"""
    global producer, consumer_thread
    try:
        # Initialize SuperTokens
        initialize_supertokens()
        
        # Initialize Kafka producer
        producer = create_kafka_producer()
        
        # Start consumer thread
        consumer_thread = threading.Thread(target=consume_messages, daemon=True)
        consumer_thread.start()
        logger.info("Consumer thread started")
        
        # Test database connection
        with get_db_connection() as conn:
            logger.info("Database connection successful")
        
        # Register cleanup
        atexit.register(cleanup)
        
        logger.info("All services initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize services: {e}")
        return False

if __name__ == "__main__":
    if initialize_services():
        logger.info("Starting Flask application...")
        app.run(host="0.0.0.0", port=int(os.getenv('USER_SERVICE_PORT', 8000)))
    else:
        logger.error("Failed to initialize services. Exiting...")
        exit(1)