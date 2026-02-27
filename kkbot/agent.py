"""Agent loop with tool execution."""
import asyncio
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from kkbot.config import SKILLS_DIR
from kkbot.llm import LLMProvider, LLMResponse, apply_cache
from kkbot.session import MemoryStore, Session, SessionManager

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _tool(name: str, desc: str, props: dict, required: list) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required},
    }}

TOOLS = [
    _tool("shell", "Execute a shell command and return stdout+stderr.",
          {"cmd": {"type": "string"},
           "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"}},
          ["cmd"]),
    _tool("read_file", "Read the contents of a file.",
          {"path": {"type": "string"}}, ["path"]),
    _tool("write_file", "Write content to a file (creates parent dirs if needed).",
          {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _tool("patch_file",
          "Replace exact text snippets in a file. Provide {old,new} pairs; each `old` must match exactly once.",
          {"path": {"type": "string"},
           "patches": {"type": "array", "description": "List of {old, new} replacement pairs",
                       "items": {"type": "object",
                                 "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
                                 "required": ["old", "new"]}}},
          ["path", "patches"]),
    _tool("save_memory", "Persist important facts to long-term memory.",
          {"content": {"type": "string"}}, ["content"]),
    _tool("recall_memory", "Read current long-term memory.", {}, []),
    _tool("restart_self",
          "Restart kkbot by re-executing the current process. MUST be called after modifying kkbot's own source code.",
          {}, []),
    _tool("web_search", "Search the web using Brave Search. Returns titles, URLs and snippets.",
          {"query": {"type": "string"},
           "count": {"type": "integer", "description": "Number of results (1-10, default 5)"}},
          ["query"]),
    _tool("web_fetch", "Fetch a URL and return its readable text content.",
          {"url": {"type": "string"},
           "max_chars": {"type": "integer", "description": "Max chars to return (default 8000)"}},
          ["url"]),
]

# ---------------------------------------------------------------------------
# Web tools
# ---------------------------------------------------------------------------

_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"


def _strip_html(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", html.unescape(text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _http_client(proxy: str = "", **kwargs) -> httpx.AsyncClient:
    """Return an AsyncClient, using proxy if provided or found in environment."""
    p = proxy or os.environ.get("https_proxy") or os.environ.get("http_proxy")
    return httpx.AsyncClient(proxy=p or None, **kwargs)


async def _web_search(query: str, count: int, api_key: str, proxy: str = "") -> str:
    if not api_key:
        return "Error: Brave Search API key not configured."
    n = min(max(count, 1), 10)
    try:
        async with _http_client(proxy, timeout=10.0) as client:
            r = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": n},
                headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            )
            r.raise_for_status()
        results = r.json().get("web", {}).get("results", [])
        if not results:
            return f"No results for: {query}"
        lines = [f"Search results for: {query}\n"]
        for i, item in enumerate(results[:n], 1):
            lines.append(f"{i}. {item.get('title', '')}\n   {item.get('url', '')}")
            if desc := item.get("description"):
                lines.append(f"   {desc}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


async def _web_fetch(url: str, max_chars: int, proxy: str = "") -> str:
    try:
        async with _http_client(proxy, follow_redirects=True, timeout=20.0) as client:
            r = await client.get(url, headers={"User-Agent": _USER_AGENT})
            r.raise_for_status()
        text = _strip_html(r.text)
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[truncated, {len(r.text)} chars total]"
        return text
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _patch_file(path: Path, patches: list[dict]) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"
    errors = []
    for i, p in enumerate(patches):
        old, new = p.get("old", ""), p.get("new", "")
        count = text.count(old)
        if count == 0:  errors.append(f"Patch {i}: `old` not found")
        elif count > 1: errors.append(f"Patch {i}: `old` matches {count} times (must be unique)")
        else:           text = text.replace(old, new, 1)
    if errors:
        return "Patch failed:\n" + "\n".join(errors)
    try:
        path.write_text(text, encoding="utf-8")
        return f"Patched {len(patches)} location(s) in {path}"
    except Exception as e:
        return f"Error writing file: {e}"


async def _run_tool(name: str, args: dict[str, Any],
                    workspace: Path, memory: MemoryStore,
                    brave_api_key: str = "", http_proxy: str = "") -> tuple[str, bool]:
    """Execute a tool. Returns (result, should_restart)."""

    def resolve(p: str) -> Path:
        path = Path(p).expanduser()
        return path if path.is_absolute() else workspace / path

    if name == "shell":
        cmd, timeout = args.get("cmd", ""), int(args.get("timeout", 30))
        try:
            r = await asyncio.get_event_loop().run_in_executor(
                None, lambda: subprocess.run(
                    cmd, shell=True, capture_output=True, text=True,
                    timeout=timeout, cwd=str(workspace)))
            return (r.stdout + r.stderr).strip()[:8000] or "(no output)", False
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout}s", False
        except Exception as e:
            return f"Error: {e}", False

    if name == "read_file":
        try:   return resolve(args.get("path", "")).read_text(encoding="utf-8"), False
        except Exception as e: return f"Error: {e}", False

    if name == "write_file":
        p = resolve(args.get("path", ""))
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args.get("content", ""), encoding="utf-8")
            return f"Written to {p}", False
        except Exception as e: return f"Error: {e}", False

    if name == "patch_file":
        return _patch_file(resolve(args.get("path", "")), args.get("patches", [])), False

    if name == "save_memory":
        if c := args.get("content", "").strip():
            memory.append(c)
        return "Memory saved.", False

    if name == "recall_memory":
        return memory.load() or "(no memory yet)", False

    if name == "restart_self":
        return "Restarting now...", True

    if name == "web_search":
        return await _web_search(args.get("query", ""), args.get("count", 5), brave_api_key, http_proxy), False

    if name == "web_fetch":
        return await _web_fetch(args.get("url", ""), args.get("max_chars", 8000), http_proxy), False

    return f"Unknown tool: {name}", False

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
# Agent loop
# ---------------------------------------------------------------------------

