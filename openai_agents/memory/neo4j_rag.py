"""
Neo4j RAG Module for Knowledge Graph Vector Search
==================================================
Provides RAG (Retrieval Augmented Generation) capabilities using Neo4j vector indexes.

This module:
1. Generates embeddings for Result nodes' markdown content
2. Stores embeddings in Neo4j
3. Creates/manages vector indexes
4. Provides semantic search over the knowledge graph

Note: This is a simple, focused implementation for vector search over Result nodes.
For more advanced features (entity extraction, graph construction pipelines, complex
graph traversals), consider migrating to the official neo4j-graphrag package.

Environment Variables:
- NEO4J_URI: Neo4j connection URI (default: bolt://localhost:7687)
- NEO4J_USER: Neo4j username (default: neo4j)
- NEO4J_PASSWORD: Neo4j password (required)
- OPENAI_API_KEY: OpenAI API key for generating embeddings (required)
"""

import os
from typing import List, Optional, Tuple

from dotenv import load_dotenv
from neo4j import AsyncGraphDatabase

load_dotenv()

# Neo4j connection settings
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# OpenAI settings
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Vector index configuration
VECTOR_INDEX_NAME = "result_embeddings_index"
EMBEDDING_DIMENSION = 1536  # OpenAI text-embedding-3-small dimension
CHUNK_SIZE = 1000  # Characters per chunk
CHUNK_OVERLAP = 200  # Overlap between chunks


