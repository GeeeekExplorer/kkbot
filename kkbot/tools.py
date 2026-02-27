"""Tool definitions and execution."""

import asyncio
import html
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx

from kkbot.config import CFG, WORKSPACE
from kkbot.session import MemoryStore

# ---------------------------------------------------------------------------
# Tool schema definitions
# ---------------------------------------------------------------------------


def _tool(name: str, desc: str, props: dict, required: list) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


TOOLS = [
    _tool(
        "shell",
        "Execute a shell command and return stdout+stderr.",
        {
            "cmd": {"type": "string"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"},
        },
        ["cmd"],
    ),
    _tool("read_file", "Read the contents of a file.", {"path": {"type": "string"}}, ["path"]),
    _tool(
        "write_file",
        "Write content to a file (creates parent dirs if needed).",
        {"path": {"type": "string"}, "content": {"type": "string"}},
        ["path", "content"],
    ),
    _tool(
        "edit_file",
        "Replace one exact occurrence of `old` with `new` in a file. `old` must match exactly once.",
        {
            "path": {"type": "string"},
            "old": {
                "type": "string",
                "description": "Exact text to replace (must be unique in file)",
            },
            "new": {"type": "string", "description": "Replacement text"},
        },
        ["path", "old", "new"],
    ),
    _tool(
        "save_memory",
        "Persist important facts to long-term memory.",
        {"content": {"type": "string"}},
        ["content"],
    ),
    _tool("recall_memory", "Read current long-term memory.", {}, []),
    _tool(
        "restart_self",
        "Restart kkbot by re-executing the current process. MUST be called after modifying kkbot's own source code.",
        {},
        [],
    ),
    _tool(
        "web_search",
        "Search the web using Brave Search. Returns titles, URLs and snippets.",
        {
            "query": {"type": "string"},
            "count": {"type": "integer", "description": "Number of results (1-10, default 5)"},
        },
        ["query"],
    ),
    _tool(
        "web_fetch",
        "Fetch a URL and return its readable text content.",
        {
            "url": {"type": "string"},
            "max_chars": {"type": "integer", "description": "Max chars to return (default 8000)"},
        },
        ["url"],
    ),
]

# ---------------------------------------------------------------------------
# Web helpers
# ---------------------------------------------------------------------------

_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"


def _http_client(**kwargs) -> httpx.AsyncClient:
    proxy = CFG.http_proxy or os.environ.get("https_proxy") or os.environ.get("http_proxy")
    return httpx.AsyncClient(proxy=proxy or None, **kwargs)


def _strip_html(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", html.unescape(text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


async def _web_search(query: str, count: int) -> str:
    if not CFG.brave_api_key:
        return "Error: Brave Search API key not configured."
    n = min(max(count, 1), 10)
    try:
        async with _http_client(timeout=10.0) as client:
            r = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": n},
                headers={"Accept": "application/json", "X-Subscription-Token": CFG.brave_api_key},
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


async def _web_fetch(url: str, max_chars: int) -> str:
    try:
        async with _http_client(follow_redirects=True, timeout=20.0) as client:
            r = await client.get(url, headers={"User-Agent": _USER_AGENT})
            r.raise_for_status()
        text = _strip_html(r.text)
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[truncated, {len(r.text)} chars total]"
        return text
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def _resolve(p: str) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else WORKSPACE / path


def _edit_file(path: Path, old: str, new: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"
    count = text.count(old)
    if count == 0:
        return "Error: `old` not found in file"
    if count > 1:
        return f"Error: `old` matches {count} times (must be unique)"
    try:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"Edited {path}"
    except Exception as e:
        return f"Error writing file: {e}"


# ---------------------------------------------------------------------------
# Tool executor
# ---------------------------------------------------------------------------

_memory = MemoryStore()


async def run_tool(name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Execute a tool. Returns (result, should_restart)."""

    if name == "shell":
        cmd, timeout = args.get("cmd", ""), int(args.get("timeout", 30))
        try:
            r = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=str(WORKSPACE),
                ),
            )
            return (r.stdout + r.stderr).strip()[:8000] or "(no output)", False
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout}s", False
        except Exception as e:
            return f"Error: {e}", False

    if name == "read_file":
        try:
            return _resolve(args.get("path", "")).read_text(encoding="utf-8"), False
        except Exception as e:
            return f"Error: {e}", False

    if name == "write_file":
        p = _resolve(args.get("path", ""))
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args.get("content", ""), encoding="utf-8")
            return f"Written to {p}", False
        except Exception as e:
            return f"Error: {e}", False

    if name == "edit_file":
        return _edit_file(
            _resolve(args.get("path", "")), args.get("old", ""), args.get("new", "")
        ), False

    if name == "save_memory":
        if c := args.get("content", "").strip():
            _memory.append(c)
        return "Memory saved.", False

    if name == "recall_memory":
        return _memory.load() or "(no memory yet)", False

    if name == "restart_self":
        return "Restarting now...", True

    if name == "web_search":
        return await _web_search(args.get("query", ""), args.get("count", 5)), False

    if name == "web_fetch":
        return await _web_fetch(args.get("url", ""), args.get("max_chars", 8000)), False

    return f"Unknown tool: {name}", False
