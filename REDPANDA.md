# Redpanda Event Streaming - Advanced Setup & Troubleshooting

This document contains detailed instructions for setting up, monitoring, and troubleshooting Redpanda event streaming in the research workflow application.

## Table of Contents
- [Docker Network Setup (Recommended)](#docker-network-setup-recommended)
- [Monitoring Options](#monitoring-options)
- [Troubleshooting Guide](#troubleshooting-guide)
- [Advanced Configuration](#advanced-configuration)

---

## Docker Network Setup (Recommended)

For the most reliable setup, especially when using Redpanda Console, configure both containers on the same Docker network:

```bash
# Create a network
docker network create redpanda-network

# Stop existing containers if running
docker stop redpanda redpanda-console 2>/dev/null || true
docker rm redpanda redpanda-console 2>/dev/null || true

# Start Redpanda on the network
docker run -d \
  --name redpanda \
  --network redpanda-network \
  -p 9092:9092 \
  -p 9644:9644 \
  docker.redpanda.com/redpandadata/redpanda:latest \
  redpanda start \
  --smp 1 \
  --memory 1G \
  --overprovisioned \
  --node-id 0 \
  --kafka-addr PLAINTEXT://0.0.0.0:29092,OUTSIDE://0.0.0.0:9092 \
  --advertise-kafka-addr PLAINTEXT://redpanda:29092,OUTSIDE://localhost:9092

# Create topics
docker exec -it redpanda rpk topic create research-workflow-events

# Start Console on the same network
docker run -d \
  --name redpanda-console \
  --network redpanda-network \
  -p 8080:8080 \
  -e KAFKA_BROKERS=redpanda:29092 \
  docker.redpanda.com/redpandadata/console:latest
```

Then open http://localhost:8080 in your browser.

---

## Monitoring Options

### Option 1: Redpanda Console (Web UI)

**Using Docker Network (Best):**
```bash
docker run -d \
  --name redpanda-console \
  --network redpanda-network \
  -p 8080:8080 \
  -e KAFKA_BROKERS=redpanda:29092 \
  docker.redpanda.com/redpandadata/console:latest
```

**Using Host Network (Linux only):**
```bash
docker run -d \
  --name redpanda-console \
  --network host \
  -e KAFKA_BROKERS=localhost:9092 \
  docker.redpanda.com/redpandadata/console:latest
```

**Direct Host Access (Mac/Windows fallback):**
```bash
docker run -d \
  --name redpanda-console \
  -p 8080:8080 \
  --add-host=host.docker.internal:host-gateway \
  -e KAFKA_BROKERS=host.docker.internal:9092 \
  docker.redpanda.com/redpandadata/console:latest
```

**Console Features:**
- Visual topic explorer
- Real-time message viewer with JSON formatting
- Consumer group monitoring
- Message throughput and lag metrics
- Schema registry integration (if configured)

### Option 2: rpk CLI Tool

The `rpk` command-line tool is bundled with Redpanda and provides powerful debugging capabilities.

**Basic Commands:**
```bash
# List all topics
docker exec -it redpanda rpk topic list

# View topic details and message count
docker exec -it redpanda rpk topic describe research-workflow-events -a

# Check cluster health
docker exec -it redpanda rpk cluster info
```

**Consuming Events:**
```bash
# Watch all new events as they arrive
docker exec -it redpanda rpk topic consume research-workflow-events

# Watch from the beginning
docker exec -it redpanda rpk topic consume research-workflow-events --offset start

# Pretty print JSON events (requires jq)
docker exec -it redpanda rpk topic consume research-workflow-events -f '%v\n' | jq .

# Tail last 10 messages
docker exec -it redpanda rpk topic consume research-workflow-events --offset -10
```

**Filtering Events:**
```bash
# Watch only clarification events
docker exec -it redpanda rpk topic consume research-workflow-events -f '%v\n' | \
  jq 'select(.event_type | contains("clarification"))'

# Watch only image generation events
docker exec -it redpanda rpk topic consume research-workflow-events -f '%v\n' | \
  jq 'select(.event_type | contains("image"))'

# Watch lifecycle events only
docker exec -it redpanda rpk topic consume research-workflow-events -f '%v\n' | \
  jq 'select(.event_type | test("query_received|research_started|research_complete"))'
```

### Option 3: Install rpk Locally

For convenience, you can install `rpk` on your host machine:

**macOS:**
```bash
brew install redpanda-data/tap/redpanda
```

**Linux:**
```bash
curl -LO https://github.com/redpanda-data/redpanda/releases/latest/download/rpk-linux-amd64.zip
unzip rpk-linux-amd64.zip
sudo mv rpk /usr/local/bin/
```

Then use rpk directly:
```bash
rpk topic consume research-workflow-events --brokers localhost:9092
```

### Option 4: Python Consumer

Create a custom consumer script:

```python
from kafka import KafkaConsumer
import json

consumer = KafkaConsumer(
    'research-workflow-events',
    bootstrap_servers=['localhost:9092'],
    value_deserializer=lambda m: json.loads(m.decode('utf-8')),
    auto_offset_reset='latest',
    group_id='my-consumer-group'
)

print("Listening for events...")
for message in consumer:
    event = message.value
    print(f"\n--- Event: {event['event_type']} ---")
    print(f"Workflow: {event['workflow_id']}")
    print(f"Timestamp: {event['timestamp']}")
    print(f"Data: {json.dumps(event['data'], indent=2)}")
```

---

## Troubleshooting Guide

### No events appearing in topic

**Symptoms:** Worker logs show events published, but topic is empty or rpk/Console shows no messages.

**Diagnosis:**
```bash
# 1. Verify Redpanda is running
docker ps | grep redpanda

# 2. Check if topic exists
docker exec -it redpanda rpk topic list

# 3. Check broker is reachable
docker exec -it redpanda rpk cluster info

# 4. Verify worker configuration
grep REDPANDA_BOOTSTRAP_SERVERS .env
```

**Solutions:**
1. Ensure `REDPANDA_BOOTSTRAP_SERVERS` is set in `.env`
2. Restart worker after changing `.env`
3. Create topic manually if missing: `docker exec -it redpanda rpk topic create research-workflow-events`
4. Check worker logs for connection errors

### Connection refused errors

**Symptoms:** Worker logs show `Connection refused` or `dial tcp connect: connection refused`

**Diagnosis:**
```bash
# Check Redpanda container status
docker ps | grep redpanda

# Check port mapping
docker port redpanda

# Test connectivity from host
nc -zv localhost 9092
# or
telnet localhost 9092
```

**Solutions:**

1. **For worker connection issues:**
   - Ensure Redpanda is running: `docker start redpanda`
   - Verify `.env` has `REDPANDA_BOOTSTRAP_SERVERS='localhost:9092'`
   - Check no firewall blocking port 9092

2. **For Docker Desktop (Mac/Windows):**
   - Use `localhost:9092` in `.env`
   - Not `127.0.0.1:9092` or `host.docker.internal:9092`

3. **For Linux:**
   - May need to use Docker host IP instead
   - Find with: `docker inspect redpanda | grep IPAddress`

### Redpanda Console showing "connection refused"

**Symptoms:** Console UI loads but shows "Failed to list topic start offsets" or similar errors.

**Best Solution - Use Docker Network:**
```bash
# Recreate both containers on same network
docker network create redpanda-network
docker stop redpanda redpanda-console
docker rm redpanda redpanda-console

# Restart Redpanda on network
docker run -d \
  --name redpanda \
  --network redpanda-network \
  -p 9092:9092 \
  -p 9644:9644 \
  docker.redpanda.com/redpandadata/redpanda:latest \
  redpanda start \
  --smp 1 \
  --memory 1G \
  --overprovisioned \
  --node-id 0 \
  --kafka-addr PLAINTEXT://0.0.0.0:29092,OUTSIDE://0.0.0.0:9092 \
  --advertise-kafka-addr PLAINTEXT://redpanda:29092,OUTSIDE://localhost:9092

# Recreate topic
docker exec -it redpanda rpk topic create research-workflow-events

# Start Console on network
docker run -d \
  --name redpanda-console \
  --network redpanda-network \
  -p 8080:8080 \
  -e KAFKA_BROKERS=redpanda:29092 \
  docker.redpanda.com/redpandadata/console:latest
```

**Debug Console connectivity:**
```bash
# Check Console can reach Redpanda
docker exec -it redpanda-console ping redpanda

# Test TCP connection
docker exec -it redpanda-console nc -zv redpanda 29092

# Verify environment variable
docker inspect redpanda-console | grep KAFKA_BROKERS
```

**Alternative:** Use rpk CLI instead (see Option 2 above)

### SASL authentication failures (Serverless/Cloud)

**Symptoms:** `Authentication failed` or `SASL handshake failed` errors in worker logs.

**Solutions:**
1. Verify credentials in `.env` match your Redpanda Cloud console exactly
2. Check `REDPANDA_SASL_MECHANISM` is set correctly (usually `SCRAM-SHA-256`)
3. Ensure `REDPANDA_SECURITY_PROTOCOL='SASL_SSL'` is set
4. For Serverless, bootstrap servers should end in `:9092` and use full FQDN
5. Test connection with rpk:
   ```bash
   rpk topic list \
     --brokers your-broker.redpanda.cloud:9092 \
     --user your-username \
     --password your-password \
     --tls-enabled
   ```

### Events published but not visible immediately

**Explanation:** This is normal behavior. Events may take a few seconds to appear due to:
- Producer batching
- Consumer polling intervals
- Console refresh rates

**Solutions:**
- Wait 5-10 seconds and refresh Console
- Use `rpk topic consume` with `--offset start` to see all messages
- Check message count: `docker exec -it redpanda rpk topic describe research-workflow-events -a`

### Worker logs show event publishing but no confirmation

**Symptoms:** Logs show "Publishing event" but no "Event published successfully" message.

**Diagnosis:**
Check worker logs for full error:
```bash
uv run openai_agents/run_worker.py 2>&1 | grep -A5 "Publishing event"
```

**Common causes:**
1. **Timeout:** Activity timeout before confirmation received
   - Solution: Events are likely published despite timeout
   - Verify with rpk: `docker exec -it redpanda rpk topic describe research-workflow-events -a`

2. **Network latency:** Slow connection to Redpanda
   - Solution: Increase timeout in `redpanda_activity.py` (currently 5 seconds)

3. **Broker overload:** Redpanda container under-resourced
   - Solution: Increase memory: `--memory 2G` in docker run command

---

## Advanced Configuration

### Topic Retention and Partitioning

Configure topics with custom settings:

```bash
# Set retention to 7 days
docker exec -it redpanda rpk topic alter-config research-workflow-events \
  --set retention.ms=604800000

# Set retention to 1GB per partition
docker exec -it redpanda rpk topic alter-config research-workflow-events \
  --set retention.bytes=1073741824

# Increase partitions for better parallelism
docker exec -it redpanda rpk topic add-partitions research-workflow-events --num 3
```

### Consumer Groups

Monitor consumer lag and offset:

```bash
# List consumer groups
docker exec -it redpanda rpk group list

# Describe consumer group
docker exec -it redpanda rpk group describe my-consumer-group

# Reset consumer offset to beginning
docker exec -it redpanda rpk group seek my-consumer-group \
  --topics research-workflow-events \
  --to start
```

### Performance Tuning

For high-throughput scenarios:

**Producer settings in `redpanda_activity.py`:**
```python
config = {
    "bootstrap_servers": [...],
    "value_serializer": lambda v: json.dumps(v).encode("utf-8"),
    "acks": 1,  # 0=no wait, 1=leader only, all=all replicas
    "compression_type": "snappy",  # or "gzip", "lz4", "zstd"
    "batch_size": 16384,  # bytes per batch
    "linger_ms": 10,  # wait up to 10ms to batch
}
```

**Redpanda container resources:**
```bash
docker run -d \
  --name redpanda \
  --memory 4G \
  --cpus 2 \
  ...
```

### Monitoring Metrics

Access Redpanda metrics:

```bash
# Admin API
curl http://localhost:9644/v1/cluster/health_overview

# Prometheus metrics
curl http://localhost:9644/metrics

# Public metrics endpoint
curl http://localhost:9644/public_metrics
```

### Multiple Topics Setup

Create and configure separate topics for each event category:

```bash
# Create topics
docker exec -it redpanda rpk topic create workflow-lifecycle
docker exec -it redpanda rpk topic create workflow-clarifications
docker exec -it redpanda rpk topic create workflow-research
docker exec -it redpanda rpk topic create workflow-artifacts

# Set retention policies
for topic in workflow-lifecycle workflow-clarifications workflow-research workflow-artifacts; do
  docker exec -it redpanda rpk topic alter-config $topic \
    --set retention.ms=604800000 \
    --set retention.bytes=1073741824
done
```

Then update `.env`:
```bash
REDPANDA_TOPIC_LIFECYCLE='workflow-lifecycle'
REDPANDA_TOPIC_CLARIFICATIONS='workflow-clarifications'
REDPANDA_TOPIC_RESEARCH='workflow-research'
REDPANDA_TOPIC_ARTIFACTS='workflow-artifacts'
```

---

## Additional Resources

- [Redpanda Documentation](https://docs.redpanda.com/)
- [rpk Command Reference](https://docs.redpanda.com/docs/reference/rpk/)
- [Redpanda Console Documentation](https://docs.redpanda.com/docs/manage/console/)
- [Kafka Python Client](https://kafka-python.readthedocs.io/)
