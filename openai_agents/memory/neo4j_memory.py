"""
Neo4j Memory Module for Conversation Persistence
================================================
Stores and retrieves conversation/workflow metadata in Neo4j.

Environment Variables:
- NEO4J_URI: Neo4j connection URI (default: bolt://localhost:7687)
- NEO4J_USER: Neo4j username (default: neo4j)
- NEO4J_PASSWORD: Neo4j password (required)
"""

import os
from datetime import datetime
from typing import List, Optional

from dotenv import load_dotenv
from neo4j import AsyncGraphDatabase

load_dotenv()

# Global instance (will be initialized in backend)
neo4j_memory: Optional["Neo4jMemory"] = None

# Neo4j connection settings
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")


class ConversationNode:
    """Represents a conversation/workflow node in Neo4j"""

    def __init__(
        self,
        workflow_id: str,
        original_query: str,
        created_at: datetime,
        status: str = "pending",
        conversation_id: Optional[str] = None,
    ):
        self.workflow_id = workflow_id
        self.original_query = original_query
        self.created_at = created_at
        self.status = status
        self.conversation_id = conversation_id or workflow_id

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            "conversation_id": self.conversation_id,
            "workflow_id": self.workflow_id,
            "original_query": self.original_query,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


class MessageNode:
    """Represents a message in a conversation"""

    def __init__(
        self,
        message_id: str,
        workflow_id: str,
        message_type: str,  # "human" or "ai"
        content: str,
        timestamp: datetime,
        sequence: int,
        message_category: Optional[
            str
        ] = None,  # e.g., "initial_query", "clarification_question", "clarification_answer"
    ):
        self.message_id = message_id
        self.workflow_id = workflow_id
        self.message_type = message_type
        self.content = content
        self.timestamp = timestamp
        self.sequence = sequence
        self.message_category = message_category

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            "message_id": self.message_id,
            "workflow_id": self.workflow_id,
            "message_type": self.message_type,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "sequence": self.sequence,
            "message_category": self.message_category,
        }


class ResultNode:
    """Represents a research result in a conversation"""

    def __init__(
        self,
        result_id: str,
        workflow_id: str,
        short_summary: str,
        markdown_report: str,
        timestamp: datetime,
        sequence: int,
        title: Optional[str] = None,
        image_file_path: Optional[str] = None,
    ):
        self.result_id = result_id
        self.workflow_id = workflow_id
        self.short_summary = short_summary
        self.markdown_report = markdown_report
        self.timestamp = timestamp
        self.sequence = sequence
        self.title = title
        self.image_file_path = image_file_path

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        # Normalize image_file_path to ensure it starts with / for web serving
        img_path = self.image_file_path
        if img_path and not img_path.startswith("/"):
            img_path = f"/{img_path}"

        return {
            "result_id": self.result_id,
            "workflow_id": self.workflow_id,
            "short_summary": self.short_summary,
            "markdown_report": self.markdown_report,
            "timestamp": self.timestamp.isoformat(),
            "sequence": self.sequence,
            "title": self.title,
            "image_file_path": img_path,
        }


