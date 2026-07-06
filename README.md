# TPCD Bot

Custom bot for the TPCD (Teamer Pest Control Department) Discord server.

**Feature 1: automated daily polls.** Posts one native Discord poll every day at 9:00 AM IST in #daily-polls from a 200-question bank, pings the Poll Ping role, captures results when each poll closes, and posts a weekly recap on Sundays. Zero daily ops required.

**Feature 2: tournaments.** For #tournament-hub, in **solo (1v1), duo (2v2), or trio (3v3)** across **single-elimination or round-robin** formats. Proper UI: rendered bracket and standings **images** (Discord-dark theme, seeds, scores, gold winners), button registration (teams register via a name popup + member picker), match announcement cards that ping who's up, score reporting, human result lines, and a gold champion banner. A `/tournament announce` command posts a promo card with a one-click sign-up button (grants the participant role, live count) and a sponsor field.

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
| `/tournament create name game [size] [format]` | Create an event. `size` = Solo/Duo/Trio, `format` = Single elimination or Round robin |
| `/tournament join` / `leave` | (Solo) enter or drop, or use the Join/Leave buttons |
| `/tournament register team_name player2 [player3]` | (Team) slash alternative to the Register team button (player3 only for 3v3) |
| `/tournament unregister` | (Team) captain or staff withdraws a team |
| `/tournament start [id]` | Lock registration, random-seed, post the bracket/standings image + opening matchups |
| `/tournament report match winner [score]` | Record a result; in team modes pick any member of the winning team |
| `/tournament bracket [id]` | Current bracket or standings image (ephemeral) |
| `/tournament list` | Recent tournaments, modes, formats, champions |
| `/tournament cancel [id]` | Cancel a tournament |
| `/tournament announce ...` | Post a promo card with a sign-up button (participant role + live count) and sponsor |

Elimination pads to the next power of two and gives top seeds first-round byes. Round robin has everyone play everyone, ranked by wins then game differential then head-to-head; the top of the final standings is champion. Seeding is random at start. `id` defaults to the most recent active tournament.

**Team flow (duo/trio):** create with size Duo or Trio → captains hit **Register team** on the post (popup asks the team name, then a member picker sized to the mode) → `/tournament start` → staff `/tournament report` each result with an optional score. Reports post a result line pinging both rosters and re-post the updated bracket/standings; the final drops a champion banner.

**Announcements:** `/tournament announce` takes title, host, schedule, size, and optional best-of, sponsor/prize, min players, coordination channel, and notes. It posts a clean card with a **Join tournament** button that toggles the participant role and keeps a live signed-up count on the embed.

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
