"""Session and memory management."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from kkbot.config import MEMORY_DIR, SESSIONS_DIR


class MemoryStore:
    """Persistent long-term memory backed by MEMORY.md."""

    def __init__(self):
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.memory_file = MEMORY_DIR / "MEMORY.md"

    def load(self) -> str:
        return self.memory_file.read_text(encoding="utf-8") if self.memory_file.exists() else ""

    def write(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def append(self, content: str) -> None:
        existing = self.load().rstrip()
        self.write((existing + "\n\n" + content.strip() if existing else content.strip()) + "\n")


class Session:
    """Append-only conversation history persisted as JSONL.

    Only unconsolidated messages (from last_consolidated onward) are sent
    to the LLM, keeping the prefix stable for cache efficiency.
    """

    _KEEP = {"role", "content", "tool_calls", "tool_call_id", "name"}

    def __init__(self, key: str, path: Path):
        self.key = key
        self.path = path
        self.messages: list[dict[str, Any]] = []
        self.last_consolidated = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not (line := line.strip()):
                    continue
                data = json.loads(line)
                if data.get("_type") == "meta":
                    self.last_consolidated = data.get("last_consolidated", 0)
                else:
                    self.messages.append(data)
        except Exception as e:
            logger.warning("Failed to load session {}: {}", self.key, e)

    def _append_line(self, obj: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def get_history(self) -> list[dict[str, Any]]:
        """Return unconsolidated messages trimmed to start at a user turn."""
        msgs = self.messages[self.last_consolidated :]
        for i, m in enumerate(msgs):
            if m.get("role") == "user":
                msgs = msgs[i:]
                break
        return [{k: v for k, v in m.items() if k in self._KEEP} for m in msgs]

    def save_turn(self, msgs: list[dict[str, Any]]) -> None:
        """Append a full turn (user + assistant + tool messages) to disk."""
        ts = datetime.now().isoformat()
        for m in msgs:
            rec = {**m, "ts": ts}
            self.messages.append(rec)
            self._append_line(rec)


class SessionManager:
    """In-memory session registry backed by per-session JSONL files."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def get(self, key: str) -> Session:
        if key not in self._sessions:
            safe = key.replace(":", "_").replace("/", "_")
            self._sessions[key] = Session(key, SESSIONS_DIR / f"{safe}.jsonl")
        return self._sessions[key]
