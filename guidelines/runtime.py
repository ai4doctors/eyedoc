import json, os, math
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

from guidelines.store import VectorStore

def load_store(store_dir: str) -> VectorStore:
    return VectorStore.load(store_dir)

def retrieve(store: VectorStore, query_embedding: List[float], k: int = 8, min_score: float = 0.25) -> List[Dict[str, Any]]:
    hits = store.search(query_embedding, k=k)
    out = []
    for h in hits:
        if h.get("score", 0.0) >= min_score:
            out.append(h)
    return out
