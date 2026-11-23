"""Long-term memory: short lessons ("skills") about websites, found again by site and by meaning.

The embedding is a local hashed bag of words and word pairs (no model download, no API cost).
It finds lessons that share words with the task; swap `embed()` for a real embedding model later
without changing the database (same 256 numbers per skill).
"""
from __future__ import annotations

import hashlib
import math
import re

from webagent.store import db

_WORD = re.compile(r"[a-z0-9]+")
_STOP = set("a an the and or of to in on for with at by from is are be this that it as into your you our we "
            "find get show tell me what how which".split())


def embed(text: str, dim: int = db.EMBED_DIM) -> list[float]:
    words = [w for w in _WORD.findall((text or "").lower()) if w not in _STOP]
    vec = [0.0] * dim
    feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
    for f in feats:
        h = int(hashlib.md5(f.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0 if (h >> 8) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 5) for v in vec]


def recall(task: str, domains: list[str], limit: int = 6) -> list[dict]:
    return db.find_skills(domains, embed(task), limit=limit)


def remember(domain: str, lesson: str) -> None:
    lesson = lesson.strip()
    # Element refs (e12, d5) change on every visit: a lesson that names them misleads the next task.
    if re.search(r"\b(ref )?[ed]\d{1,4}\b", lesson):
        return
    if domain and 10 <= len(lesson) <= 400:
        db.save_skill(domain, lesson, embed(domain + " " + lesson))
