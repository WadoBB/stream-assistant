# Forza Race Results Extractor — Project Context

## Open To Do Items
See `TODO.md` in the project root for active issues, planned features, and known limitations.
Update it as part of any task that resolves or adds an item — don't wait to be asked.

## Working Directory and Branch — Read This First

Always work in **`C:\StreamAssistant`** on the **`main`** branch.

Claude Code may create worktrees under `C:\StreamAssistant\.claude\worktrees\` — ignore them entirely. Never read, edit, or commit from a worktree path. If you find yourself in one, stop and start a new session from `C:\StreamAssistant`.

## What This Project Does
This system captures race information from Forza Horizon gaming sessions to compile
performance data over time. Specifically it tracks:
- Car performance by class and race type
- Personal performance trends over time
- Competitor information and cars that outperform the user (for tune research)

Race start and end are detected using telemetry data from the game.
Captured data is stored in Google Sheets. Claude (AI) is used to scrape data from
screenshots. Screenshots are analyzed to extract race results from the scoreboard.

**Both Forza Horizon 5 and Forza Horizon 6 are supported simultaneously** via a
`--game` flag. The user selects the active game at session start using a Stream Deck
button — no config changes needed between games.

## Architecture — Two-Computer System
The system intentionally runs across two computers to minimize load on the gaming/streaming PC.

**Gaming PC (192.168.137.63):**
- Runs Forza Horizon
- Runs `capture_agent.py` — detects scoreboard, takes screenshot
- Streams gameplay via Stream Deck

**AI Computer (192.168.137.230):**
- Runs `controller.py` (Flask, port 5000) — Stream Deck toggle target
- Runs `telemetry_listener.py` — reads Forza UDP on port 9999
- Runs `results_extractor.py` — sends screenshot to Claude API, extracts data
- Runs `sheets_writer.py` — writes to Google Sheets
- Hosts the shared network folder that the gaming PC writes screenshots to
- Serves the stream overlay (`/overlay`, `/overlay/state`) via `controller.py`
- Exposes `/overlay/test` and `/logs` on `controller.py` for remote diagnosis
  from a Claude Code session running on the Gaming PC (see "Cross-Machine
  Development" below) — no SSH or remote desktop needed for these checks

**Important:** All code for both sides of the system lives on BOTH computers.
This is intentional — it simplifies GitHub management and means either computer
can be fully restored from GitHub if lost.

## Network Configuration

| Computer    | IP               | Role                                     |
|-------------|------------------|------------------------------------------|
| Gaming PC   | 192.168.137.63   | Forza, capture agent, Stream Deck        |
| AI Computer | 192.168.137.230  | All intelligence, controller, sheets     |

| Port | Protocol | Purpose                                  |
|------|----------|------------------------------------------|
| 9999 | UDP      | Forza telemetry → AI computer            |
| 9998 | UDP      | AI computer → capture agent (RACE_END trigger) |
| 5000 | TCP      | Flask controller (Stream Deck toggle, stream overlay) |

If IPs change, update `ai-computer/config.py` and both bat files in `gaming-pc/`.

## Cross-Machine Development
A Claude Code session working on this project runs on whichever computer
launched it — often the Gaming PC — and has no filesystem or terminal access
to the other machine. SSH was attempted (2026-09-11) to fix this generally
but hit repeated friction with Windows OpenSSH setup (`Add-WindowsCapability`
parameter errors) and was abandoned in favor of a lighter approach: extend
`controller.py`, which is already a Flask server reachable from either
machine over the LAN (same path the Stream Deck `/toggle` already uses
reliably), with narrow, purpose-built diagnostic endpoints instead of general
remote access:

- `GET /overlay/test` — writes a fake overlay event (optionally overriding
  car/track/class/time via query params) so the stream overlay can be
  verified end-to-end from either machine without racing or hand-writing
  `overlay/state.json`.
- `GET /logs?file=controller|stream_assistant|telemetry&lines=N` — returns
  the last N lines of one of this machine's log files as JSON, so they can
  be read remotely instead of relayed by hand. `file` is an allowlist, not a
  free-form path.

**When a future task needs to inspect or trigger something on the machine
this session isn't running on, prefer adding a narrow endpoint like these
over asking the user to relay commands or output by hand** — it's proven
more reliable than remote-access setup on this two-machine, two-OS-account
Windows LAN, and each endpoint added this way stays useful for next time.

## Stream Overlay — New Record Alert
Flashes a "NEW RECORD!" alert in OBS when a race beats the cached Best by
Track+Class time for that (Track, Class). Added 2026-09-11; not yet tested live.

- `sheets_writer.py`'s `check_for_new_record()` runs first in `write_race()`
  (before any Sheets writes), so the alert doesn't wait on them. It lazily
  loads a `{(track, class): seconds}` cache from the **Best by Track+Class**
  tab (read-only — this script never writes that tab) and compares each
  race's time against it, using the same lap-preferred-over-race-time logic
  as `aggregatePerCar_` in the Apps Script. A new record updates the cache in
  memory immediately and writes `ai-computer/overlay/state.json`
  (temp-then-rename, same pattern as the capture agent's screenshot writes).
  Non-competitive races (Spec Race/Touge/Time Attack) are skipped, same as
  everywhere else the Y-flag/record system applies.
- **Why this can't be faster via telemetry alone:** the FH5/FH6 UDP packet has
  no track field, so a track-specific record can only be checked after the
  scoreboard OCR step gives us the track name — same timing as the rest of
  the pipeline. A telemetry-only "instant teaser" tier was considered and
  explicitly declined in favor of one simple, always-accurate alert.
- `controller.py` (always-running, not the toggled pipeline) serves the page
  at `/overlay` and the current event as JSON at `/overlay/state`. Point an
  OBS Browser Source at `http://192.168.137.230:5000/overlay`.
