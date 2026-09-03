"""Embedding service — text vector generation with TF-IDF fallback.

Uses Ollama's embeddings API if available, otherwise falls back to
a simple TF-IDF approach using sklearn.
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from config import Settings

log = logging.getLogger(__name__)


class EmbeddingService:
    """Service for generating and comparing text embeddings."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model = None
        self._idf: Dict[str, float] = {}
        self._vocabulary: Dict[str, int] = {}
        self._doc_count = 0

    async def embed(self, text: str) -> Optional[List[float]]:
        """Generate an embedding vector for text."""
        # Try Ollama first
        try:
            from agent_system.brain.ollama import OllamaClient
            client = OllamaClient(self._settings)
            embedding = await client.embed(text)
            if embedding is not None:
                return embedding
        except Exception:
            pass

        # Fallback: TF-IDF vector
        return self._tfidf_embed(text)

    def _tfidf_embed(self, text: str) -> List[float]:
        """Simple TF-IDF embedding fallback."""
        tokens = self._tokenize(text)

        # Build vocabulary if needed
        for token in tokens:
            if token not in self._vocabulary:
                self._vocabulary[token] = len(self._vocabulary)

        # TF (term frequency)
        tf: Dict[str, float] = {}
        for token in tokens:
            tf[token] = tf.get(token, 0) + 1.0
        max_tf = max(tf.values()) if tf else 1.0
        for token in tf:
            tf[token] = 0.5 + 0.5 * (tf[token] / max_tf)

        # TF-IDF vector (sparse -> dense with fixed size)
        vector_size = min(len(self._vocabulary), 256)
        vector = [0.0] * vector_size

        for token, tf_val in tf.items():
            idf = self._idf.get(token, 1.0)
            idx = self._vocabulary.get(token, 0) % vector_size
            vector[idx] += tf_val * idf

        # Normalize
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]

        return vector

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Simple tokenization."""
        return text.lower().split()

    def update_idf(self, documents: List[str]) -> None:
        """Update IDF scores from a corpus of documents."""
        self._doc_count += len(documents)
        doc_freq: Dict[str, int] = {}

        for doc in documents:
            tokens = set(self._tokenize(doc))
            for token in tokens:
                doc_freq[token] = doc_freq.get(token, 0) + 1

        for token, freq in doc_freq.items():
            self._idf[token] = math.log((self._doc_count + 1) / (freq + 1)) + 1.0

    @staticmethod
    def cosine_similarity(a: List[float], b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not a or not b:
            return 0.0

        min_len = min(len(a), len(b))
        a = a[:min_len]
        b = b[:min_len]

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot / (norm_a * norm_b)