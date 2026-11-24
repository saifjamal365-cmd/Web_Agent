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

# Any language's letters (was [a-z0-9]: an Arabic task embedded to all zeros and recalled nothing).
_WORD = re.compile(r"[^\W_]+")
_STOP = set("a an the and or of to in on for with at by from is are be this that it as into your you our we "
            "find get show tell me what how which "
            "في من على إلى الى عن مع هذا هذه ذلك التي الذي و أو او ما ماذا كيف هل لي أريد اريد".split())
_TASHKEEL = re.compile(r"[ً-ْـ]")


def _arabic_norm(text: str) -> str:
    """One spelling for the letters people write in several ways (أ/إ/آ → ا, ى → ي, ة → ه), no short vowels."""
    text = _TASHKEEL.sub("", text)
    return text.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"}))


def embed(text: str, dim: int = db.EMBED_DIM) -> list[float]:
    words = [w for w in _WORD.findall(_arabic_norm((text or "").lower())) if w not in _STOP]
    # "the" in Arabic is a prefix (ال): drop it so "السعر" and "سعر" are the same word.
    words = [w[2:] if w.startswith("ال") and len(w) > 4 else w for w in words]
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
