# TPCD Bot

Custom bot for the TPCD (Teamer Pest Control Department) Discord server.

**Feature 1: automated daily polls.** Posts one native Discord poll every day at 9:00 AM IST in #daily-polls from a 200-question bank, pings the Poll Ping role, captures results when each poll closes, and posts a weekly recap on Sundays. Zero daily ops required.

**Club member sync.** Anyone holding a TPCD¹..TPCD⁷ club role automatically gets @Club Member, and loses it if they leave all clubs. Reacts instantly to role changes, plus a full reconcile on startup and every 6 hours. `/clubsync` (staff) forces a sync and reports counts.

**Feature 3: giveaways.** Replaces Giveaway Boat: button entry with role requirements (up to 3, all/any logic) and bonus entries for a chosen role, live entrant count, a countdown that updates itself, auto-draw at the deadline with entrants re-validated (left the server or dropped the role = not drawn), reroll that excludes previous winners, and image support. Survives restarts: expired giveaways draw on startup.

**Feature 2: tournaments.** For #tournament-hub, in **solo (1v1), duo (2v2), trio (3v3), or random teams** (sign up solo, get drawn into teams at start) across **single-elimination or round-robin** formats. Proper UI: rendered bracket and standings **images** (Discord-dark theme, seeds, scores, gold winners), button registration (teams register via a name popup + member picker), match announcement cards that ping who's up, score reporting, human result lines, and a gold champion banner. A `/tournament announce` command posts a promo card with a one-click sign-up button (grants the participant role, live count) and a sponsor field.

**Sticky messages.** Persistent per-channel text or embed notices stay at the bottom of active chats, with adjustable message/time repost thresholds. Settings survive restarts, and mentions are rendered without repeatedly pinging users.

## Setup

1. **Create the bot application** at https://discord.com/developers/applications
   - New Application -> name it `TPCD Bot`
   - Bot tab -> Reset Token -> copy it (this goes in `.env`)
   - Enable the **Server Members Intent** used by greetings, club sync, and giveaway re-validation. Sticky messages do not need the Message Content intent.

2. **Invite it** (replace `CLIENT_ID` with your Application ID):

   ```
   https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=158720
   ```

   Permissions = View Channels, Send Messages, Manage Messages, Embed Links, and Mention Everyone (needed for the role ping).

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
| `/tournament create name game [size] [format]` | Create an event. `size` = Solo/Duo/Trio or Random Duos/Trios, `format` = Single elimination or Round robin |
| `/tournament join` / `leave` | (Solo) enter or drop, or use the Join/Leave buttons |
| `/tournament register team_name player2 [player3]` | (Team) slash alternative to the Register team button (player3 only for 3v3) |
| `/tournament unregister` | (Team) captain or staff withdraws a team |
| `/tournament start [id]` | Lock registration, random-seed, post the bracket/standings image + opening matchups |
| `/tournament report match winner [score]` | Record a result; in team modes pick any member of the winning team |
| `/tournament bracket [id]` | Current bracket or standings image (ephemeral) |
| `/tournament list` | Recent tournaments, modes, formats, champions |
| `/tournament cancel [id]` | Cancel a tournament |
| `/tournament announce ...` | Post a promo card with a sign-up button (participant role + live count) and sponsor |
| `/tournament schedule date time [title] ...` | Create a native Discord event (auto local-time, reminders, RSVP) and post the link |

### Giveaway commands

`create`, `end`, `reroll`, `cancel`, `entries` are staff-gated; `list` is open.

| Command | What it does |
|---|---|
| `/giveaway create prize duration ...` | Start one. Duration like `1d`, `12h`, `2h30m`. Optional: channel, host, winners, up to 3 required roles (all/any), bonus-entry role + count, image, description |
| `/giveaway end [id]` | End now and draw |
| `/giveaway reroll [id] [winners]` | Draw replacements, excluding previous winners |
| `/giveaway cancel [id]` | Cancel, nobody wins |
| `/giveaway list` | Active giveaways with countdowns |
| `/giveaway entries [id]` | Who entered, with entry weights |

### Sticky commands

Sticky commands require **Manage Messages**. The default repost speed matches StickyBot: after 5 new messages or, on the next new message, after 15 seconds.

| Command | What it does |
|---|---|
| `/stick message [style] [image_url] [every_messages] [after_seconds]` | Create or replace this channel's plain-text or embed sticky |
| `/stickstop` / `/stickstart` | Pause or resume this channel's sticky |
| `/stickremove` | Permanently remove this channel's sticky |
| `/stickies` | List all saved stickies in this server |
| `/stickspeed [every_messages] [after_seconds]` | View or change this channel's repost thresholds |

Entrants click **Enter** (click again to leave). Requirement checks happen at click time with a clear "you're missing X" message, and again at draw time so dropping a role or leaving the server disqualifies. The bonus role multiplies chances (e.g. boosters get 2x). `id` defaults to the most recent giveaway.

Elimination pads to the next power of two and gives top seeds first-round byes. Round robin has everyone play everyone, ranked by wins then game differential then head-to-head; the top of the final standings is champion. Seeding is random at start. `id` defaults to the most recent active tournament.

**Team flow (duo/trio):** create with size Duo or Trio → captains hit **Register team** on the post (popup asks the team name, then a member picker sized to the mode) → `/tournament start` → staff `/tournament report` each result with an optional score. Reports post a result line pinging both rosters and re-post the updated bracket/standings; the final drops a champion banner.

**Random teams (scramble):** create with size Random Duos or Random Trios → players hit **Join** individually (no squad needed) → `/tournament start` shuffles everyone into random teams of the chosen size, posts a "teams have been drawn" reveal pinging each new team, then runs exactly like a team event. Needs at least two full teams' worth of players; leftover players are spread across teams as subs so everyone is placed.

**Announcements:** `/tournament announce` takes title, host, schedule, size, and optional best-of, sponsor/prize, min players, coordination channel, and notes. It posts a clean card with a **Join tournament** button that toggles the participant role and keeps a live signed-up count on the embed.

**Scheduling:** `/tournament schedule date time` creates a native Discord Scheduled Event (defaults the name to the active tournament, timezone to Asia/Kolkata). The event shows in each member's local timezone, sends reminders, and tracks who's Interested. The bot also posts the event link with `<t:...>` timestamps (local time + live countdown). Needs the **Manage Events** permission. Optional `ping_participants` notifies the participant role.

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
