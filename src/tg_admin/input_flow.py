"""Temporary ForceReply input tracking (one authorized private chat only)."""
from __future__ import annotations

from dataclasses import dataclass
import time

# This is deliberately much shorter than the normal login session.
INPUT_TTL = 120


@dataclass(frozen=True)
class PendingInput:
    kind: str
    prompt_id: int
    expires_at: float

    def matches(self, reply_to_id: int | None, *, now: float | None = None) -> bool:
        if now is None:
            now = time.monotonic()
        return reply_to_id == self.prompt_id and now < self.expires_at

    @classmethod
    def make(cls, kind: str, prompt_id: int, *, now: float | None = None, ttl: int = INPUT_TTL):
        if now is None:
            now = time.monotonic()
        if kind not in {"login", "run", "sudo", "approve"}:
            raise ValueError("unsupported input kind")
        return cls(kind, prompt_id, now + ttl)
