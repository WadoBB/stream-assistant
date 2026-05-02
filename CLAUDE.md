# Forza Race Results Extractor — Project Context

## What This Project Does
This system captures race information from Forza Horizon gaming sessions to compile
performance data over time. Specifically it tracks:
- Car performance by class and race type
- Personal performance trends over time
- Competitor information and cars that outperform the user (for tune research)

Race start and end are detected using telemetry data from the game.
Captured data is stored in Google Sheets. Claude (AI) is used to scrape data from
screenshots. Screenshots are analyzed to extract race results from the scoreboard.

## Architecture — Two-Computer System
The system intentionally runs across two computers to minimize load on the gaming/streaming PC.

**Gaming PC:**
- Runs Forza Horizon
- Captures and saves screenshots of the race scoreboard
- Runs the capture agent
- Streams gameplay

**AI Computer:**
- Monitors for new screenshots
- Uses Claude API to scrape race data from screenshots
- Posts results to Google Sheets
- Hosts the shared network folder that the gaming PC writes screenshots to

**Important:** All code for both sides of the system lives on BOTH computers.
This is intentional — it simplifies GitHub management and means either computer
can be fully restored from GitHub if lost.

## Shared Drive Setup (Critical for Reinstallation)
The AI computer hosts a shared network folder. The gaming PC connects to it via
a mapped drive letter. This means the gaming PC writes screenshots to a local
drive letter that is actually a network path pointing to a folder on the AI computer.

Setup steps during reinstallation:
1. Create a user account on the AI computer for share access
2. Share the captures folder on the AI computer
3. On the gaming PC, map a drive letter to that network share path
4. The capture agent uses that drive letter as its save destination

This is not fully automated and must be set up manually during any reinstallation.

## Race Type — Dirt Trail vs Dirt Point to Point
The preferred race type designation is **Dirt Trail**. At one point the code was
changed to use "Dirt Point to Point" — this was incorrect and has been reverted.
Always use **Dirt Trail**.

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
4. The capture agent starts watching for the scoreboard yellow banner
5. If the user hits pause quickly, yellow UI elements on the pause menu can be
   mistaken for the track name banner
6. A screenshot is taken and processed, posting a result line with bad data

**How to identify a false capture:**
- The car name is the long descriptive string rather than the short scoreboard name
- The screenshot is NOT deleted after processing (normal successful captures are deleted)

**Status:** Rare enough to leave for now. Yellow detection logic will likely need
to be revisited when Forza Horizon 6 launches anyway, as the scoreboard appearance
may change. Any attempted fix must not break the normal capture flow.

**Important:** A previous attempt to fix this by tightening the yellow detection
region broke the normal capture flow and was fully reverted. Do not attempt
yellow detection changes without extensive testing against normal race completions.

## Yellow Area Detection — Handle With Care
A yellow banner at the top-left of the race scoreboard is used to detect when the
end-of-race scoreboard is being displayed. This triggers screenshot capture.

The game is currently played in a windowed/streaming mode which adds black bars
at the top and bottom of the screen. This affects where the yellow banner appears
as a fraction of total screen height.

A pixel analysis of a real scoreboard screenshot (2612x1417) confirmed:
- The track name banner yellow pixels are concentrated at **Y: 10-20%** of screen height
- A "Time Remaining" banner also appears at **Y: 80-90%** — excluded by detection region
- Main menu "World Map" yellow text appears at ~50% height — excluded by detection region

Current detection settings in `capture_agent.py`:
```python
BANNER_REGION_X      = (0.05, 0.50)   # left half of screen
BANNER_REGION_Y      = (0.10, 0.20)   # 10-20% down from top
BANNER_MIN_PIXELS    = 500
```

These values are based on real data. Do not change them without re-running the
pixel analysis against a real scoreboard screenshot.

## Tech Stack
- Language: Python
- Data storage: Google Sheets
- AI scraping: Claude API (via screenshots)
- Version control: GitHub
- Currently testing on: Forza Horizon 5
- Future target: Forza Horizon 6 (early release expected May 2026)

## Git / GitHub Notes
- Repository is hosted on GitHub
- Branch: main
- Both computers clone from the same repo
- Either computer can be fully restored from GitHub
- Use `git push origin main` and `git pull origin main` for syncing between machines

## FH6 Preparation Notes
When Forza Horizon 6 launches, the following will likely need revisiting:
- Yellow banner detection (color, position, shape may change)
- Claude extraction prompt (scoreboard layout may differ)
- Race type detection keywords in `results_extractor.py`
- Telemetry packet offsets (verify against FH6 UDP spec — may be unchanged)
