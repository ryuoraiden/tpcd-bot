# TPCD Bot — Handoff Context

> Portable context doc for any Claude instance working on this project. Written 2026-07-04, day of initial build + deploy. Read alongside the owner's "TPCD Full Context Document" (server strategy, channel rules, audit findings) if available.

## What this is

Custom Discord bot for the TPCD (Teamer Pest Control Department) server, a ~500 member multi-club Brawl Stars anti-teaming community owned by Mohammed Hani (B25 CSE, NITC). First shipped feature: fully automated daily polls for the #daily-polls channel, built because engagement there was dying (4-27 votes per poll, 50% tie rate, one member complaining they carried the channel alone).

- Code: `D:\Projects\TPCD Discord Bot` (Windows dev machine). Repo: **https://github.com/ryuoraiden/tpcd-bot** (public; infra specifics like the server IP live only in the untracked `OPS.md`, keep them out of tracked files)
- Bot account: **TPCD Bot#6680**, application id `1522683008652411041`
- Server: TPCD, guild id `1265581954049380394`; poll channel id `1399995153850306640`
- Production: Oracle Cloud Always Free VM shared with the owner's NITC Bot (separate project, `D:/Projects/NITC Discord Bot`, repo ryuoraiden/nitc-bot)

## Architecture

Python 3.14 locally / 3.12 on server, discord.py 2.7, aiosqlite, APScheduler, python-dotenv. Stack deliberately mirrors NITC Bot so modules stay shareable and both bots share one host.

```
bot/
├── __main__.py         # entry: python -m bot; loads cogs, seeds bank, syncs commands
├── config.py           # frozen dataclass from .env; config.validate() gives friendly errors
├── checks.py           # shared is_staff() / staff_only() gate (owner + roles + Manage Server)
├── db.py               # Database class: aiosqlite, schema, all queries (polls + tournaments)
├── seed_check.py       # offline bank validator: python -m bot.seed_check (stdlib only)
├── tournament.py       # PURE single-elim bracket logic (seeding, byes, match tree); no DB/Discord
├── cogs/
│   ├── daily_polls.py  # DailyPolls cog: APScheduler jobs, posting, results capture, recap
│   ├── poll_admin.py   # PollAdmin cog: /poll command group
│   └── tournaments.py  # Tournaments cog: /tournament group + persistent Join/Leave view
└── data/
    └── question_bank.json  # 200 questions + 13 pre_used historical, seeded into SQLite
data/tpcd.db            # SQLite (gitignored); THE SERVER COPY IS AUTHORITATIVE
.env                    # token + IDs (gitignored); .env.example is the template
```

SQLite schema (all `CREATE TABLE IF NOT EXISTS`, so schema changes auto-migrate on restart):
- `questions`, `polls`, `kv` — daily poll feature (as before)
- `tournaments` (id, name, game, format, status[open/running/finished/cancelled], guild_id, channel_id, message_id, created_by, bracket_size, rounds, winner_user_id)
- `participants` (tournament_id, user_id, display_name, seed) UNIQUE(tournament_id, user_id)
- `matches` (tournament_id, match_no, round, pos, p1_user_id, p2_user_id, winner_user_id, status[pending/ready/done], next_match_no, next_slot)

## How the daily poll works