class Neo4jMemory:
    """Manages conversation memory in Neo4j"""

    def __init__(
        self,
        uri: str = NEO4J_URI,
        user: str = NEO4J_USER,
        password: Optional[str] = NEO4J_PASSWORD,
    ):
        if not password:
            raise ValueError("NEO4J_PASSWORD environment variable is required")
        self.driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async def close(self):
        """Close the Neo4j driver connection"""
        await self.driver.close()

    async def create_conversation(
        self, workflow_id: str, original_query: str, status: str = "pending"
    ) -> ConversationNode:
        """
        Create a new conversation node in Neo4j.

        Args:
            workflow_id: Temporal workflow ID
            original_query: The initial research query
            status: Current workflow status

        Returns:
            ConversationNode representing the created conversation
        """
        async with self.driver.session() as session:
            created_at = datetime.utcnow()
            # Use MERGE to avoid duplicates if conversation already exists
            result = await session.run(
                """
                MERGE (c:Conversation {workflow_id: $workflow_id})
                ON CREATE SET
                    c.original_query = $original_query,
                    c.status = $status,
                    c.created_at = $created_at,
                    c.conversation_id = $workflow_id
                ON MATCH SET
                    c.status = $status
                RETURN c
                """,
                workflow_id=workflow_id,
                original_query=original_query,
                status=status,
                created_at=created_at,
            )
            record = await result.single()
            # Consume the result to ensure transaction commits
            if record:
                node = record["c"]
                return ConversationNode(
                    workflow_id=node["workflow_id"],
                    original_query=node["original_query"],
                    created_at=node["created_at"],
                    status=node["status"],
                    conversation_id=node.get("conversation_id", workflow_id),
                )
            raise Exception("Failed to create conversation node")

    async def update_conversation_status(
        self, workflow_id: str, status: str
    ) -> Optional[ConversationNode]:
        """
        Update the status of a conversation.

        Args:
            workflow_id: Temporal workflow ID
            status: New status

        Returns:
            Updated ConversationNode or None if not found
        """
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (c:Conversation {workflow_id: $workflow_id})
                SET c.status = $status
                RETURN c
                """,
                workflow_id=workflow_id,
                status=status,
            )
            record = await result.single()
            if record:
                node = record["c"]
                return ConversationNode(
                    workflow_id=node["workflow_id"],
                    original_query=node["original_query"],
                    created_at=node["created_at"],
                    status=node["status"],
                    conversation_id=node.get("conversation_id", workflow_id),
                )
            return None

    async def get_conversation(self, workflow_id: str) -> Optional[ConversationNode]:
        """
        Retrieve a conversation by workflow ID.

        Args:
            workflow_id: Temporal workflow ID

        Returns:
            ConversationNode or None if not found
        """
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (c:Conversation {workflow_id: $workflow_id})
                RETURN c
                """,
                workflow_id=workflow_id,
            )
            record = await result.single()
            if record:
                node = record["c"]
                return ConversationNode(
                    workflow_id=node["workflow_id"],
                    original_query=node["original_query"],
                    created_at=node["created_at"],
                    status=node["status"],
                    conversation_id=node.get("conversation_id", workflow_id),
                )
            return None

    async def list_conversations(
        self, limit: int = 50, offset: int = 0
    ) -> List[ConversationNode]:
        """
        List all conversations, ordered by creation date (newest first).

        Args:
            limit: Maximum number of conversations to return
            offset: Number of conversations to skip

        Returns:
            List of ConversationNode objects
        """
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (c:Conversation)
                RETURN c
                ORDER BY c.created_at DESC
                SKIP $offset
                LIMIT $limit
                """,
                limit=limit,
                offset=offset,
            )
            conversations = []
            async for record in result:
                node = record["c"]
                conversations.append(
                    ConversationNode(
                        workflow_id=node["workflow_id"],
                        original_query=node["original_query"],
                        created_at=node["created_at"],
                        status=node["status"],
                        conversation_id=node.get(
                            "conversation_id", node["workflow_id"]
                        ),
                    )
                )
            return conversations

    async def add_message(
        self,
        workflow_id: str,
        message_type: str,  # "human" or "ai"
        content: str,
        message_category: Optional[str] = None,
    ) -> MessageNode:
        """
        Add a message to a conversation.

        Args:
            workflow_id: Temporal workflow ID
            message_type: "human" or "ai"
            content: The message content
            message_category: Optional category (e.g., "initial_query", "clarification_question", "clarification_answer")

        Returns:
            MessageNode representing the created message
        """
        async with self.driver.session() as session:
            # Get the next sequence number for this conversation (check both Message and Result nodes)
            seq_result = await session.run(
                """
                MATCH (c:Conversation {workflow_id: $workflow_id})-[:HAS_MESSAGE|HAS_RESULT]->(n)
                RETURN MAX(n.sequence) as max_seq
                """,
                workflow_id=workflow_id,
            )
            seq_record = await seq_result.single()
            next_sequence = (
                (seq_record["max_seq"] + 1)
                if seq_record and seq_record["max_seq"] is not None
                else 0
            )

            # Get the last node to link to (for NEXT relationship) - could be Message or Result
            last_node_result = await session.run(
                """
                MATCH (c:Conversation {workflow_id: $workflow_id})-[:HAS_MESSAGE|HAS_RESULT]->(n)
                WHERE n.sequence = $last_seq
                RETURN
                    CASE
                        WHEN n:Message THEN n.message_id
                        WHEN n:Result THEN n.result_id
                    END as last_id
                """,
                workflow_id=workflow_id,
                last_seq=next_sequence - 1,
            )
            last_node_record = await last_node_result.single()
            last_node_id = last_node_record["last_id"] if last_node_record else None

            # Create the message node
            message_id = f"{workflow_id}-msg-{next_sequence}"
            timestamp = datetime.utcnow()

            if last_node_id:
                # Link to previous node (could be Message or Result)
                result = await session.run(
                    """
                    MATCH (c:Conversation {workflow_id: $workflow_id})
                    MATCH (prev)
                    WHERE (prev:Message AND prev.message_id = $last_node_id)
                       OR (prev:Result AND prev.result_id = $last_node_id)
                    CREATE (m:Message {
                        message_id: $message_id,
                        workflow_id: $workflow_id,
                        message_type: $message_type,
                        content: $content,
                        timestamp: $timestamp,
                        sequence: $sequence,
                        message_category: $message_category
                    })
                    CREATE (c)-[:HAS_MESSAGE]->(m)
                    CREATE (prev)-[:NEXT]->(m)
                    RETURN m
                    """,
                    workflow_id=workflow_id,
                    message_id=message_id,
                    message_type=message_type,
                    content=content,
                    timestamp=timestamp,
                    sequence=next_sequence,
                    message_category=message_category,
                    last_node_id=last_node_id,
                )
            else:
                # First message, no previous to link to
                result = await session.run(
                    """
                    MATCH (c:Conversation {workflow_id: $workflow_id})
                    CREATE (m:Message {
                        message_id: $message_id,
                        workflow_id: $workflow_id,
                        message_type: $message_type,
                        content: $content,
                        timestamp: $timestamp,
                        sequence: $sequence,
                        message_category: $message_category
                    })
                    CREATE (c)-[:HAS_MESSAGE]->(m)
                    RETURN m
                    """,
                    workflow_id=workflow_id,
                    message_id=message_id,
                    message_type=message_type,
                    content=content,
                    timestamp=timestamp,
                    sequence=next_sequence,
                    message_category=message_category,
                )

            record = await result.single()
            # Consume the result to ensure transaction commits
            if record:
                node = record["m"]
                return MessageNode(
                    message_id=node["message_id"],
                    workflow_id=node["workflow_id"],
                    message_type=node["message_type"],
                    content=node["content"],
                    timestamp=node["timestamp"],
                    sequence=node["sequence"],
                    message_category=node.get("message_category"),
                )
            raise Exception("Failed to create message node")

    async def add_result(
        self,
        workflow_id: str,
        short_summary: str,
        markdown_report: str,
        title: Optional[str] = None,
        image_file_path: Optional[str] = None,
    ) -> ResultNode:
        """
        Add a research result to a conversation.

        Args:
            workflow_id: Temporal workflow ID
            short_summary: Brief summary of the research findings
            markdown_report: Full markdown research report
            title: Optional title extracted from markdown H1
            image_file_path: Optional path to generated image

        Returns:
            ResultNode representing the created result
        """
        print(f"INFO: add_result() called for workflow {workflow_id}")
        async with self.driver.session() as session:
            # Get the next sequence number for this conversation (check both Message and Result nodes)
            print(f"INFO: Getting next sequence number for workflow {workflow_id}")
            seq_result = await session.run(
                """
                MATCH (c:Conversation {workflow_id: $workflow_id})-[:HAS_MESSAGE|HAS_RESULT]->(n)
                RETURN MAX(n.sequence) as max_seq
                """,
                workflow_id=workflow_id,
            )
            seq_record = await seq_result.single()
            next_sequence = (
                (seq_record["max_seq"] + 1)
                if seq_record and seq_record["max_seq"] is not None
                else 0
            )
            print(f"INFO: Next sequence number: {next_sequence}")

            # Get the last node to link to (for NEXT relationship) - could be Message or Result
            last_node_result = await session.run(
                """
                MATCH (c:Conversation {workflow_id: $workflow_id})-[:HAS_MESSAGE|HAS_RESULT]->(n)
                WHERE n.sequence = $last_seq
                RETURN
                    CASE
                        WHEN n:Message THEN n.message_id
                        WHEN n:Result THEN n.result_id
                    END as last_id
                """,
                workflow_id=workflow_id,
                last_seq=next_sequence - 1,
            )
            last_node_record = await last_node_result.single()
            last_node_id = last_node_record["last_id"] if last_node_record else None

            # Create the result node
            result_id = f"{workflow_id}-result-{next_sequence}"
            timestamp = datetime.utcnow()

            if last_node_id:
                # Link to previous node (could be Message or Result)
                result = await session.run(
                    """
                    MATCH (c:Conversation {workflow_id: $workflow_id})
                    MATCH (prev)
                    WHERE (prev:Message AND prev.message_id = $last_node_id)
                       OR (prev:Result AND prev.result_id = $last_node_id)
                    CREATE (r:Result {
                        result_id: $result_id,
                        workflow_id: $workflow_id,
                        short_summary: $short_summary,
                        markdown_report: $markdown_report,
                        timestamp: $timestamp,
                        sequence: $sequence,
                        title: $title,
                        image_file_path: $image_file_path
                    })
                    CREATE (c)-[:HAS_RESULT]->(r)
                    CREATE (prev)-[:NEXT]->(r)
                    RETURN r
                    """,
                    workflow_id=workflow_id,
                    result_id=result_id,
                    short_summary=short_summary,
                    markdown_report=markdown_report,
                    timestamp=timestamp,
                    sequence=next_sequence,
                    title=title,
                    image_file_path=image_file_path,
                    last_node_id=last_node_id,
                )
            else:
                # First node in conversation (unlikely for a result, but handle it)
                result = await session.run(
                    """
                    MATCH (c:Conversation {workflow_id: $workflow_id})
                    CREATE (r:Result {
                        result_id: $result_id,
                        workflow_id: $workflow_id,
                        short_summary: $short_summary,
                        markdown_report: $markdown_report,
                        timestamp: $timestamp,
                        sequence: $sequence,
                        title: $title,
                        image_file_path: $image_file_path
                    })
                    CREATE (c)-[:HAS_RESULT]->(r)
                    RETURN r
                    """,
                    workflow_id=workflow_id,
                    result_id=result_id,
                    short_summary=short_summary,
                    markdown_report=markdown_report,
                    timestamp=timestamp,
                    sequence=next_sequence,
                    title=title,
                    image_file_path=image_file_path,
                )

            record = await result.single()
            # Consume the result to ensure transaction commits
            if record:
                node = record["r"]
                print(
                    f"INFO: ✅ Result node created successfully with ID: {node['result_id']}"
                )
                return ResultNode(
                    result_id=node["result_id"],
                    workflow_id=node["workflow_id"],
                    short_summary=node["short_summary"],
                    markdown_report=node["markdown_report"],
                    timestamp=node["timestamp"],
                    sequence=node["sequence"],
                    title=node.get("title"),
                    image_file_path=node.get("image_file_path"),
                )
            print(f"ERROR: Failed to create result node - no record returned")
            raise Exception("Failed to create result node")

    async def link_existing_result(
        self,
        workflow_id: str,
        existing_result_id: str,
    ) -> ResultNode:
        """
        Link an existing Result node to a conversation instead of creating a new one.
        This is used when we reuse a Result from the knowledge graph (e.g., 80% similarity match).

        Args:
            workflow_id: Temporal workflow ID
            existing_result_id: ID of the existing Result node to link

        Returns:
            ResultNode representing the linked result
        """
        async with self.driver.session() as session:
            # First, get the existing Result node
            result = await session.run(
                """
                MATCH (r:Result {result_id: $existing_result_id})
                RETURN r
                """,
                existing_result_id=existing_result_id,
            )
            record = await result.single()
            if not record:
                raise Exception(f"Result node with ID {existing_result_id} not found")

            existing_node = record["r"]

            # Check if this Result is already linked to this conversation
            check_result = await session.run(
                """
                MATCH (c:Conversation {workflow_id: $workflow_id})-[:HAS_RESULT]->(r:Result {result_id: $existing_result_id})
                RETURN r
                """,
                workflow_id=workflow_id,
                existing_result_id=existing_result_id,
            )
            if await check_result.single():
                print(
                    f"INFO: Result {existing_result_id} already linked to conversation {workflow_id}"
                )
                return ResultNode(
                    result_id=existing_node["result_id"],
                    workflow_id=existing_node["workflow_id"],
                    short_summary=existing_node["short_summary"],
                    markdown_report=existing_node["markdown_report"],
                    timestamp=existing_node["timestamp"],
                    sequence=existing_node.get("sequence", 0),
                    title=existing_node.get("title"),
                    image_file_path=existing_node.get("image_file_path"),
                )

            # Get the last node in the conversation to link from
            last_node_result = await session.run(
                """
                MATCH (c:Conversation {workflow_id: $workflow_id})-[:HAS_MESSAGE|HAS_RESULT]->(n)
                RETURN n
                ORDER BY n.sequence DESC
                LIMIT 1
                """,
                workflow_id=workflow_id,
            )
            last_node_record = await last_node_result.single()

            # Link the existing Result to the conversation
            if last_node_record:
                last_node = last_node_record["n"]
                last_node_id = last_node.element_id
                link_result = await session.run(
                    """
                    MATCH (c:Conversation {workflow_id: $workflow_id})
                    MATCH (r:Result {result_id: $existing_result_id})
                    MATCH (prev)
                    WHERE elementId(prev) = $last_node_id
                    CREATE (c)-[:HAS_RESULT]->(r)
                    CREATE (prev)-[:NEXT]->(r)
                    RETURN r
                    """,
                    workflow_id=workflow_id,
                    existing_result_id=existing_result_id,
                    last_node_id=last_node_id,
                )
            else:
                # First node in conversation (unlikely for a result, but handle it)
                link_result = await session.run(
                    """
                    MATCH (c:Conversation {workflow_id: $workflow_id})
                    MATCH (r:Result {result_id: $existing_result_id})
                    CREATE (c)-[:HAS_RESULT]->(r)
                    RETURN r
                    """,
                    workflow_id=workflow_id,
                    existing_result_id=existing_result_id,
                )

            link_record = await link_result.single()
            if link_record:
                node = link_record["r"]
                print(
                    f"INFO: ✅ Linked existing Result node {existing_result_id} to conversation {workflow_id}"
                )
                return ResultNode(
                    result_id=node["result_id"],
                    workflow_id=node["workflow_id"],
                    short_summary=node["short_summary"],
                    markdown_report=node["markdown_report"],
                    timestamp=node["timestamp"],
                    sequence=node.get("sequence", 0),
                    title=node.get("title"),
                    image_file_path=node.get("image_file_path"),
                )
            raise Exception("Failed to link existing result node")

    async def get_messages(self, workflow_id: str, limit: Optional[int] = None) -> List:
        """
        Get all messages and results for a conversation, ordered by sequence.

        Args:
            workflow_id: Temporal workflow ID
            limit: Optional limit on number of items to return

        Returns:
            List of MessageNode and ResultNode objects in sequence order
        """
        async with self.driver.session() as session:
            if limit:
                result = await session.run(
                    """
                    MATCH (c:Conversation {workflow_id: $workflow_id})-[:HAS_MESSAGE|HAS_RESULT]->(n)
                    RETURN n
                    ORDER BY n.sequence ASC
                    LIMIT $limit
                    """,
                    workflow_id=workflow_id,
                    limit=limit,
                )
            else:
                result = await session.run(
                    """
                    MATCH (c:Conversation {workflow_id: $workflow_id})-[:HAS_MESSAGE|HAS_RESULT]->(n)
                    RETURN n
                    ORDER BY n.sequence ASC
                    """,
                    workflow_id=workflow_id,
                )
            items = []
            async for record in result:
                node = record["n"]
                if "Message" in node.labels:
                    # It's a Message node
                    items.append(
                        MessageNode(
                            message_id=node["message_id"],
                            workflow_id=node["workflow_id"],
                            message_type=node["message_type"],
                            content=node["content"],
                            timestamp=node["timestamp"],
                            sequence=node["sequence"],
                            message_category=node.get("message_category"),
                        )
                    )
                elif "Result" in node.labels:
                    # It's a Result node
                    items.append(
                        ResultNode(
                            result_id=node["result_id"],
                            workflow_id=node["workflow_id"],
                            short_summary=node["short_summary"],
                            markdown_report=node["markdown_report"],
                            timestamp=node["timestamp"],
                            sequence=node["sequence"],
                            title=node.get("title"),
                            image_file_path=node.get("image_file_path"),
                        )
                    )
            return items

    async def get_results(self, workflow_id: Optional[str] = None) -> List[ResultNode]:
        """
        Get all Result nodes, optionally filtered by workflow_id.

        Args:
            workflow_id: Optional workflow ID to filter results

        Returns:
            List of ResultNode objects
        """
        async with self.driver.session() as session:
            if workflow_id:
                result = await session.run(
                    """
                    MATCH (c:Conversation {workflow_id: $workflow_id})-[:HAS_RESULT]->(r:Result)
                    RETURN r
                    ORDER BY r.sequence ASC
                    """,
                    workflow_id=workflow_id,
                )
            else:
                result = await session.run(
                    """
                    MATCH (r:Result)
                    RETURN r
                    ORDER BY r.timestamp DESC
                    """,
                )
            results = []
            async for record in result:
                node = record["r"]
                results.append(
                    ResultNode(
                        result_id=node["result_id"],
                        workflow_id=node["workflow_id"],
                        short_summary=node["short_summary"],
                        markdown_report=node["markdown_report"],
                        timestamp=node["timestamp"],
                        sequence=node["sequence"],
                        title=node.get("title"),
                        image_file_path=node.get("image_file_path"),
                    )
                )
            return results

    async def verify_connection(self) -> bool:
        """
        Verify that the Neo4j connection is working.

        Returns:
            True if connection is successful, False otherwise
        """
        try:
            async with self.driver.session() as session:
                await session.run("RETURN 1")
                return True
        except Exception:
            return False


async def get_neo4j_memory() -> Optional["Neo4jMemory"]:
    """Get or create the global Neo4j memory instance"""
    global neo4j_memory
    if neo4j_memory is None:
        # Check if password is configured
        if not NEO4J_PASSWORD:
            print("INFO: Neo4j memory disabled - NEO4J_PASSWORD not set in environment")
            return None

        try:
            print(
                f"INFO: Initializing Neo4j memory connection to {NEO4J_URI} as user {NEO4J_USER}"
            )
            neo4j_memory = Neo4jMemory()
            # Verify connection
            if not await neo4j_memory.verify_connection():
                print(
                    "ERROR: Neo4j connection verification failed - check your connection settings"
                )
                neo4j_memory = None
                return None
            print("INFO: Neo4j memory connection established successfully")
        except ValueError as e:
            print(f"ERROR: Neo4j configuration error: {e}")
            neo4j_memory = None
            return None
        except Exception as e:
            print(f"ERROR: Failed to initialize Neo4j memory: {e}")
            import traceback

            traceback.print_exc()
            neo4j_memory = None
            return None
    return neo4j_memory
