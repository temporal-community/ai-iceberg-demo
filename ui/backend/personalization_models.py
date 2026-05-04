from pydantic import BaseModel, Field
from typing import List, Optional

class PersonalizationState(BaseModel):
    user_id: str
    email: Optional[str] = None
    topic_preferences: List[str] = Field(default_factory=list)
    research_interests: List[str] = Field(default_factory=list)
    updated_at: Optional[str] = None
    source_doc_ids: List[str] = Field(default_factory=list)
    source_doc_titles: List[str] = Field(default_factory=list)

class UpdateFromGoogleDocRequest(BaseModel):
    drive_file_id: str = Field(..., description="Google Drive fileId (Google Doc)")

class ExtractionResult(BaseModel):
    topic_preferences: List[str] = Field(default_factory=list)
    research_interests: List[str] = Field(default_factory=list)
