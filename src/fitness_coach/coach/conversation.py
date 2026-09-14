"""Short rolling conversation window for multi-turn coherence.

Durable memory stays in structured tables (ConversationMemory, commitments, injuries).
This window only gives the model the last few turns so clarifications and follow-ups
read naturally; it is process-local, small, and expires after inactivity.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True, slots=True)
class Turn:
    role: str
    text: str
    at: datetime


class ConversationWindow:
    def __init__(self, *, max_turns: int = 8, ttl: timedelta = timedelta(hours=2)) -> None:
        self.max_turns = max_turns
        self.ttl = ttl
        self._turns: dict[str, deque[Turn]] = {}
        self._lock = threading.Lock()

    def append(self, user_id: str, role: str, text: str, *, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        with self._lock:
            turns = self._turns.setdefault(user_id, deque(maxlen=self.max_turns))
            self._expire(turns, now)
            turns.append(Turn(role=role, text=text.strip(), at=now))

    def recent(self, user_id: str, *, now: datetime | None = None) -> list[Turn]:
        now = now or datetime.now(UTC)
        with self._lock:
            turns = self._turns.get(user_id)
            if not turns:
                return []
            self._expire(turns, now)
            return list(turns)

    def clear(self, user_id: str) -> None:
        with self._lock:
            self._turns.pop(user_id, None)

    def _expire(self, turns: deque[Turn], now: datetime) -> None:
        if turns and now - turns[-1].at > self.ttl:
            turns.clear()

    def format(self, user_id: str, *, max_chars: int = 400) -> str:
        turns = self.recent(user_id)
        if not turns:
            return ""
        lines = ["# Recent Conversation (this session only, newest last)", ""]
        for turn in turns:
            text = turn.text if len(turn.text) <= max_chars else turn.text[:max_chars] + "…"
            lines.append(f"{'User' if turn.role == 'user' else 'Coach'}: {text}")
        return "\n".join(lines)
