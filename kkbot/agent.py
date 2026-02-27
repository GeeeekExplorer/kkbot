"""Agent loop."""

import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Any

from loguru import logger

from kkbot.config import SKILLS_DIR
from kkbot.llm import LLMProvider, LLMResponse
from kkbot.session import MemoryStore, Session, SessionManager
from kkbot.tools import TOOLS, run_tool

# ---------------------------------------------------------------------------
# Skills loader
# ---------------------------------------------------------------------------


def _load_skills() -> str:
    if not SKILLS_DIR.exists():
        return ""
    parts = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        try:
            if content := path.read_text(encoding="utf-8").strip():
                parts.append(f"### Skill: {path.stem}\n{content}")
        except Exception as e:
            logger.warning("Failed to load skill {}: {}", path.name, e)
    if not parts:
        return ""
    logger.info("Loaded {} skill(s)", len(parts))
    return "## Skills\n\n" + "\n\n".join(parts)


# ---------------------------------------------------------------------------
# User content builder
# ---------------------------------------------------------------------------


def build_user_content(text: str, images_b64: list[str]) -> Any:
    if not images_b64:
        return text
    parts: list[dict] = [{"type": "text", "text": text}] if text else []
    for b64 in images_b64:
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return parts


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


class AgentLoop:
    def __init__(
        self,
        provider: LLMProvider,
        memory: MemoryStore,
        sessions: SessionManager,
        system_prompt: str,
        max_tool_rounds: int = 20,
    ):
        self.provider = provider
        self.memory = memory
        self.sessions = sessions
        self.system_prompt = system_prompt
        self.max_tool_rounds = max_tool_rounds

    def _build_system(self) -> str:
        parts = [self.system_prompt]
        if mem := self.memory.load():
            parts.append(f"## Memory\n\n{mem}")
        if skills := _load_skills():
            parts.append(skills)
        return "\n\n".join(parts)

    def _build_messages(self, session: Session, user_content: Any) -> list[dict]:
        """Assemble messages for prefix-cache efficiency.

        [0]    system: prompt + memory + skills  ← stable across turns
        [1..N] session history                   ← append-only, prefix stable
        [N+1]  user: runtime context + message   ← last user, gets cache_control in llm.py
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        return [
            {"role": "system", "content": self._build_system()},
            *session.get_history(),
            {"role": "user", "content": f"[Context]\nTime: {now}\nChat: {session.key}"},
            {"role": "user", "content": user_content},
        ]

    async def run(self, chat_id: str, user_content: Any, on_reply: Any = None) -> str:
        session = self.sessions.get(chat_id)
        messages = self._build_messages(session, user_content)
        turn_msgs: list[dict] = [{"role": "user", "content": user_content}]

        final_reply = ""
        pending_restart = False

        for round_num in range(self.max_tool_rounds):
            resp: LLMResponse = await self.provider.chat(messages=messages, tools=TOOLS)

            if resp.finish_reason == "error":
                final_reply = resp.content or "LLM error."
                break

            asst: dict[str, Any] = {"role": "assistant", "content": resp.content or ""}
            if resp.has_tool_calls:
                asst["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in resp.tool_calls
                ]
            messages.append(asst)
            turn_msgs.append(asst)

            if not resp.has_tool_calls:
                final_reply = resp.content or ""
                break

            for tc in resp.tool_calls:
                logger.info("Tool: {} {}", tc.name, str(tc.arguments)[:200])
                result, restart = await run_tool(tc.name, tc.arguments)
                if restart:
                    pending_restart = True
                logger.debug("  → {:.200}", result)
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": result,
                }
                messages.append(tool_msg)
                turn_msgs.append(tool_msg)

            if round_num == self.max_tool_rounds - 1:
                logger.warning("Max tool rounds reached")
                final_reply = "Reached maximum tool call rounds."

        session.save_turn(turn_msgs)

        if final_reply and on_reply:
            await on_reply(final_reply)
        if pending_restart:
            await asyncio.sleep(1)
            logger.info("Restarting kkbot via os.execv...")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        return final_reply
