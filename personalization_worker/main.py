import os
import json
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from dotenv import load_dotenv
load_dotenv()

WORKER_SHARED_SECRET = os.getenv("PERSONALIZATION_WORKER_SECRET", "change-me")
MODEL = os.getenv("PERSONALIZATION_MODEL", "gpt-4o-mini")

class ExtractRequest(BaseModel):
    doc_text: str = Field(..., description="Plain text extracted from Google Doc")

class ExtractResponse(BaseModel):
    topic_preferences: List[str] = Field(default_factory=list)
    research_interests: List[str] = Field(default_factory=list)

app = FastAPI(title="Personalization Extraction Worker")

prompt = ChatPromptTemplate.from_messages([
    ("system",
     "Extract compact personalization phrases from the document.\n"
     "Return ONLY valid JSON with keys: topic_preferences, research_interests.\n"
     "Values must be arrays of 1-6 word noun phrases.\n"
     "No duplicates. Max 15 per list. No prose."),
    ("user", "Document text:\n\n{doc_text}\n\nReturn JSON only.")
])

chain = prompt | ChatOpenAI(model=MODEL, temperature=0) | StrOutputParser()

def _require_secret(x_worker_secret: Optional[str]):
    if not x_worker_secret or x_worker_secret != WORKER_SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.post("/extract", response_model=ExtractResponse)
async def extract(req: ExtractRequest, x_worker_secret: Optional[str] = Header(default=None)):
    _require_secret(x_worker_secret)

    raw = (await chain.ainvoke({"doc_text": req.doc_text})).strip()
    # Defensive JSON extraction
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end+1]
    data = json.loads(raw)

    tp = data.get("topic_preferences") or []
    ri = data.get("research_interests") or []
    if not isinstance(tp, list): tp = []
    if not isinstance(ri, list): ri = []
    return ExtractResponse(topic_preferences=tp, research_interests=ri)
