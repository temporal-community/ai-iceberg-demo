import os
from neo4j import GraphDatabase
from typing import Optional
from datetime import datetime, timezone
from personalization_models import PersonalizationState

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def close_driver():
    _driver.close()

def get_personalization_state(user_id: str) -> Optional[PersonalizationState]:
    cypher = """
    MATCH (s:UserPersonalization {user_id: $user_id})
    RETURN s LIMIT 1
    """
    with _driver.session() as session:
        rec = session.run(cypher, user_id=user_id).single()
        if not rec:
            return None
        s = rec["s"]
        return PersonalizationState(
            user_id=s.get("user_id"),
            email=s.get("email"),
            topic_preferences=s.get("topic_preferences") or [],
            research_interests=s.get("research_interests") or [],
            updated_at=str(s.get("updated_at")) if s.get("updated_at") else None,
            source_doc_ids=s.get("source_doc_ids") or [],
            source_doc_titles=s.get("source_doc_titles") or [],
        )

def upsert_personalization_state(state: PersonalizationState) -> PersonalizationState:
    cypher = """
    MERGE (s:UserPersonalization {user_id: $user_id})
    SET
      s.email = $email,
      s.topic_preferences = $topic_preferences,
      s.research_interests = $research_interests,
      s.source_doc_ids = $source_doc_ids,
      s.source_doc_titles = $source_doc_titles,
      s.updated_at = datetime()
    RETURN s
    """
    with _driver.session() as session:
        rec = session.run(
            cypher,
            user_id=state.user_id,
            email=state.email,
            topic_preferences=state.topic_preferences,
            research_interests=state.research_interests,
            source_doc_ids=state.source_doc_ids,
            source_doc_titles=state.source_doc_titles,
        ).single()
        s = rec["s"]
        return PersonalizationState(
            user_id=s.get("user_id"),
            email=s.get("email"),
            topic_preferences=s.get("topic_preferences") or [],
            research_interests=s.get("research_interests") or [],
            updated_at=str(s.get("updated_at")) if s.get("updated_at") else None,
            source_doc_ids=s.get("source_doc_ids") or [],
            source_doc_titles=s.get("source_doc_titles") or [],
        )
