"""Bloom-filter based duplicate detection.

A Bloom filter gives O(1) "definitely new / maybe seen" checks in constant memory, which matters
if this ever runs on 50k resumes instead of 50. Because Bloom filters have false positives, a
positive is ALWAYS confirmed against an exact dict, so we never wrongly drop a candidate.
"""
from __future__ import annotations

import hashlib
import math
import re


class BloomFilter:
    def __init__(self, capacity: int = 10_000, fp_rate: float = 0.001):
        self.m = max(8, int(-capacity * math.log(fp_rate) / (math.log(2) ** 2)))   # bits
        self.k = max(1, round(self.m / capacity * math.log(2)))                   # hash fns
        self.bits = bytearray((self.m + 7) // 8)

    def _positions(self, item: str):
        d = hashlib.blake2b(item.encode(), digest_size=16).digest()
        h1, h2 = int.from_bytes(d[:8], "big"), int.from_bytes(d[8:], "big") | 1
        return ((h1 + i * h2) % self.m for i in range(self.k))      # Kirsch-Mitzenmacher

    def add(self, item: str) -> None:
        for p in self._positions(item):
            self.bits[p >> 3] |= 1 << (p & 7)

    def might_contain(self, item: str) -> bool:
        return all(self.bits[p >> 3] & (1 << (p & 7)) for p in self._positions(item))


def fingerprint(text: str) -> str:
    """Layout-insensitive content hash (case/whitespace/punctuation stripped)."""
    return hashlib.sha256(re.sub(r"[^a-z0-9]", "", text.lower()).encode()).hexdigest()


class DuplicateDetector:
    """Detects duplicate resumes by content hash or by identical e-mail address."""

    def __init__(self, capacity: int = 10_000, fp_rate: float = 0.001):
        self._bloom = BloomFilter(capacity, fp_rate)
        self._seen: dict[str, str] = {}          # key -> first file (exact confirmation)

    def check_and_add(self, file: str, text: str, email: str | None) -> str | None:
        """Return the first file this one duplicates, or None if it is new."""
        keys = [f"h:{fingerprint(text)}"] + ([f"e:{email.lower()}"] if email else [])
        for key in keys:
            if self._bloom.might_contain(key) and key in self._seen:   # bloom hit + exact confirm
                return self._seen[key]
        for key in keys:
            self._bloom.add(key)
            self._seen[key] = file
        return None
