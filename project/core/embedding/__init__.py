"""Bundled embedding model (the ONLY model shipped inside the project).

Hidden from the UI. Lives in project/embedding_model/ (single bundled
folder, offline-first), device (GPU/CPU) auto-detected once at startup.
Everything else connects through the Universal Connection Engine (AI Models).
"""
from core.embedding.service import embed_query, embed_texts, is_ready

__all__ = ["embed_texts", "embed_query", "is_ready"]
