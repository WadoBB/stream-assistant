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
| 5000 | TCP      | Flask controller (Stream Deck toggle)    |

If IPs change, update `ai-computer/config.py` and both bat files in `gaming-pc/`.

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
Race type is derived from keywords in the track name returned by Claude.
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
| *(no match)*          | Street Race            |

"CROSS COUNTRY CIRCUIT" must precede "CROSS COUNTRY" and "CIRCUIT" or those
would match first.

## Filtering Rules — Races That Are Skipped
Some race results are automatically skipped and not recorded:

| Condition               | Reason                                                     |
|-------------------------|------------------------------------------------------------|
| `race_mode = "Time Attack"` | Solo timed events — no opponents, not meaningful for comparison |
| `total_racers < 3`      | Filters out Touge races (1v1) and other sub-3-racer events |

**Spec Race** is recorded normally on both Results and Opponents tabs, but the
Notes column is set to `"Spec Race"`. The Google Apps Script (`Forza Car Updater`)
skips car stats updates for these rows because all cars run stock tunes — lap times
are not comparable to tuned race data.

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
Race time is captured from the **scoreboard screenshot**, not from the telemetry.
This was an intentional fix — the last telemetry record before end-of-race did
not reliably match the actual race time shown on the scoreboard.

The code prefers the scoreboard value and falls back to telemetry only if the
scoreboard value is missing. Do not revert this behavior.

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
FH6 | Year | MFG | Model | Car Name | D | OC | Class | Type | Fav | Notes | Tuner | Tune | Races | Wins

**Races and Wins** (columns N and O) are updated automatically after every race by
`sheets_writer.py`. Matching uses the composite key **(Car Name, Class, Type)** —
the same car tuned to different classes or surface types (Road vs Dirt) is tracked separately.

**Google Apps Script** (`Forza Car Updater`) runs separately (manually or on a
daily schedule) and manages computed columns — Win Rate, Best Time, Last Raced —
plus the Fav flag logic and the **Best by Track+Class** tab. Run from the
**Forza → Update Cars** menu in the spreadsheet. It skips Spec Race rows.

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
- **Stream overlays** — OBS browser-source overlays fed by a local JSON file: car stats on car change, race summary at race end, personal record alerts
- **Module 5: Chat moderation** — Claude API reading Twitch/YouTube chat simultaneously; deferred until streaming is established
- **Stream Deck button color change** — dynamic green/red state indicator, tracked separately
