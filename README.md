# TPCD Bot

Custom bot for the TPCD (Teamer Pest Control Department) Discord server.

**Feature 1: automated daily polls.** Posts one native Discord poll every day at 9:00 AM IST in #daily-polls from a 200-question bank, pings the Poll Ping role, captures results when each poll closes, and posts a weekly recap on Sundays. Zero daily ops required.

**Feature 2: tournaments.** Single-elimination brackets for #tournament-hub, **solo (1v1) or 3v3 teams** (for Brawl Stars), with a proper UI: rendered bracket **images** (Discord-dark theme, seeds, scores, gold winners, champion strip), button registration (teams register via a name popup + member picker), match-ready announcement cards that ping who's up, score reporting, flavor-text result cards, and a gold champion banner image at the end.

## Setup

1. **Create the bot application** at https://discord.com/developers/applications
   - New Application -> name it `TPCD Bot`
   - Bot tab -> Reset Token -> copy it (this goes in `.env`)
   - No privileged intents needed

2. **Invite it** (replace `CLIENT_ID` with your Application ID):

   ```
   https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=157696
   ```

   Permissions = View Channels, Send Messages, Embed Links, Mention Everyone (needed for the role ping).

3. **Configure:**

   ```
   copy .env.example .env
   ```

   Fill in the token plus the IDs (enable Developer Mode in Discord, right-click channel/role/user -> Copy ID). `ADMIN_ROLE_IDS` should be the Captain, 1st Commander, and Manager role IDs, comma-separated.

4. **Install and run:**

   ```
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   .venv\Scripts\python -m bot
   ```

## Commands

All gated to Captain / 1st Commander / Manager (or anyone with Manage Server), replies are ephemeral so the poll channel stays clean:

| Command | What it does |
|---|---|
| `/poll post [force]` | Post the daily poll now. `force` bypasses the once-per-day guard |
| `/poll preview` | See the next queued question, with a reroll button |
| `/poll skip` | Throw out the queued question, pick another |
| `/poll add` | Add a question (options separated by `\|`) |
| `/poll bank` | Unused questions left, per category |
| `/poll history [n]` | Recent polls with winners and vote counts |
| `/poll stats` | Avg votes, tie rate, best categories vs the old 8-11 vote baseline |

### Tournament commands

`/tournament create` and `start`/`report`/`cancel` are staff-gated; `join`/`leave`/`bracket`/`list` are open to everyone.

| Command | What it does |
|---|---|
| `/tournament create name game [mode]` | Create an event. `mode` = Solo (1v1) or Team (3v3), defaults solo |
| `/tournament join` / `leave` | (Solo) enter or drop, or use the Join/Leave buttons |
| `/tournament register team_name player2 player3` | (Team) slash alternative to the Register team button |
| `/tournament unregister` | (Team) captain or staff withdraws a team |
| `/tournament start [id]` | Lock registration, random-seed, post the bracket image + first matchup pings |
| `/tournament report match winner [score]` | Record a result (e.g. score `2-1`); in 3v3 pick any member of the winning team |
| `/tournament bracket [id]` | Current bracket image (ephemeral) |
| `/tournament list` | Recent tournaments, modes, champions |
| `/tournament cancel [id]` | Cancel a tournament |

Any entrant count works: the bracket pads to the next power of two and gives top seeds first-round byes. Seeding is random at start. `id` defaults to the most recent active tournament, so you usually omit it.

**3v3 flow:** create with mode Team → captains hit **Register team** on the post (popup asks the team name, then a member picker for the 2 teammates) → `/tournament start` → after each set, staff `/tournament report` with any member of the winning team and an optional score. Every report posts a result card pinging both full rosters, announces the next match that became ready, and re-posts the updated bracket image. The final drops a champion banner.

## How it behaves

- One poll per day at `POST_TIME` (default 09:00 Asia/Kolkata), native Discord poll, 24h duration, `@Poll Ping` mention — matches the #daily-polls rules
- Never repeats a question: the bank tracks used questions in SQLite, and the 13 polls posted before the bot existed are pre-marked as used
- Avoids posting the same category two days in a row (anti-streak)
- Captures final results ~2 minutes after each poll closes (also catches up on restart if it was offline when a poll ended)
- DMs the owner when fewer than 14 unused questions remain
- Sunday 8 PM IST: weekly recap embed with the week's winners

## Maintenance

- `python -m bot.seed_check` validates the question bank (run after editing `bot/data/question_bank.json`)
- New questions added to the JSON are picked up on next restart; `/poll add` works live
- The SQLite DB lives in `data/tpcd.db` — back it up if you migrate hosts

See [HOSTING.md](HOSTING.md) for running it 24/7 for free.
