#!/bin/sh

# Path to the Kafka topics command
KT="/opt/bitnami/kafka/bin/kafka-topics.sh"

# Wait for Kafka to be up and running
echo "Waiting for Kafka to be ready..."
"$KT" --bootstrap-server localhost:9092 --list

# Create the Kafka topics
echo "Creating Kafka topics..."
for topic in login signup e2a-translation a2e-translation summarization login-response signup-response e2a-translation-response a2e-translation-response summarization-response; do
  "$KT" --bootstrap-server localhost:9092 --create --if-not-exists --topic $topic --partitions 1 --replication-factor 1
  echo "Created topic: $topic"
done

# List all topics to verify creation
echo "Successfully created the following topics:"
"$KT" --bootstrap-server localhost:9092 --list
