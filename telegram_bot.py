#!/usr/bin/env python3
"""
DarkMatter Traffic Bot - Telegram Controller Entry Point

Allows standalone remote management of DarkMatterBot via Telegram.
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.telegram_bot import TelegramBotController


def setup_logging(level: str = "INFO") -> logging.Logger:
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)-16s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger("dm-telegram")


def parse_args():
    parser = argparse.ArgumentParser(description="DarkMatter Telegram Bot Controller")
    parser.add_argument(
        "--token",
        "-t",
        default=os.environ.get("DM_TELEGRAM_BOT_TOKEN", ""),
        help="Telegram Bot Token from @BotFather",
    )
    parser.add_argument(
        "--chat-id",
        "-c",
        default=os.environ.get("DM_TELEGRAM_CHAT_ID", ""),
        help="Allowed Telegram Chat ID for security",
    )
    parser.add_argument(
        "--log-level",
        "-l",
        default=os.environ.get("DM_LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level",
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    logger = setup_logging(args.log_level)

    if not args.token:
        logger.error("Missing Telegram Bot Token. Set DM_TELEGRAM_BOT_TOKEN environment variable or pass --token")
        sys.exit(1)

    bot = TelegramBotController(bot_token=args.token, chat_id=args.chat_id)

    shutdown_event = asyncio.Event()

    def handle_signal():
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            pass

    logger.info("=" * 60)
    logger.info("DarkMatter Telegram Controller Starting")
    logger.info("=" * 60)

    await bot.start()
    try:
        await shutdown_event.wait()
    finally:
        await bot.stop()
        logger.info("Telegram Controller terminated.")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
