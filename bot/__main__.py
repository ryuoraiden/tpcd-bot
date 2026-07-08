"""TPCD Bot entry point: python -m bot"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import discord
from discord.ext import commands

from .config import config
from .db import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("tpcd")

BANK_PATH = Path(__file__).parent / "data" / "question_bank.json"

COGS = [
    "bot.cogs.daily_polls",
    "bot.cogs.poll_admin",
    "bot.cogs.tournaments",
    "bot.cogs.greetings",
    "bot.cogs.self_roles",
]


class TPCDBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        # privileged intent, needed only for welcome/goodbye; must also be
        # enabled in the dev portal (Bot -> Server Members Intent)
        if config.welcome_channel_id or config.goodbye_channel_id:
            intents.members = True
        super().__init__(command_prefix="!tpcd ", intents=intents)
        self.db = Database(config.db_path)

    async def setup_hook(self) -> None:
        await self.db.connect()
        bank = json.loads(BANK_PATH.read_text(encoding="utf-8"))
        inserted, retired = await self.db.seed(bank)
        if inserted or retired:
            log.info("Seed: +%d new questions, -%d retired.", inserted, retired)
        for cog in COGS:
            await self.load_extension(cog)
        synced = await self.tree.sync()
        log.info("Synced %d slash commands.", len(synced))

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id %s)", self.user, self.user.id)

    async def close(self) -> None:
        await self.db.close()
        await super().close()


def main() -> None:
    config.validate()
    bot = TPCDBot()
    try:
        bot.run(config.token, log_handler=None)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
