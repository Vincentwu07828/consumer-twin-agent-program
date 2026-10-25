from typing import Protocol
from .storage import EventStore


class MemoryBackend(Protocol):
    def retrieve(self, user_id: str, query: str, top_k: int = 5, source: str = "real") -> list[dict]: ...


class MemoryStore:
    """Deterministic local fallback. Replace via MemoryBackend for embedding retrieval."""
    def __init__(self, store: EventStore):
        self.store = store

    def retrieve(self, user_id, query, top_k=5, source="real"):
        if top_k < 0:
            raise ValueError("top_k 必须非负")
        tokens = set(query.lower().split())
        memories = self.store.memories(user_id, source)
        ranked = sorted(enumerate(memories), key=lambda pair: (
            sum(token in pair[1].get("text", "").lower() for token in tokens), pair[0]), reverse=True)
        return [memory for _, memory in ranked[:top_k]]
