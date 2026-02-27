"""Main entry point — Feishu bot daemon."""

import argparse
import asyncio
import sys
from datetime import datetime

from loguru import logger

import kkbot.config as cfg
from kkbot import agent, feishu, llm, session


def _setup_logging(verbose: bool = False) -> None:
    fmt = "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO", format=fmt)
    cfg.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = cfg.LOGS_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logger.add(
        str(log_file),
        level="DEBUG" if verbose else "INFO",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    )
    logger.info("Log file: {}", log_file)


def cmd_init(args: argparse.Namespace) -> int:
    config = cfg.load()
    cfg.save(config)
    print(f"Config written to: {cfg.CONFIG_PATH}")
    print("Edit it to add your Feishu app_id/secret and LLM api_key.")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    config = cfg.load()
    _setup_logging(args.verbose)

    if not config.feishu_app_id or not config.feishu_app_secret:
        logger.error("Feishu credentials not configured. Run: kkbot init")
        return 1
    if not config.llm_api_key:
        logger.error("LLM api_key not configured. Run: kkbot init")
        return 1

    bot = feishu.FeishuBot(config.feishu_app_id, config.feishu_app_secret)
    ag = agent.AgentLoop(
        provider=llm.LLMProvider(
            config.llm_api_key, config.llm_api_base, config.llm_model, config.llm_max_tokens
        ),
        memory=session.MemoryStore(),
        sessions=session.SessionManager(),
        system_prompt=config.system_prompt,
        max_tool_rounds=config.max_tool_rounds,
    )

    async def on_message(sender_id: str, chat_id: str, text: str, images_b64: list[str]) -> None:
        logger.info("Message from {}: {:.80}", sender_id, text.replace("\n", " "))
        content = agent.build_user_content(f"[sender_open_id:{sender_id}]\n{text}", images_b64)
        await ag.run(f"feishu:{chat_id}", content, on_reply=lambda reply: bot.send(chat_id, reply))

    bot.set_handler(on_message)
    logger.info("Starting kkbot...")
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        asyncio.run(bot.stop())
    return 0


def cli() -> int:
    parser = argparse.ArgumentParser(prog="kkbot", description="Feishu agent bot")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create default config").set_defaults(func=cmd_init)
    sub.add_parser("start", help="Start Feishu bot daemon").set_defaults(func=cmd_start)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(cli())
