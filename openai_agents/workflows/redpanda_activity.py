"""Redpanda event streaming activity for workflow progress tracking."""

import json
import os
from datetime import datetime
from typing import Any

from kafka import KafkaProducer
from temporalio import activity


def get_kafka_config() -> dict[str, Any]:
    """Get Kafka configuration from environment variables."""
    bootstrap_servers = os.getenv("REDPANDA_BOOTSTRAP_SERVERS", "localhost:9092")

    config = {
        "bootstrap_servers": [s.strip() for s in bootstrap_servers.split(",")],
        "value_serializer": lambda v: json.dumps(v).encode("utf-8"),
    }

    # Optional SASL authentication for Redpanda Serverless/Cloud
    security_protocol = os.getenv("REDPANDA_SECURITY_PROTOCOL")
    sasl_mechanism = os.getenv("REDPANDA_SASL_MECHANISM")
    sasl_username = os.getenv("REDPANDA_SASL_USERNAME")
    sasl_password = os.getenv("REDPANDA_SASL_PASSWORD")

    if sasl_mechanism and sasl_username and sasl_password:
        config.update(
            {
                "security_protocol": security_protocol or "SASL_SSL",
                "sasl_mechanism": sasl_mechanism,
                "sasl_plain_username": sasl_username,
                "sasl_plain_password": sasl_password,
            }
        )
    elif security_protocol:
        config["security_protocol"] = security_protocol

    return config


def get_topic_for_event(event_type: str) -> str:
    """
    Determine the appropriate topic for an event type.

    Supports topic routing based on event categories:
    - Lifecycle events: query_received, research_complete
    - Clarification events: clarifications_*, clarification_*
    - Research events: research_*, search_*, report_*
    - Artifact events: image_*, pdf_*
    """
    default_topic = os.getenv("REDPANDA_TOPIC", "research-workflow-events")

    # Lifecycle events
    if event_type in ["query_received", "research_complete", "research_started"]:
        return os.getenv("REDPANDA_TOPIC_LIFECYCLE", default_topic)

    # Clarification events
    if "clarification" in event_type:
        return os.getenv("REDPANDA_TOPIC_CLARIFICATIONS", default_topic)

    # Artifact generation events
    if any(x in event_type for x in ["image_", "pdf_", "_generated"]):
        return os.getenv("REDPANDA_TOPIC_ARTIFACTS", default_topic)

    # Research pipeline events
    if any(x in event_type for x in ["search_", "report_", "knowledge_graph"]):
        return os.getenv("REDPANDA_TOPIC_RESEARCH", default_topic)

    return default_topic


@activity.defn
async def publish_workflow_event(
    event_type: str, workflow_id: str, data: dict[str, Any]
) -> None:
    """
    Publish workflow progress events to Redpanda.

    Args:
        event_type: Type of event (e.g., "query_received", "research_complete")
        workflow_id: Unique identifier for the workflow
        data: Event-specific payload data

    Example event types:
        - query_received
        - knowledge_graph_hit
        - clarifications_needed
        - clarifications_generated
        - clarification_answered
        - clarifications_complete
        - research_started
        - search_plan_created
        - search_executing
        - image_generation_started
        - image_generated
        - report_writing
        - report_generated
        - pdf_generation_started
        - pdf_generated
        - research_complete
    """
    # Skip if Redpanda is not configured
    if not os.getenv("REDPANDA_BOOTSTRAP_SERVERS"):
        activity.logger.debug(
            "Redpanda not configured (REDPANDA_BOOTSTRAP_SERVERS not set), skipping event publish"
        )
        return

    topic = get_topic_for_event(event_type)

    try:
        kafka_config = get_kafka_config()
        producer = KafkaProducer(**kafka_config)

        event = {
            "event_type": event_type,
            "workflow_id": workflow_id,
            "timestamp": datetime.utcnow().isoformat(),
            "data": data,
        }

        activity.logger.info(
            f"Publishing event to Redpanda: {event_type} for workflow {workflow_id} to topic {topic}"
        )

        future = producer.send(topic, value=event)
        producer.flush(timeout=5)

        # Wait for confirmation
        record_metadata = future.get(timeout=5)
        activity.logger.info(
            f"Event published successfully to topic {record_metadata.topic}, "
            f"partition {record_metadata.partition}, offset {record_metadata.offset}"
        )

    except Exception as e:
        activity.logger.error(f"Failed to publish event to Redpanda: {str(e)}")
        # Don't raise - we don't want event publishing failures to break workflows
        # In production, you might want to send this to a dead-letter queue
    finally:
        if "producer" in locals():
            producer.close()
