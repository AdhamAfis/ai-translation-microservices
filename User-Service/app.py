from flask import Flask, request, jsonify
from flask_cors import CORS
from supertokens_python import init, InputAppInfo, SupertokensConfig
from supertokens_python.recipe import session, emailpassword
from supertokens_python.recipe.session.framework.flask import verify_session
from supertokens_python.recipe.emailpassword import EmailPasswordRecipe
import psycopg2
from psycopg2.extras import RealDictCursor
from kafka import KafkaConsumer, KafkaProducer
import os
import time
import json
import uvicorn

app = Flask(__name__)
CORS(app, supports_credentials=True, origins=["http://localhost:4200"])

DATABASE_URL = os.getenv('DATABASE_URL')
SUPERTOKENS_API_KEY = os.getenv('SUPERTOKENS_API_KEY')
KAFKA_BROKER =  'kafka:9092'

producer = KafkaProducer(
    bootstrap_servers=KAFKA_BROKER,
    value_serializer=lambda v: json.dumps(v).encode('utf-8')
)

consumer = KafkaConsumer(
    'login', 'signup', 'e2a-translation-response', 'a2e-translation-response', 'summarization-response',
    bootstrap_servers=KAFKA_BROKER,
    value_deserializer=lambda v: json.loads(v.decode('utf-8')),
    group_id='user-service-group',
    auto_offset_reset='earliest',
    enable_auto_commit=True,
    max_poll_interval_ms=300000,  # 5 minutes
    session_timeout_ms=180000,    # 3 minutes
    heartbeat_interval_ms=60000   # 1 minute
)

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

init(
    app_info=InputAppInfo(
        app_name="Cloud Project",
        api_domain="http://localhost:8000",
        website_domain="http://localhost:4200",
    ),
    supertokens_config=SupertokensConfig(
        connection_uri="http://supertokens:3567",
        api_key=SUPERTOKENS_API_KEY,
    ),
    framework="flask",
    recipe_list=[
        emailpassword.init(),
        session.init(),
    ],
)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy"})

def handle_login(data):
    form_fields = data.get('formFields', {})
    response = {}

    try:
        email = form_fields['email']
        password = form_fields['password']

        user = emailpassword.EmailPasswordRecipe.get_instance().sign_in(email, password)
        response = {"status": "success", "userId": user.user_id}
    except Exception as e:
        response = {"status": "error", "message": str(e)}

    producer.send('login-response', response)

def handle_signup(data):
    form_fields = data.get('formFields', {})
    response = {}

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

        response = {"status": "success", "userId": user.user_id}
    except Exception as e:
        response = {"status": "error", "message": str(e)}

    producer.send('signup-response', response)

def handle_translation_or_summarization(data, topic):
    type_map = {
        'e2a-translation-response': 'e2a-translation',
        'a2e-translation-response': 'a2e-translation',
        'summarization-response': 'summarization'
    }

    message_type = type_map.get(topic)
    user_id = data.get('user_id')
    chat_id = data.get('chat_id')
    in_text = data.get('in_text')
    out_text = data.get('out_text')
    formality = data.get('formality') if topic == 'summarization-response' else None

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'INSERT INTO chat (user_id, chat_id, in_text, out_text, type, formality, timestamp) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)',
                    (user_id, chat_id, in_text, out_text, message_type, formality)
                )
                conn.commit()
        print(f"Stored message for topic {topic}: {data}")
    except Exception as e:
        print(f"Failed to store message for topic {topic}: {e}")

def consume_messages():
    for message in consumer:
        topic = message.topic
        data = message.value

        if topic == 'login':
            handle_login(data)
        elif topic == 'signup':
            handle_signup(data)
        elif topic in ['e2a-translation-response', 'a2e-translation-response', 'summarization-response']:
            handle_translation_or_summarization(data, topic)

def connect_with_retry():
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            with get_db_connection() as conn:
                print("Connected to PostgreSQL")
                return
        except Exception as e:
            print(f"Failed to connect to PostgreSQL (attempt {attempt}/{max_retries}): {e}")
            time.sleep(5)
    raise Exception("Failed to connect to PostgreSQL after multiple retries")

def initialize_app():
    try:
        connect_with_retry()
        print("Starting Kafka consumer")
        consume_messages()
    except Exception as e:
        print(f"Failed to initialize application: {e}")
        exit(1)

@app.route('/history/<user_id>', methods=['GET'])
def get_user_history(user_id):
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
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
