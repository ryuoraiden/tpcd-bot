"""Daily poll engine: scheduled posting, results capture, weekly recap.

Channel rules baked in: native Discord polls, 24h duration, one bot poll per
day, @Poll Ping mention, never repeat a question.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite
import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from discord.ext import commands

from ..config import config
from ..db import Database

log = logging.getLogger(__name__)

LOW_POOL_THRESHOLD = 14
KV_LAST_POST_DATE = "last_post_date"
KV_QUEUED_QUESTION = "queued_question_id"


class DailyPolls(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db
        self.scheduler = AsyncIOScheduler(timezone=config.tz)

    async def cog_load(self) -> None:
        self.scheduler.add_job(
            self.post_daily_poll,
            CronTrigger(hour=config.post_hour, minute=config.post_minute, timezone=config.tz),
            id="daily_poll",
        )
        if config.weekly_recap:
            self.scheduler.add_job(
                self.post_weekly_recap,
                CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=config.tz),
                id="weekly_recap",
            )
        self.scheduler.start()
        self.bot.loop.create_task(self._sweep_unfinalized())

    async def cog_unload(self) -> None:
        self.scheduler.shutdown(wait=False)

    # -- selection -------------------------------------------------------

    async def next_question(self, reroll: bool = False) -> aiosqlite.Row | None:
        """Return the queued question, picking (or re-picking) if needed."""
        if not reroll:
            queued_id = await self.db.kv_get(KV_QUEUED_QUESTION)
            if queued_id:
                row = await self.db.get_question(queued_id)
                if row and row["used_at"] is None and not row["skipped"]:
                    return row
        recent = await self.db.recent_categories(2)
        row = await self.db.pick_random_unused(exclude_categories=recent)
        await self.db.kv_set(KV_QUEUED_QUESTION, row["id"] if row else None)
        return row

    # -- posting -------------------------------------------------------

    async def post_daily_poll(self, force: bool = False) -> str:
        """Post today's poll. Returns a status string for command feedback."""
        today = datetime.now(config.tz).date().isoformat()
        if not force and await self.db.kv_get(KV_LAST_POST_DATE) == today:
            return "Already posted today's poll. Use force to post another."

        question = await self.next_question()
        if question is None:
            await self._warn_owner("The question bank is empty. Add questions with /poll add.")
            return "Question bank is empty!"

        channel = self.bot.get_channel(config.poll_channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(config.poll_channel_id)
            except discord.HTTPException:
                log.error("Poll channel %s not found.", config.poll_channel_id)
                return "Poll channel not found. Check POLL_CHANNEL_ID."

        options = json.loads(question["options_json"])
        poll = discord.Poll(
            question=question["question"],
            duration=timedelta(hours=config.poll_duration_hours),
        )
        for option in options:
            poll.add_answer(text=option)

        content = f"<@&{config.poll_ping_role_id}>" if config.poll_ping_role_id else None
        message = await channel.send(
            content=content,
            poll=poll,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )

        posted_at = datetime.now(timezone.utc)
        closes_at = posted_at + timedelta(hours=config.poll_duration_hours)
        poll_id = await self.db.record_poll(
            question["id"], message.id, channel.id, posted_at.isoformat(), closes_at.isoformat()
        )
        await self.db.mark_used(question["id"])
        await self.db.kv_set(KV_LAST_POST_DATE, today)
        await self.db.kv_set(KV_QUEUED_QUESTION, None)
        self._schedule_finalize(poll_id, closes_at)
        log.info("Posted poll #%d: %s", poll_id, question["question"])

        remaining = await self.db.unused_count()
        if remaining < LOW_POOL_THRESHOLD:
            await self._warn_owner(
                f"Question bank is running low: {remaining} unused questions left. "
                "Top up with /poll add."
            )
        return f"Posted: {question['question']}"

    # -- results capture -------------------------------------------------------

    def _schedule_finalize(self, poll_id: int, closes_at: datetime) -> None:
        run_at = closes_at + timedelta(minutes=2)
        self.scheduler.add_job(
            self.finalize_poll,
            DateTrigger(run_date=run_at),
            args=[poll_id],
            id=f"finalize_{poll_id}",
            replace_existing=True,
        )

    async def _sweep_unfinalized(self) -> None:
        """On startup, finalize polls that ended while offline and re-schedule
        capture for ones still running."""
        await self.bot.wait_until_ready()
        for row in await self.db.unfinalized_polls():
            closes_at = datetime.fromisoformat(row["closes_at"])
            if closes_at <= datetime.now(timezone.utc):
                await self.finalize_poll(row["id"])
            else:
                self._schedule_finalize(row["id"], closes_at)

    async def finalize_poll(self, poll_id: int) -> None:
        async with self.db.conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)) as cur:
            row = await cur.fetchone()
        if row is None or row["finalized"]:
            return
        try:
            channel = self.bot.get_channel(row["channel_id"]) or await self.bot.fetch_channel(
                row["channel_id"]
            )
            message = await channel.fetch_message(row["message_id"])
        except discord.HTTPException as e:
            log.warning("Could not fetch poll message for poll #%d: %s", poll_id, e)
            await self.db.finalize_poll(poll_id, 0, None, {})
            return

        poll = message.poll
        if poll is None:
            await self.db.finalize_poll(poll_id, 0, None, {})
            return

        results = {answer.text: answer.vote_count for answer in poll.answers}
        total = sum(results.values())
        winner = None
        if results:
            top = max(results.values())
            leaders = [text for text, count in results.items() if count == top]
            if top > 0 and len(leaders) == 1:
                winner = leaders[0]  # ties stay None — tie rate is a tracked stat
        await self.db.finalize_poll(poll_id, total, winner, results)
        log.info("Finalized poll #%d: %d votes, winner=%s", poll_id, total, winner or "TIE")

    # -- weekly recap -------------------------------------------------------

    async def post_weekly_recap(self) -> None:
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        polls = await self.db.polls_since(week_ago)
        if not polls:
            return
        channel = self.bot.get_channel(config.poll_channel_id)
        if channel is None:
            return

        lines = []
        total_votes = 0
        for p in polls:
            votes = p["total_votes"] or 0
            total_votes += votes
            outcome = p["winner_option"] or "It's a tie!"
            lines.append(f"**{p['question']}**\n> 🏆 {outcome} · {votes} votes")

        embed = discord.Embed(
            title="📊 This Week in Polls",
            description="\n\n".join(lines)[:4000],
            color=discord.Color.gold(),
        )
        embed.set_footer(
            text=f"{len(polls)} polls · {total_votes} votes this week · "
            "Grab the Poll Ping role in #self-roles to never miss one"
        )
        await channel.send(embed=embed)
        log.info("Posted weekly recap (%d polls).", len(polls))

    # -- helpers -------------------------------------------------------

    async def _warn_owner(self, text: str) -> None:
        if not config.owner_id:
            log.warning("Owner warning (no OWNER_ID set): %s", text)
            return
        try:
            owner = await self.bot.fetch_user(config.owner_id)
            await owner.send(f"⚠️ TPCD Bot: {text}")
        except discord.HTTPException:
            log.warning("Could not DM owner: %s", text)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DailyPolls(bot))
