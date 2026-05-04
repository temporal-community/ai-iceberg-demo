# auth0_ai_utils.py
from __future__ import annotations

from typing import List, Optional

# This is the Auth0 for AI Agents / Token Vault API
from auth0_ai_langchain.token_vault import get_access_token_from_token_vault

GOOGLE_DRIVE_SCOPES: List[str] = [
    "https://www.googleapis.com/auth/drive.readonly",
]

def get_google_drive_token() -> Optional[str]:
    """
    Fetch a delegated Google token (Drive scope) from Auth0 Token Vault.
    Returns None if the user has not connected Google (or scope missing).
    """
    token = get_access_token_from_token_vault(scopes=GOOGLE_DRIVE_SCOPES)
    return token or None
