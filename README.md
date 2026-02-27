# kkbot

A lightweight AI agent bot running on Feishu (Lark), powered by any OpenAI-compatible LLM.

## Features

- 💬 Responds in group chats (when @-mentioned) and private chats
- 🛠️ Tool use: shell, file I/O, web search (Brave), memory
- 🧠 Persistent long-term memory via `MEMORY.md`
- 📚 Extensible skills system (drop `.md` files into `skills/`)
- ⚡ Prefix cache optimised for cost efficiency

## Quick Start

### 1. Install

```bash
git clone https://github.com/GeeeekExplorer/kkbot
cd kkbot
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

### 2. Configure

```bash
kkbot init          # creates ~/.kkbot/config.json
```

Edit `~/.kkbot/config.json`:

```json
{
  "feishu": {
    "app_id": "cli_xxx",
    "app_secret": "xxx"
  },
  "llm": {
    "api_key": "sk-xxx",
    "api_base": "https://api.openai.com/v1",
    "model": "gpt-4o",
    "max_tokens": 4096
  },
  "agent": {
    "system_prompt": "You are kkbot, a helpful assistant on Feishu.",
    "max_tool_rounds": 20
  },
  "tools": {
    "web": {
      "brave_api_key": "BSA-xxx",
      "http_proxy": ""
    }
  }
}
```

### 3. Run

```bash
kkbot start
kkbot start -v    # verbose / debug logging
```

## Project Structure

```
kkbot/
├── kkbot/
│   ├── agent.py    # Agent loop, tool execution, web tools
│   ├── config.py   # Configuration & paths
│   ├── feishu.py   # Feishu WebSocket channel
│   ├── llm.py      # OpenAI-compatible LLM provider
│   ├── main.py     # CLI entry point
│   └── session.py  # Session & memory management
├── skills/         # Skill .md files injected into system prompt
│   └── gh.md       # GitHub CLI skill
└── pyproject.toml
```

## Skills

Drop any `.md` file into the `skills/` directory to extend the agent's capabilities. Skills are loaded at startup and injected into the system prompt.

Example skill file `skills/mytool.md`:
```markdown
# MyTool Skill
Instructions for using mytool...
```

## Memory

Long-term memory is stored at `~/.kkbot/workspace/memory/MEMORY.md`.  
Use the `save_memory` / `recall_memory` tools to read and write facts.

## Tools

| Tool | Description |
|------|-------------|
| `shell` | Execute shell commands |
| `read_file` | Read file contents |
| `write_file` | Write file contents |
| `patch_file` | Patch exact text snippets in a file |
| `save_memory` | Append facts to long-term memory |
| `recall_memory` | Read long-term memory |
| `web_search` | Search the web via Brave Search API |
| `web_fetch` | Fetch and extract text from a URL |
| `restart_self` | Restart the bot (after code changes) |
