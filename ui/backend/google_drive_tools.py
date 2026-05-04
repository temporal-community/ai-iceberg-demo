import os
import httpx
from fastapi import HTTPException
from typing import Dict, Any

DOC_TEXT_MAX_CHARS = int(os.getenv("DOC_TEXT_MAX_CHARS", "50000"))
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"

async def drive_file_metadata(google_access_token: str, file_id: str) -> Dict[str, Any]:
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    params = {"fields": "id,name,mimeType,modifiedTime"}
    headers = {"Authorization": f"Bearer {google_access_token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, params=params, headers=headers)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="Drive file not found")
        r.raise_for_status()
        return r.json()

async def export_google_doc_text(google_access_token: str, file_id: str) -> str:
    # Export native Google Doc → text/plain (simple, reliable)
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
    params = {"mimeType": "text/plain"}
    headers = {"Authorization": f"Bearer {google_access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, params=params, headers=headers)
        r.raise_for_status()
        text = r.text or ""
        if len(text) > DOC_TEXT_MAX_CHARS:
            text = text[:DOC_TEXT_MAX_CHARS] + "\n...[truncated]..."
        return text
