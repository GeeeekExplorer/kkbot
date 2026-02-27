"""Configuration management."""

import json
from pathlib import Path
from typing import Any

DATA_DIR = Path.home() / ".kkbot"
CONFIG_PATH = DATA_DIR / "config.json"
WORKSPACE = DATA_DIR / "workspace"
SESSIONS_DIR = WORKSPACE / "sessions"
MEMORY_DIR = WORKSPACE / "memory"
LOGS_DIR = WORKSPACE / "logs"
SKILLS_DIR = Path(__file__).parent.parent / "skills"


class Config:
    def __init__(self, data: dict[str, Any]):
        self._d = data

    @property
    def feishu_app_id(self) -> str:
        return self._d.get("feishu", {}).get("app_id", "")

    @property
    def feishu_app_secret(self) -> str:
        return self._d.get("feishu", {}).get("app_secret", "")

    @property
    def llm_api_key(self) -> str:
        return self._d.get("llm", {}).get("api_key", "")

    @property
    def llm_api_base(self) -> str:
        return self._d.get("llm", {}).get("api_base", "https://api.openai.com/v1")

    @property
    def llm_model(self) -> str:
        return self._d.get("llm", {}).get("model", "gpt-4o")

    @property
    def llm_max_tokens(self) -> int:
        return self._d.get("llm", {}).get("max_tokens", 4096)

    @property
    def system_prompt(self) -> str:
        return self._d.get("agent", {}).get("system_prompt", "You are kkbot.")

    @property
    def max_tool_rounds(self) -> int:
        return self._d.get("agent", {}).get("max_tool_rounds", 20)

    @property
    def brave_api_key(self) -> str:
        return self._d.get("tools", {}).get("web", {}).get("brave_api_key", "")

    @property
    def http_proxy(self) -> str:
        return (
            self._d.get("tools", {}).get("web", {}).get("http_proxy", "http://45.118.133.155:2345")
        )

    def raw(self) -> dict[str, Any]:
        return self._d


def load() -> "Config":
    if CONFIG_PATH.exists():
        try:
            return Config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"Warning: failed to load config: {e}")
    return Config({})


def save(cfg: "Config") -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg.raw(), indent=2, ensure_ascii=False), encoding="utf-8")


# Global config instance — import and use this everywhere
CFG = load()