- `ai-computer/overlay/index.html` polls `/overlay/state` every 1.5s, flashes
  a 7-second animation on a new `race_id`, and ignores events older than 60
  seconds so a Browser Source reload mid-stream doesn't replay a stale record.
- Plays `ai-computer/overlay/cheer.mp3` (served at `/overlay/cheer.mp3`) the
  moment the alert flashes in. OBS Browser Sources generally allow audio
  autoplay without a prior user gesture (this is how every existing
  StreamElements/Streamlabs-style alert box relies on sound working) —
  `.play()` is wrapped in `.catch(() => {})` so a browser that does block it
  just skips the sound instead of throwing.

## Network Share (Screenshots)
The AI computer shares `C:\StreamAssistant\ai-computer\captures\` as `StreamCaptures`.
The gaming PC maps this as drive **Z:** → `\\192.168.137.230\StreamCaptures`.
The capture agent writes screenshots to Z:\.

Setup during reinstallation:
1. Share the captures folder on the AI computer (share name: `StreamCaptures`)
2. On the gaming PC, map Z: to `\\192.168.137.230\StreamCaptures` (reconnect at sign-in)
3. Enter the AI computer account credentials when prompted

This is not fully automated and must be set up manually during any reinstallation.

## Game Version Switching
The `--game` flag (FH5 or FH6) is passed from the Stream Deck bat file through
the entire pipeline. controller → main.py → telemetry_listener, results_extractor,
sheets_writer, and capture_agent all switch behavior based on it.

- `gaming-pc/toggle_fh5.bat` — starts everything in FH5 mode, writes to FH5 sheet
- `gaming-pc/toggle_fh6.bat` — starts everything in FH6 mode, writes to FH6 sheet

Switching mid-session: press the running button to stop, then press the other to start.

Google Sheets IDs (from `ai-computer/config.py`):
- `FH5_SPREADSHEET_ID = "1Kk7Z35YZJQn9ZdkBl5-_Eso_nzKXxCmauszChrME9hE"`
- `FH6_SPREADSHEET_ID = "1Rd1V7z86sJFMumtativB6Tv6kZfBcwbD0JyWFhiy7gY"`

## Race Type — Dirt Trail vs Dirt Point to Point
The preferred race type designation is **Dirt Trail**. At one point the code was
changed to use "Dirt Point to Point" — this was incorrect and has been reverted.
Always use **Dirt Trail**.

## Race Type Detection
Race type is derived from keywords in the track name returned by Claude, via
`RACE_TYPE_MAP` in `results_extractor.py`'s `derive_race_type()`.
Evaluated top-to-bottom; **first match wins.** Order matters — do not reorder.

| Keyword in Track Name | Race Type Recorded     |
|-----------------------|------------------------|
| CROSS COUNTRY CIRCUIT | Cross-Country Circuit  |
| CROSS COUNTRY         | Cross-Country          |
| SCRAMBLE              | Dirt Scramble          |
| TRAIL                 | Dirt Trail             |
| CIRCUIT               | Road Circuit           |
| SPRINT                | Road Sprint            |
| DRAG                  | Drag Race              |
| COLOSSUS, GOLIATH     | Road Sprint            |
| TITAN                 | Cross-Country          |
| GAUNTLET              | Dirt Trail             |

"CROSS COUNTRY CIRCUIT" must precede "CROSS COUNTRY" and "CIRCUIT" or those
would match first.

**FH6 Touge tracks** are matched by full track name (no shared keyword):
HAKONE NANMAGARI, NORIKURA SKYLINE, ARASHIYAMA TAKAO, BANDI AZUMA, MT. HARUNA
→ all recorded as `Touge`.

**FH6 Street Race keywords** (checked after the Touge list, since Touge tracks
are matched first): RUN, CHARGE, DESCENT, CLIMB, ASCENT, CHASE, KITA INE
→ all recorded as `Street Race`.

**Default (no match):** `Street Race` for both FH5 and FH6
(`RACE_TYPE_DEFAULT_FH5` / `RACE_TYPE_DEFAULT_FH6`). The FH6 default was
briefly set to `Touge` (commit `984dd92`, assuming any unmatched FH6 track
was more likely an untracked Touge run) — this incorrectly labeled street
races whose track names didn't contain one of the keywords above as Touge.
Fixed 2026-08-04 by adding the missing ASCENT/CHASE keywords and reverting
the default to Street Race, since real Touge tracks are already positively
matched by name — the Touge default added no value and only mislabeled
unmatched street races. If new unmatched Touge tracks turn up, add them to
the explicit name list above rather than changing the default again.

## Filtering Rules — Non-Competitive Races
No race is skipped purely for having few or no opponents — a lap/race time is
still a valid personal best even in a solo Time Attack run or a 1v1 Touge race.
Instead, races with no meaningful win/loss comparison are recorded normally on
the Results tab but flagged via the Notes column so they don't pollute the
Cars-tab Races/Wins tally:

| Condition                                  | Notes value    |
|---------------------------------------------|----------------|
| `race_mode = "Spec Race"` (all racers same car + PI) | `"Spec Race"` |
| `race_type = "Touge"` (track name match — see Race Type Detection) | `"Touge"` |
| `total_racers < 2`, or `race_mode = "Time Attack"` | `"Time Attack"` |

**Note:** Touge detection used to be `total_racers = 2`, which produced false
positives on 2-person online races. As of commit `984dd92` (2026-07-01) it's
derived from `race_type == "Touge"` (track name match) instead — see Race Type
Detection above.

Rows with any of these Notes values are excluded from the Opponents tab (no real
opponents to log) and from the Races/Wins tally in `sheets_writer.py`'s
`update_car_stats()` — since position is always 1 in these events, counting them
would falsely inflate win rate. The Google Apps Script (`Forza Car Updater`)
should apply the same exclusion when computing the Fav flag (see TODO.md).

## Known Limitation — Short Drag Races Are Skipped
`MIN_RACE_DURATION_SECONDS = 30` in `telemetry_listener.py` filters out false
positives from loading screens and early quits. Drag races at high car class
finish in roughly 11–17 seconds — well under this threshold — so they are silently
dropped before a scoreboard screenshot is even requested.

**Status:** Accepted for personal use. Drag racing is rarely done outside of
storyline requirements and weekly challenges.

**If this ever needs to be fixed:** Detect the drag race signature in telemetry
(very high speed, very short duration, no laps) and bypass the duration check
for that case. A simple threshold reduction risks letting early quits through.

## Scoreboard Time vs Telemetry Time
Both **race time** and **best lap time** are captured from the **scoreboard
screenshot**, not from the telemetry. This was an intentional fix — the last
telemetry record before end-of-race did not reliably match the actual times
shown on the scoreboard.

The code prefers the scoreboard value and falls back to telemetry only if the
scoreboard value is missing/unparseable. Do not revert this behavior.

**2026-08-02 correction:** the original fix (`results_extractor.py`'s
`extract_results()`) only applied this scoreboard-preferred logic to
`race_time`. `best_lap` was left reading straight from `telemetry_summary`,
so lap-based races (Circuit, Trail, Scramble, etc.) kept recording the
telemetry best lap, which could read higher than the scoreboard's value.
Fixed so `best_lap` now mirrors `race_time`'s scoreboard-first, telemetry-fallback
pattern. If you're touching this logic again, verify **both** fields use the
same pattern — they're easy to fix one at a time and forget the other.

## Race Condition Fix — Temp Save and Rename
A race condition existed where the AI computer would attempt to process a screenshot
while it was still being written by the gaming PC over the network share. This was
resolved by:
1. The gaming PC saves the screenshot as a **temporary file** (`_tmp_scoreboard_xxx.png`)
2. The AI computer is configured to **ignore files starting with `_tmp_`**
3. Once the file is fully written, the gaming PC **renames it** to the final filename
4. The AI computer then picks it up and processes it

Do not revert this behavior.

## Known Issue — False Capture on Quit Race
There is a known edge case where quitting a race mid-way can result in a false
result being recorded. This is rare and has been left unresolved intentionally.

**The scenario:**
1. The user quits a race after it has started
2. The game returns to free roam
3. The telemetry listener correctly detects race end (packet timeout)
4. The capture agent starts watching for the scoreboard header
5. If the user hits pause quickly, colored UI elements on the pause menu can be
   mistaken for the scoreboard header (yellow in FH5, lime-green in FH6)
6. A screenshot is taken and processed, posting a result line with bad data

**How to identify a false capture:**
- The car name is the long descriptive string rather than the short scoreboard name
- The screenshot is NOT deleted after processing (normal successful captures are deleted)

**Status:** Rare enough to leave for now. Detection logic will likely need
revisiting as FH6 live gameplay is tested anyway.

**Important:** A previous attempt to fix this by tightening the yellow detection
region broke the normal capture flow and was fully reverted. Do not attempt
detection region changes without extensive testing against normal race completions.

## FH5 Scoreboard Detection — Yellow Banner
FH5 uses a **yellow banner** at the top-left of the race scoreboard to detect when
the end-of-race scoreboard is being displayed.

The game is played in a windowed/streaming mode which adds black bars at the top
and bottom of the screen. This affects where the yellow banner appears as a fraction
of total screen height.

A pixel analysis of a real scoreboard screenshot (2612x1417) confirmed:
- The track name banner yellow pixels are concentrated at **Y: 10-22%** of screen height
- A "Time Remaining" banner also appears at **Y: 80-90%** — excluded by detection region
- Main menu "World Map" yellow text appears at ~50% height — excluded by detection region

Current detection settings in `capture_agent.py` (FH5 branch):
```python
BANNER_COLOR_LOW    = np.array([20,  150, 150])   # HSV lower bound
BANNER_COLOR_HIGH   = np.array([35,  255, 255])   # HSV upper bound
BANNER_REGION_X     = (0.05, 0.45)   # 5% to 45% of screen width
BANNER_REGION_Y     = (0.10, 0.22)   # 10% to 22% of screen height
BANNER_MIN_PIXELS   = 500
```

These values are based on real pixel analysis. Do not change them without re-running
the pixel analysis against a real scoreboard screenshot.

## FH6 Scoreboard Detection — Lime-Green Header
FH6 uses a **lime-green column header row** spanning the full scoreboard table width,
rather than the narrow left-side yellow banner used in FH5.

Current detection settings in `capture_agent.py` (FH6 branch):
```python
BANNER_COLOR_LOW    = np.array([35,  200, 180])   # HSV lower bound
BANNER_COLOR_HIGH   = np.array([50,  255, 255])   # HSV upper bound
BANNER_REGION_X     = (0.15, 0.85)   # green header spans full table width
BANNER_REGION_Y     = (0.18, 0.30)   # header sits lower than FH5 banner
BANNER_MIN_PIXELS   = 1500           # larger region = higher threshold
```

**These values were derived from pre-release screenshots and may need tuning against
live FH6 gameplay.** Check `gaming-pc\logs\capture_agent.log` for pixel counts if
the scoreboard isn't being detected. Adjust `BANNER_COLOR_LOW`, `BANNER_COLOR_HIGH`,
and `BANNER_MIN_PIXELS` as needed after observing live captures.

## PI Class Ranges (Per Game)

FH6 introduces the **R** class and removes **E**. PI boundaries also shift.
The correct ranges are applied automatically based on the `--game` flag.

| Class | FH5 PI Range | FH6 PI Range |
|-------|--------------|--------------|
| E     | ≤ 100        | —            |
| D     | 101 – 500    | 100 – 400    |
| C     | 501 – 600    | 401 – 500    |
| B     | 601 – 700    | 501 – 600    |
| A     | 701 – 800    | 601 – 700    |
| S1    | 801 – 900    | 701 – 800    |
| S2    | 901 – 998    | 801 – 900    |
| R     | —            | 901 – 998    |
| X     | 999          | 999          |

## Google Sheets Structure

Two separate spreadsheets — one per game (FH5 and FH6). Both use the same tab structure.

**Results tab columns:**
Date | Race ID | Position | Car | Class | Race Type | Track | Total Racers | Best Lap | Race Time | Notes

**Opponents tab columns:**
Race ID | Track | Position | Gamertag | Car | Class | PI | Best Lap | Race Time | Gap To Me

Only opponents who finished *ahead* of the user are logged. Best Lap is blank for
point-to-point and trail races (no laps to track). Spec Race rows have Notes = "Spec Race".

**Cars tab** (inventory, managed manually + by Apps Script):
FH6 | Year | MFG | Model | Car Name | D | OC | Class | Type | Fav | Notes | Tuner | Tune | Races | Wins | Win Rate

**Races and Wins** (columns N and O) are updated automatically after every race by
`sheets_writer.py`. Matching uses the composite key **(Car Name, Class, Type)** —
the same car tuned to different classes or surface types (Road vs Dirt) is tracked separately.

**Win Rate is column P, always** — `forza_car_updater.gs`'s `ensureWinRateAtColumnP_`
pins it there rather than letting it land wherever auto-append last put it. It's
fully recomputed from scratch every run (`wins / races`), so relocating it is
lossless: if it's ever found somewhere else (e.g. an older sheet), that column is
deleted and a fresh one inserted at P, then repopulated in the same run.

**Google Apps Script** (`Forza Car Updater`) runs separately (manually or on a
daily schedule) and manages the Cars tab's Win Rate column plus the Fav flag
logic and the **Best by Track+Class** tab. Run from the
**Forza → Update Cars** menu in the spreadsheet. It skips Spec Race rows.

**Best Time and Last Raced are NOT written to the Cars tab** (removed
2026-08-23, `applyUpdates_`/`COMPUTED_COLS`) — those are per-(Track,Class)
values and exist only on the **Best by Track+Class** tab. They used to be
auto-appended to the Cars tab too, and would silently come back if manually
deleted there since the script treated them as columns it owns; removing them
from `COMPUTED_COLS` stops that.

**Best by Track+Class does NOT have Races or Win Rate columns** (removed
2026-08-23) — it only ever shows which car holds the record at each
(Track, Class) and that car's best time. Races/Win Rate are per-car totals
and exist in exactly one place: the Cars tab. The two tabs previously had a
column both called "Races" with different meanings (one per-car total, one
tied to a specific track/class record), which was a real source of confusion
about which number was even being looked at.

**Races and Wins are written by both `sheets_writer.py`'s `update_car_stats()`
(real-time, after every race) and the Apps Script (nightly batch run) — and this
is intentional, not a bug to "fix" by picking one.** Python can only ever update
a Cars row that already exists, so a car raced for the first time has nowhere
to write to until a Cars row for it exists. The Apps Script is the only piece
that can auto-add that row (`appendNewCars_`, keyed on the abbreviated Car Name
exactly as it appears on the scoreboard/Results tab) — which is why the normal
workflow is race all night, then run **Forza → Update Cars** once at the end so
new cars get both a Cars row and a populated Races/Wins in the same pass.

The Apps Script used to write Races but never Wins. Since it does a full
rebuild every run and zeroes any Cars row it fails to match that run (correct
behavior for an authoritative recompute — e.g. after a Results row is deleted),
zeroing only one of the two columns let them drift out of sync with each other
over time. Fixed 2026-08-21: `forza_car_updater.gs` now writes Races and Wins
together, always zeroed or populated as a pair, so they can't disagree.

**If a car's Races/Wins are blank after running Update Cars, check the Notes
column on its Results rows first** — `Spec Race`/`Touge`/`Time Attack` rows are
intentionally excluded from the tally (see Filtering Rules below) in both
`update_car_stats()` and the Apps Script's `readResults_()`. That's usually the
answer before suspecting a matching-key bug. If it's not that, run
**Forza → Diagnose Matching** (writes a **Match Debug** tab) to see exactly
which (Car Name, Class, Type) key a Results row produced and whether/why it
missed the Cars tab row.

**Every Cars tab column the Apps Script touches is found by header text, never
by position.** `resolveCol_()` in `forza_car_updater.gs` looks a column up by
name and re-verifies the live header cell still says exactly that right
before writing to it, throwing a specific error naming the mismatch if not.
This means the Cars tab's column *order* can differ from what's documented
above, gain new columns, or differ between the FH5 and FH6 spreadsheets, and
the Apps Script still finds the right one — only the header *text* has to
match. `sheets_writer.py`, by contrast, still addresses Races/Wins by
hardcoded column letters (`N`/`O`) — if the Cars tab layout ever genuinely
diverges from A–O as documented above, Python's writes would silently land in
the wrong place with no error, unlike the Apps Script's side.

**On a Car Name match, the Apps Script auto-fills Year/MFG/Model from any
other row with that same Car Name** — both when auto-adding a brand-new
(Car Name, Class, Type) row (`appendNewCars_`) and, as of 2026-08-23, as a
backfill pass in `applyUpdates_` for any *existing* row still missing one of
those three fields. It never overwrites a value that's already present. The
only case that still needs manual entry is a genuinely new Car Name with no
existing catalog row to copy from.

## FH6 Telemetry Probe Logging
Packet samples are saved to `ai-computer\logs\packet_samples\` — one pair of files
per race session — to help verify FH6 packet structure on first live play:

- `packet_FH6_YYYYMMDD_HHMMSS_Xb.bin` — raw UDP bytes for offline analysis
- `packet_FH6_YYYYMMDD_HHMMSS_Xb.txt` — every 4-byte-aligned offset decoded as
  float32 and int32, with known fields labelled

Compare an FH5 `.txt` against an FH6 `.txt` to spot offset shifts or new fields.

The telemetry log also emits **WARNING** entries if:
- The UDP packet size changes between sessions (protocol shift signal)
- A parsed field value is outside physical bounds (speed > 350mph, position > 24, PI out of 100–999 range)

FH6 packet structure is assumed unchanged from FH5. These logs will surface any
differences on the first FH6 play session.

## Tech Stack
- Language: Python
- Data storage: Google Sheets (separate spreadsheet per game version)
- AI scraping: Claude API — model `claude-sonnet-4-6` (set in `config.py`)
- Version control: GitHub
- Games supported: Forza Horizon 5 and Forza Horizon 6

## Git / GitHub Notes
- Repository is hosted on GitHub
- Both computers clone from the same repo
- Either computer can be fully restored from GitHub

**Active development branch: `main`**
- All current work happens on `main`
- Always commit and push to `main`: `git push origin main` / `git pull origin main`
- If a large feature requires a branch, create one, but merge back to main when done and do not continue developing on the feature branch

**Working directory: `C:\StreamAssistant`**
- Always make edits, commits, and pushes directly in `C:\StreamAssistant`
- Ignore any Claude-created worktrees (paths like `.claude\worktrees\...`) — work there causes confusion and requires extra steps to get changes onto the right branch

## What's Not Yet Built
- **FH6 detection tuning** — scoreboard detection values for FH6 are from pre-release screenshots; verify and tune against live FH6 gameplay (check capture_agent.log for pixel counts)
- **FH6 telemetry offsets** — packet structure assumed unchanged from FH5; verify on first live session using packet samples
- **Online/AI race flag** — planned Results tab column to distinguish Open online races from AI races, enabling separate win-rate tracking
- **Car-change detection** — `car_ordinal` changes in telemetry when the user switches cars in free roam; would trigger stream overlay events without needing a screen scraper
- **Stream overlays — car stats on car change, race summary at race end** still not built. The personal-record-alert overlay described below is built (2026-09-11) but not yet tested live.
- **Module 5: Chat moderation** — Claude API reading Twitch/YouTube chat simultaneously; deferred until streaming is established
- **Stream Deck button color change** — dynamic green/red state indicator, tracked separately
