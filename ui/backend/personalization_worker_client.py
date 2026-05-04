import os
import httpx
from fastapi import HTTPException
from personalization_models import ExtractionResult

WORKER_URL = os.getenv("PERSONALIZATION_WORKER_URL", "http://localhost:8091").rstrip("/")
WORKER_SHARED_SECRET = os.getenv("PERSONALIZATION_WORKER_SECRET", "change-me")

async def extract_phrases(doc_text: str) -> ExtractionResult:
    url = f"{WORKER_URL}/extract"
    headers = {"X-Worker-Secret": WORKER_SHARED_SECRET}
    payload = {"doc_text": doc_text}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=payload, headers=headers)
        if r.status_code == 401:
            raise HTTPException(status_code=500, detail="Personalization worker auth failed")
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Personalization worker error ({r.status_code})"
            ) from e
        return ExtractionResult(**r.json())
