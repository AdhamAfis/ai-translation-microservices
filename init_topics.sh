#!/bin/bash

# Define the main topics
main_topics=("login" "signup" "e2a-translation" "a2e-translation" "summarization")

# Define the response topics by appending '-response' to each main topic
response_topics=("${main_topics[@]/%/-response}")

# Combine main topics and response topics
all_topics=("${main_topics[@]}" "${response_topics[@]}")

# Kafka broker address
BOOTSTRAP_SERVER="kafka:9092"

# Function to create a Kafka topic
create_topic() {
    local topic=$1
    echo "Creating topic: $topic"
    docker exec kafka kafka-topics.sh \
        --create \
        --topic "$topic" \
        --bootstrap-server "$BOOTSTRAP_SERVER" \
        --replication-factor 1 \
        --partitions 1 \
        --if-not-exists
}

# Iterate over all topics and create them
for topic in "${all_topics[@]}"; do
    create_topic "$topic"
done

echo "All topics have been created successfully."