def build_user_content(text: str, images_b64: list[str]) -> Any:
    if not images_b64:
        return text
    parts: list[dict] = [{"type": "text", "text": text}] if text else []
    for b64 in images_b64:
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return parts


class AgentLoop:
    def __init__(self, provider: LLMProvider, memory: MemoryStore, sessions: SessionManager,
                 workspace: Path, system_prompt: str, max_tool_rounds: int = 20,
                 brave_api_key: str = "", http_proxy: str = ""):
        self.provider        = provider
        self.memory          = memory
        self.sessions        = sessions
        self.workspace       = workspace
        self.system_prompt   = system_prompt
        self.max_tool_rounds = max_tool_rounds
        self.brave_api_key   = brave_api_key
        self.http_proxy      = http_proxy

    def _build_system(self) -> str:
        parts = [self.system_prompt]
        if mem := self.memory.load():
            parts.append(f"## Memory\n\n{mem}")
        if skills := _load_skills():
            parts.append(skills)
        return "\n\n".join(parts)

    def _build_messages(self, session: Session, user_content: Any) -> tuple[list[dict], list[int]]:
        """Assemble messages for prefix-cache efficiency.

        [0] system: prompt + memory + skills  ← cache: stable across all turns
        [1..N] session history                ← append-only, prefix stable
        [N+1] user: runtime context           ← cache: stable within a turn's tool rounds
        [N+2] user: current message           ← NOT cached: changes every turn

        Returns (messages, cache_indices) — indices 0 and N+1 get cache_control.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        history = session.get_history()
        messages = [
            {"role": "system", "content": self._build_system()},
            *history,
            {"role": "user", "content": f"[Context]\nTime: {now}\nChat: {session.key}"},
            {"role": "user", "content": user_content},
        ]
        # system=0, context user = 1 + len(history)
        cache_indices = [0, 1 + len(history)]
        return messages, cache_indices

    async def run(self, chat_id: str, user_content: Any, on_reply: Any = None) -> str:
        session  = self.sessions.get(chat_id)
        messages, cache_indices = self._build_messages(session, user_content)
        turn_msgs: list[dict] = [{"role": "user", "content": user_content}]

        final_reply     = ""
        pending_restart = False

        for round_num in range(self.max_tool_rounds):
            resp: LLMResponse = await self.provider.chat(messages=messages, tools=TOOLS, cache_indices=cache_indices)

            if resp.finish_reason == "error":
                final_reply = resp.content or "LLM error."
                break

            asst: dict[str, Any] = {"role": "assistant", "content": resp.content or ""}
            if resp.has_tool_calls:
                asst["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.name,
                                  "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                    for tc in resp.tool_calls
                ]
            messages.append(asst)
            turn_msgs.append(asst)

            if not resp.has_tool_calls:
                final_reply = resp.content or ""
                break

            for tc in resp.tool_calls:
                logger.info("Tool: {} {}", tc.name, str(tc.arguments)[:200])
                result, restart = await _run_tool(tc.name, tc.arguments, self.workspace, self.memory, self.brave_api_key, self.http_proxy)
                if restart:
                    pending_restart = True
                logger.debug("  → {:.200}", result)
                tool_msg = {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": result}
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
