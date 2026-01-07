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


class Neo4jMemory:
    """Manages conversation memory in Neo4j"""

    def __init__(
        self,
        uri: str = NEO4J_URI,
        user: str = NEO4J_USER,
        password: str = NEO4J_PASSWORD,
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
            result = await session.run(
                """
                CREATE (c:Conversation {
                    workflow_id: $workflow_id,
                    original_query: $original_query,
                    status: $status,
                    created_at: $created_at,
                    conversation_id: $workflow_id
                })
                RETURN c
                """,
                workflow_id=workflow_id,
                original_query=original_query,
                status=status,
                created_at=created_at,
            )
            record = await result.single()
            if record:
                node = record["c"]
                return ConversationNode(
                    workflow_id=node["workflow_id"],
                    original_query=node["original_query"],
                    created_at=node["created_at"],
                    status=node["status"],
                    conversation_id=node["conversation_id"],
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
        try:
            neo4j_memory = Neo4jMemory()
            # Verify connection
            if not await neo4j_memory.verify_connection():
                print("Warning: Neo4j connection verification failed")
                return None
        except Exception as e:
            print(f"Warning: Failed to initialize Neo4j memory: {e}")
            return None
    return neo4j_memory