class Neo4jRAG:
    """RAG interface for Neo4j knowledge graph using vector embeddings"""

    def __init__(
        self,
        uri: str = NEO4J_URI,
        user: str = NEO4J_USER,
        password: Optional[str] = NEO4J_PASSWORD,
        openai_api_key: Optional[str] = OPENAI_API_KEY,
    ):
        if not password:
            raise ValueError("NEO4J_PASSWORD environment variable is required")
        if not openai_api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required for RAG")
        self.driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        self.openai_api_key = openai_api_key

    async def close(self):
        """Close the Neo4j driver connection"""
        await self.driver.close()

    async def _generate_embedding(self, text: str) -> List[float]:
        """
        Generate an embedding for the given text using OpenAI.

        Args:
            text: Text to generate embedding for

        Returns:
            List of floats representing the embedding vector
        """
        try:
            # Import OpenAI client
            from openai import OpenAI

            client = OpenAI(api_key=self.openai_api_key)
            response = client.embeddings.create(
                model="text-embedding-3-small",  # 1536 dimensions
                input=text,
            )
            return response.data[0].embedding
        except ImportError:
            raise ImportError(
                "openai package is required for embeddings. Install with: pip install openai"
            )
        except Exception as e:
            raise Exception(f"Failed to generate embedding: {e}")

    def _chunk_text(
        self, text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
    ) -> List[str]:
        """
        Split text into overlapping chunks.

        Args:
            text: Text to chunk
            chunk_size: Maximum size of each chunk
            overlap: Number of characters to overlap between chunks

        Returns:
            List of text chunks
        """
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            chunks.append(chunk)
            start = end - overlap  # Overlap for context continuity
        return chunks

    async def create_vector_index(self) -> bool:
        """
        Create the vector index in Neo4j if it doesn't exist.

        Returns:
            True if index was created or already exists, False on error
        """
        async with self.driver.session() as session:
            try:
                # Check if index already exists
                result = await session.run(
                    """
                    SHOW INDEXES
                    YIELD name, type, entityType, labelsOrTypes, properties
                    WHERE name = $index_name
                    RETURN name
                    """,
                    index_name=VECTOR_INDEX_NAME,
                )
                existing = await result.single()
                if existing:
                    print(f"INFO: Vector index '{VECTOR_INDEX_NAME}' already exists")
                    return True

                # Create vector index
                # Note: Neo4j vector index syntax may vary by version
                # This uses the syntax for Neo4j 5.x
                # Using string concatenation to avoid linter issues with f-strings
                index_query = (
                    "CREATE VECTOR INDEX "
                    + VECTOR_INDEX_NAME
                    + " IF NOT EXISTS FOR (r:Result) ON r.embedding "
                    "OPTIONS { indexConfig: { `vector.dimensions`: "
                    + str(EMBEDDING_DIMENSION)
                    + ", `vector.similarity_function`: 'cosine' } }"
                )
                await session.run(index_query)  # type: ignore[arg-type]
                print(f"INFO: ✅ Created vector index '{VECTOR_INDEX_NAME}'")
                return True
            except Exception as e:
                print(f"ERROR: Failed to create vector index: {e}")
                return False

    async def index_result_node(self, result_id: str, markdown_report: str) -> bool:
        """
        Generate embeddings for a Result node's markdown content and store in Neo4j.

        This chunks the markdown report and creates embeddings for each chunk,
        storing them as separate properties or as a single embedding for the full text.

        Args:
            result_id: The result_id of the Result node
            markdown_report: The markdown content to embed

        Returns:
            True if successful, False otherwise
        """
        if not markdown_report or not markdown_report.strip():
            print(
                f"WARNING: Empty markdown report for result {result_id}, skipping embedding"
            )
            return False

        async with self.driver.session() as session:
            try:
                # For simplicity, we'll embed the full markdown report
                # For very long reports, you might want to chunk and store multiple embeddings
                embedding = await self._generate_embedding(markdown_report)
                print(
                    f"INFO: Generated embedding for result {result_id} (dimension: {len(embedding)})"
                )

                # Store embedding on the Result node
                result = await session.run(
                    """
                    MATCH (r:Result {result_id: $result_id})
                    SET r.embedding = $embedding
                    RETURN r.result_id
                    """,
                    result_id=result_id,
                    embedding=embedding,
                )
                record = await result.single()
                if record:
                    print(f"INFO: ✅ Stored embedding for result {result_id}")
                    return True
                else:
                    print(f"WARNING: Result node {result_id} not found")
                    return False
            except Exception as e:
                print(f"ERROR: Failed to index result node {result_id}: {e}")
                import traceback

                traceback.print_exc()
                return False

    async def search_similar_results(
        self, query: str, limit: int = 5, min_score: float = 0.7
    ) -> List[Tuple[dict, float]]:
        """
        Search for similar Result nodes using vector similarity.

        Args:
            query: Search query text
            limit: Maximum number of results to return
            min_score: Minimum similarity score (0-1, cosine similarity)

        Returns:
            List of tuples containing (result_node_dict, similarity_score)
        """
        print(
            f"INFO: 🔍 search_similar_results() called with query: '{query[:50]}...', limit: {limit}, min_score: {min_score}"
        )
        async with self.driver.session() as session:
            try:
                # First, check if there are any Result nodes with embeddings
                check_result = await session.run(
                    "MATCH (r:Result) WHERE r.embedding IS NOT NULL RETURN count(r) as count"
                )
                check_record = await check_result.single()
                node_count = check_record["count"] if check_record else 0
                print(f"INFO: Found {node_count} Result nodes with embeddings")

                if node_count == 0:
                    print(
                        "INFO: ⚠️ No Result nodes with embeddings found, skipping vector search"
                    )
                    return []

                # Generate embedding for the query
                print(f"INFO: 📊 Generating embedding for query...")
                query_embedding = await self._generate_embedding(query)
                print(
                    f"INFO: ✅ Generated query embedding (dimension: {len(query_embedding)})"
                )

                # Search using vector index
                # Note: The index name must be a string literal in the CALL statement
                index_name_literal = (
                    VECTOR_INDEX_NAME  # This is a constant, safe to use in query
                )
                search_query = (
                    f"CALL db.index.vector.queryNodes('{index_name_literal}', $limit, $query_embedding) "
                    "YIELD node, score WHERE score >= $min_score "
                    "RETURN node, score ORDER BY score DESC"
                )
                print(
                    f"INFO: 🔎 Executing vector search query with index '{VECTOR_INDEX_NAME}'"
                )
                print(f"INFO: Query: {search_query}")
                print(
                    f"INFO: Parameters: limit={limit}, min_score={min_score}, embedding_dim={len(query_embedding)}"
                )

                result = await session.run(  # type: ignore[arg-type]
                    search_query,
                    limit=limit,
                    query_embedding=query_embedding,
                    min_score=min_score,
                )

                results = []
                record_count = 0
                async for record in result:
                    node = record["node"]
                    score = record["score"]
                    results.append((dict(node), float(score)))
                    record_count += 1
                    print(
                        f"INFO:   Match {record_count}: score={score:.4f}, title={node.get('title', 'N/A')[:30]}..."
                    )

                print(
                    f"INFO: ✅ Vector search completed: Found {len(results)} similar results for query: '{query[:50]}...'"
                )
                if len(results) == 0:
                    print(f"INFO: ⚠️ No results found above threshold {min_score}")
                return results
            except Exception as e:
                print(f"ERROR: ❌ Failed to search similar results: {e}")
                import traceback

                traceback.print_exc()
                return []

    async def get_best_match(
        self, query: str, min_score: float = 0.8
    ) -> Optional[Tuple[dict, float]]:
        """
        Get the best matching Result node if similarity is above threshold.

        Args:
            query: Search query
            min_score: Minimum similarity score (default 0.8 for high confidence)

        Returns:
            Tuple of (result_node_dict, similarity_score) if found, None otherwise
        """
        print(
            f"INFO: get_best_match() called with query: '{query[:50]}...', min_score: {min_score}"
        )
        similar_results = await self.search_similar_results(
            query, limit=1, min_score=min_score
        )
        print(f"INFO: get_best_match() found {len(similar_results)} results")
        if similar_results and len(similar_results) > 0:
            print(
                f"INFO: ✅ Returning best match with score: {similar_results[0][1]:.2f}"
            )
            return similar_results[0]
        print(f"INFO: ❌ No match found above threshold {min_score}")
        return None

    async def get_relevant_context(
        self, query: str, limit: int = 3, min_score: float = 0.5
    ) -> str:
        """
        Get relevant context from the knowledge graph for a query.

        This is a convenience method that searches for similar results and
        formats them as context text for use in prompts.

        Args:
            query: Search query
            limit: Maximum number of results to include
            min_score: Minimum similarity score (default 0.5 for medium confidence)

        Returns:
            Formatted context string with relevant information
        """
        similar_results = await self.search_similar_results(
            query, limit=limit, min_score=min_score
        )

        if not similar_results:
            return ""

        context_parts = []
        for node_dict, score in similar_results:
            title = node_dict.get("title", "Untitled")
            short_summary = node_dict.get("short_summary", "")
            markdown_report = node_dict.get("markdown_report", "")

            # Truncate markdown if too long
            if len(markdown_report) > 2000:
                markdown_report = markdown_report[:2000] + "..."

            context_parts.append(
                f"Title: {title}\n"
                f"Summary: {short_summary}\n"
                f"Content: {markdown_report}\n"
                f"(Similarity: {score:.2f})"
            )

        context = "\n\n---\n\n".join(context_parts)
        return f"Relevant information from previous research:\n\n{context}"

    async def verify_connection(self) -> bool:
        """Verify Neo4j connection"""
        try:
            async with self.driver.session() as session:
                result = await session.run("RETURN 1 as test")
                await result.single()
                return True
        except Exception as e:
            print(f"ERROR: Neo4j connection failed: {e}")
            return False


# Global RAG instance cache
_neo4j_rag_instance: Optional[Neo4jRAG] = None


async def get_neo4j_rag() -> Optional[Neo4jRAG]:
    """
    Get or create a Neo4jRAG instance.

    Returns:
        Neo4jRAG instance if configured, None otherwise
    """
    global _neo4j_rag_instance

    # Return cached instance if available
    if _neo4j_rag_instance is not None:
        return _neo4j_rag_instance

    if not NEO4J_PASSWORD or not OPENAI_API_KEY:
        print("INFO: Neo4j RAG disabled - NEO4J_PASSWORD or OPENAI_API_KEY not set")
        return None

    try:
        print(f"INFO: 🔧 Initializing Neo4j RAG connection to {NEO4J_URI}")
        rag = Neo4jRAG()
        # Verify connection
        if not await rag.verify_connection():
            print("ERROR: Neo4j RAG connection verification failed")
            return None
        # Ensure vector index exists
        await rag.create_vector_index()
        print("INFO: ✅ Neo4j RAG initialized successfully")
        _neo4j_rag_instance = rag  # Cache the instance
        return rag
    except Exception as e:
        print(f"ERROR: Failed to initialize Neo4j RAG: {e}")
        import traceback

        traceback.print_exc()
        return None