1. APScheduler cron fires daily at 9:00 AM Asia/Kolkata (owner's chosen time)
2. Picks a random unused question, avoiding the categories of the last 2 polls (anti-streak); a "queued" question in `kv` lets `/poll preview`/`skip`/reroll control what posts next
3. Posts a native `discord.Poll` (24h duration, per channel rules) with `<@&poll_ping_role>` mention
4. Marks question used, records the poll row, sets `last_post_date` (dedupe guard so restarts can't double-post the same day)
5. A DateTrigger job fires ~2 min after the poll closes and stores final vote counts + winner (ties stored as winner=NULL, tie rate is a tracked stat). On startup, a sweep finalizes anything that closed while offline and reschedules pending captures
6. Sunday 8 PM IST: recap embed of the week's winners, nudges Poll Ping role signup
7. Below 14 unused questions: DMs the owner

Slash commands (`/poll post|preview|skip|add|bank|history|stats`), all ephemeral, gated to owner id + `ADMIN_ROLE_IDS` + anyone with Manage Server.

## How tournaments work (Feature 2, shipped 2026-07-04)

Single elimination for #tournament-hub, in two modes: **solo (1v1)** and **team (3v3, for Brawl Stars)**. `tournaments.mode` = 'solo'/'team', `team_size` = 1/3.

Core concept: the bracket runs over **entrant ids**. In solo an entrant is a user (entrant id = user_id); in team an entrant is a team (entrant id = team id). `matches.p1_user_id/p2_user_id/winner_user_id` therefore hold entrant ids, not always user ids — the column names are legacy. Cog helpers `names_map(t)` (entrant id -> display name) and `entrant_member_ids(t, id)` (entrant -> list of user ids for pinging) hide the difference.

Solo: `/tournament create` (mode Solo) posts an embed with persistent Join/Leave buttons (one `RegistrationView` with static custom_ids handles every open tournament; re-registered via `bot.add_view` in cog_load so buttons survive restarts — callback resolves the tournament from the message id).

Team: `/tournament create` (mode Team) posts an embed with no buttons. A captain runs `/tournament register team_name player2 player3` (caller = captain = player1); it validates 3 distinct non-bot users, none already registered, then creates a `teams` row + 3 `participants` rows carrying `team_id`/`is_captain`. `/tournament unregister` (captain or staff) removes a team.

`/tournament start` random-seeds the entrants (teams or players), calls `build_bracket` in `bot/tournament.py`, persists matches, auto-advances round-1 byes. Byes: entrant count pads to next power of two; `seed_positions` guarantees byes face top seeds and never each other.

`/tournament report match winner`: in team mode `winner` is any member of the winning team (resolved via `team_of_user`); sets the winning entrant, fills the next match slot (-> `ready` when both slots set), up to the final which sets champion + announces. **After every report it pings the whole match**: `AllowedMentions(users=True)`, congrats to the winning roster, respect to the losing roster (full 3-man pings in team mode).

Staff-gated: create/start/report/cancel/(register is open, unregister captain-or-staff). Open: join/leave/register/bracket/list. `tournament.py` is pure and covered by offline sims: solo 2-32 players and team 2-16 teams, each checking every match resolves, ping rosters are correct, and the top seed wins when the better seed always wins. DB migration (ADD COLUMN, tested idempotent + against a pre-existing DB) runs in `db.connect()`.

### /tournament schedule (2026-07-06)

- Creates a native Discord Scheduled Event (`guild.create_scheduled_event`, entity_type external, guild_only, end_time = start + duration). Params: date (YYYY-MM-DD), time (HH:MM), title (defaults to active tournament name), duration_hours (clamped 1-24), location (defaults to `#channel`), description, timezone (IANA, default config.timezone), ping_participants. Sets the event cover to EVENT_BANNER (the welcome banner) when present. Posts the event link with `<t:unix:F>`/`<t:unix:R>` timestamps so it renders in each viewer's local time + countdown. **Needs Manage Events perm** (checked up front). Parsing/timezone logic offline-tested.

### Scramble / random teams (2026-07-06)

- New `mode='scramble'`: players sign up solo (SoloRegView, `registers_individually(t)` covers solo+scramble), then `/tournament start` calls `_form_scramble_teams`: shuffles participants, groups into `len//team_size` teams (leftovers spread as +1 subs, so every team >= team_size), creates team rows, `assign_participant_team` on each, then `set_tournament_mode(tid, 'team')` — so all post-start logic is plain team mode. Needs >= 2*team_size players. `_teams_drawn_text` posts the reveal with pings. `team_size_of` and `registers_individually` treat scramble correctly.
- create size choices encode scramble as "s2"/"s3" (parsed in create). Offline-tested: everyone placed, teams >= size, flips to team, runs a bracket.

### Duo + round robin + announce (2026-07-06)

- **Sizes:** solo/duo/trio. `mode` stays 'solo'/'team'; `team_size` (1/2/3) is the source of truth. `team_size_of(t)`, `vs_label(t)` derive labels. Team registration flow (modal → UserSelect picker) is sized by `team_size` — the picker's min/max are set in `TeammatePickView.__init__`. `/tournament register` makes player3 optional.
- **Round robin:** `tournaments.format` ('single_elim'/'round_robin'). Pure `build_round_robin` (circle method) + `round_robin_standings` (wins → game diff from scores → head-to-head) in `bot/tournament.py`. RR matches reuse the matches table with next pointers NULL and all status 'ready'; no advancement. Champion = top of final standings once every match is done. `render_standings` in bracket_render.py draws the table image. Cog dispatches on format: `display_message`, `_finish_report_rr` vs `_finish_report_elim`.
- **/tournament announce:** promo card + `ParticipantButton` (DynamicItem, role id + player goal in custom_id, toggles PARTICIPANT_ROLE_ID = 1380414459050332160, live count via `role.members`, edits the "Signed up" field). Params: title, host, schedule, size, best_of, sponsor, min_players, coordinate_in, notes, ping_everyone. Needs Manage Roles + bot role above the participant role (same as self-roles).
- Offline-tested: RR schedule (2-12 entrants, complete + no dupes), standings ranking, renders visually verified. Not yet live-tested against real Discord.

### UI layer (upgraded 2026-07-05)

- **`bot/bracket_render.py`**: pure Pillow renderers. `render_bracket(...)` draws the full bracket as a Discord-dark PNG (round columns with QUARTERFINALS/SEMIFINALS/GRAND FINAL labels, seed numbers, gold winner rows, scores, elbow connectors, BYE/TBD states, CHAMPION strip); `render_champion(...)` draws a gold banner with team name + roster. Fonts: DejaVu (Ubuntu, `fonts-dejavu-core` installed on the server), Segoe/Arial on Windows, Pillow bundled fallback. Brackets over `MAX_BRACKET_SIZE` (32) fall back to the text embed, as does any render exception (try/except in `bracket_message`).
- **Registration views**: `SoloRegView` (Join/Leave buttons) and `TeamRegView` (Register team/Withdraw team). Both persistent with static custom_ids, re-registered in cog_load. Team flow: button → `TeamNameModal` (text input) → ephemeral `TeammatePickView` (a `discord.ui.UserSelect`, min/max 2, + Confirm/Cancel) → `register_team()` shared helper (also used by the slash command).
- **Report flow posts, in order**: result card (round label, optional score, random flavor line from WIN_LINES/LOSE_LINES, full-roster pings) → champion banner if final → updated bracket image → "Up next" card pinging the match that just became ready.
- **`/tournament start`** posts the bracket image + a "First matchups" message pinging all round-1 pairings.
- `matches.score` column added via MIGRATIONS; `resolve_match(..., score=None)`.

## Key decisions (owner-made, don't relitigate)

| Decision | Choice |
|---|---|
| Post time | 9:00 AM IST daily |
| If a member already posted a poll that day | **Bot posts anyway** (no skip logic; owner explicitly chose this over rule-strict skipping) |
| Scope | "Go full on": SQLite + analytics + full command set, not a minimal JSON-file service |
| Storage | SQLite over JSON state files |
| Hosting | Oracle Always Free (owner had the instance for NITC bot); didn't wait for GitHub Education |
| BrawlTools | Do NOT build clan-management features; TPCD pays for BrawlTools premium. Future niches: manager application pipeline, inter-club tournaments, transfer tracking |

## Question bank design

200 questions in 7 categories, weighted toward what the channel's history proved engages: debate bait and rivalry binaries. gaming_general 42, brawl_stars 35, hot_takes 34, this_or_that 34, hypotheticals 20, sports_pop_culture 20, food 15. (Clash Royale was retired 2026-07-07 — CR no longer relevant to TPCD. The bank carries `retired_categories`, and `db.seed` deletes unused questions in those categories on startup so a removed category actually leaves the live pool; already-posted questions are kept for history.)

Channel rule 7 is NO repeat questions ever. The 13 polls posted manually before the bot existed are in `pre_used` in the JSON and seeded with `used_at` set, so they're unpickable. Avoid adding questions on already-used topics: favorite legendary brawler, house pets, song genres, boost perks, event choice, overrated foods, superpowers, space settlement, FIFA 26 teams, dogs vs cats.

Discord limits enforced by seed_check: question ≤300 chars, options 2-10, each ≤55 chars. Run `python -m bot.seed_check` after editing the JSON. New JSON questions are picked up on restart (INSERT OR IGNORE by id); `/poll add` works live.

## Production deployment

- Instance: Ubuntu 24.04, VM.Standard.E2.1.Micro (1 GB RAM). Concrete IP, instance name, and SSH key location live in the **untracked `OPS.md`** in the project root on the dev machine
- Bot at `/opt/tpcd-bot`, own venv, runs as no-login system user `tpcdbot`, `.env` chmod 600
- systemd: `tpcd-bot.service`, enabled on boot, Restart=on-failure
- 2G swapfile present; NITC bot shares the box (service `nitc-bot` planned/deployed separately)
- Ops one-liners are in HOSTING.md (logs, restart, DB backup, redeploy procedure)

## Gotchas (learned the hard way)

1. **Token mixups are destructive.** On launch day the owner pasted the NITC bot's token into TPCD's .env; the startup `tree.sync()` wiped NITC Bot's global slash commands (restored when NITC bot next syncs). Always check the "Logged in as ..." log line matches the intended bot before calling a deploy done.
2. **Never run the bot locally now.** The server DB is authoritative. A local copy = duplicate command replies + duplicate 9 AM polls (separate DBs mean the dedupe guard can't see each other).
3. `.env.example` had a real token pasted into it at one point and it is NOT gitignored. Before ever running `git init`/pushing, confirm it holds placeholders only. The exposed NITC token ideally should be reset in the dev portal.
4. `POLL_CHANNEL_ID` must be the bare snowflake, not a channel URL (owner pasted a URL once; config has no URL parsing).
5. `ADMIN_ROLE_IDS` in the server's `.env` is still empty (owner had put a user id there, which does nothing). Owner override covers him meanwhile. Fill with Captain / 1st Commander / Manager role ids, then `systemctl restart tpcd-bot`.
6. Slash commands are global-synced; new-app propagation plus Discord client caching can hide them. Ctrl+R the client first; if truly absent, re-authorize with `scope=bot+applications.commands`.
7. Oracle Always Free may reclaim instances idling under ~20% CPU for a week. If the instance stops, just start it again; services are enabled and come back on boot.
8. discord.py warns about missing message content intent at startup; harmless (prefix commands unused).

## Pending / backlog

- [ ] Fill `ADMIN_ROLE_IDS` on the server and restart (gotcha 5)
- [x] ~~Clean the real token out of .env.example~~ (done 2026-07-05, placeholders restored) — still worth resetting the NITC token that sat in it
- [x] ~~git init + private GitHub repo~~ (done 2026-07-05: ryuoraiden/tpcd-bot, private. Commit per change from now on so upgrades have history)
- [x] ~~Verify the first scheduled 9 AM IST poll fires~~ (confirmed 2026-07-05: fired on schedule, finalize job queued)
- [ ] Verify NITC Bot's slash commands recovered after its next start (gotcha 1)
- [ ] Occasional `scp` backup of `/opt/tpcd-bot/data/tpcd.db`
- [ ] Bank runs dry around year-end at 1/day (200 questions, ~187 days from launch); low-pool DM gives warning at 14 left
- [x] ~~Live-test tournaments~~ (done 2026-07-05: real 3v3 ran end to end, bracket/byes/champion all correct. Surfaced two render bugs, both fixed: CJK glyphs drew as boxes -> names now sanitized; long team names truncated -> wider boxes)
- **Copy rule for all bot output:** owner rejects "AI-sounding" text. No theatrical flavor lines, no em dashes, sparse emoji, consolidated pings, blank lines between message sections. Write what a human organizer would type.
- [ ] Tournament niceties not yet built: per-match score/best-of, letting participants self-report (currently staff-only), image bracket rendering (text embeds only), round-robin format (only single-elim), variable team size (hardcoded 3v3 via TEAM_SIZE in tournaments.py)
- [ ] Future features (owner's doc, backburner): manager application pipeline with Commander voting, cross-club transfer tracking. Inter-club tournament automation is now DONE (single-elim). Add new features as cogs in the `COGS` list in `__main__.py`

## Owner preferences (apply to all interactions)

Casual, direct tone. **No em dashes.** Structured outputs: tables, ready-to-copy templates. Automation over anything needing daily manual input. Honest reframes over cheerleading. He works in mission-mode bursts.
