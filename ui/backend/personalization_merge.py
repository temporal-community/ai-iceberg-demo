import os
import re
from typing import List, Tuple

TOPIC_CAP = int(os.getenv("TOPIC_CAP", "30"))
INTEREST_CAP = int(os.getenv("INTEREST_CAP", "30"))

def _norm(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def _filter(items: List[str]) -> List[str]:
    out = []
    seen = set()
    for raw in items or []:
        if not raw:
            continue
        x = _norm(str(raw))
        if not x:
            continue
        if len(x) > 80:
            continue
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out

def merge_and_cap(existing: List[str], new: List[str], cap: int) -> List[str]:
    new_f = _filter(new)
    existing_f = _filter(existing)
    merged = new_f + [x for x in existing_f if x not in set(new_f)]
    return merged[:cap]

def merge_state(existing_topics: List[str], existing_interests: List[str],
                new_topics: List[str], new_interests: List[str]) -> Tuple[List[str], List[str]]:
    topics = merge_and_cap(existing_topics, new_topics, TOPIC_CAP)
    interests = merge_and_cap(existing_interests, new_interests, INTEREST_CAP)
    return topics, interests
