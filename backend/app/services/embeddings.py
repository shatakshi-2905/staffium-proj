"""Text embeddings with BAAI/bge-small-en-v1.5 (384 dimensions).

Runs locally through fastembed (ONNX Runtime) rather than sentence-transformers/PyTorch: same
model and vectors, a fraction of the memory, which matters on a 512 MB hosting instance.

The "hash" provider is a deterministic stand-in for tests and offline development only; the
settings refuse it in production.
"""
import asyncio
import hashlib
import math
import re
import threading

from ..config import get_settings

DIMENSIONS = 384
MODEL_VERSION = "v1.5"
# bge models expect this instruction on the *query* side of retrieval (not on documents).
_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

_model = None
_lock = threading.Lock()


def _fastembed_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from fastembed import TextEmbedding
                s = get_settings()
                _model = TextEmbedding(s.embedding_model, cache_dir=s.embedding_cache_dir, threads=1)
    return _model


def _hash_embed(text: str) -> list[float]:
    vec = [0.0] * DIMENSIONS
    for token in re.findall(r"[a-z0-9+#.]+", text.lower()):
        h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "big")
        vec[h % DIMENSIONS] += 1.0 if (h >> 9) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _embed_sync(text: str) -> list[float]:
    s = get_settings()
    text = text[:4000]
    if s.embeddings_provider == "hash":
        return _hash_embed(text)
    vector = next(iter(_fastembed_model().embed([text])))
    return [float(x) for x in vector]


async def embed_passage(text: str) -> list[float]:
    return await asyncio.to_thread(_embed_sync, text)


async def embed_query(text: str) -> list[float]:
    s = get_settings()
    prefix = "" if s.embeddings_provider == "hash" else _QUERY_INSTRUCTION
    return await asyncio.to_thread(_embed_sync, prefix + text)


def model_name() -> str:
    s = get_settings()
    return s.embedding_model if s.embeddings_provider == "fastembed" else "hash-dev"


def warm_up() -> None:
    """Load the model at startup so the first upload or search isn't slow."""
    if get_settings().embeddings_provider == "fastembed":
        _embed_sync("warm up")


def resume_representation(parsed: dict, text: str) -> str:
    """The compact text that gets embedded for a resume. bge-small reads ~512 tokens, so the
    most signal-dense parts go first and the (already sanitised) resume text fills the remainder.
    Company names are left out of the structured part on purpose: employer prestige isn't job fit."""
    parts = []
    if parsed.get("headline"):
        parts.append(str(parsed["headline"]))
    if parsed.get("summary"):
        parts.append(str(parsed["summary"]))
    skills = parsed.get("matched_skills") or parsed.get("skills")   # canonical names: "Postgres" -> "PostgreSQL"
    if skills:
        parts.append("Skills: " + ", ".join(map(str, skills[:60])))
    for exp in (parsed.get("experience") or [])[:6]:
        parts.append(f"{exp.get('title') or ''}. {exp.get('summary') or ''}")
    for proj in (parsed.get("projects") or [])[:5]:
        parts.append(f"Project: {proj.get('title') or ''} ({', '.join(proj.get('technologies') or [])})")
    head = "\n".join(p for p in parts if p.strip())
    return (head + "\n" + text)[:3000]


def job_representation(title: str, description: str, skills: list[str]) -> str:
    return f"{title}\nSkills: {', '.join(skills)}\n{description}"[:3000]
