# Stream Assistant - Forza Race Tracker

Automatically captures Forza Horizon race results and logs them to Google Sheets.
Built for a two-computer streaming setup — a Gaming PC and a dedicated AI computer.
Supports both **Forza Horizon 5** and **Forza Horizon 6** via a game version switch.

---

## What It Does

When you finish a Forza Horizon race with the pipeline running:

1. Forza telemetry (UDP) signals the race ended
2. The gaming PC captures a screenshot of the scoreboard
3. Claude AI reads the screenshot and extracts structured data
4. Google Sheets is updated automatically — Results tab and Opponents tab

You control it with a Stream Deck button — one for FH5, one for FH6.
Screenshots are deleted automatically after successful extraction — nothing accumulates on disk.

---

## Repository Structure

```
stream-assistant/
├── ai-computer/                 ← copy to C:\StreamAssistant\ai-computer\ on AI computer
│   ├── main.py                  ← coordinator, starts all modules
│   ├── controller.py            ← Flask HTTP server for Stream Deck toggle
│   ├── telemetry_listener.py    ← reads Forza UDP telemetry
│   ├── results_extractor.py     ← sends screenshot to Claude, extracts data
│   ├── sheets_writer.py         ← writes to Google Sheets
│   ├── config.py                ← all settings in one place
│   ├── start_controller_on_boot.bat  ← place in Windows Startup folder
│   └── credentials/
│       └── .env.template        ← copy to .env and fill in your API key
│
├── gaming-pc/                   ← copy to C:\StreamAssistant\gaming-pc\ on gaming PC
│   ├── capture_agent.py         ← detects scoreboard, takes screenshot
│   ├── toggle_fh5.bat           ← Stream Deck button for Forza Horizon 5
│   └── toggle_fh6.bat           ← Stream Deck button for Forza Horizon 6
│
├── .gitignore
└── README.md
```

Each computer only uses its own subfolder. Clone the full repo on both
machines but only run files from the relevant subfolder.

---

## Network Configuration

| Computer | IP | Role |
|---|---|---|
| Gaming PC | 192.168.137.63 | Forza, capture agent, Stream Deck |
| AI Computer | 192.168.137.230 | All intelligence, controller, sheets |

If IPs change, update `ai-computer/config.py` and both bat files in `gaming-pc/`.

---

## Game Version Switching

The game version is selected by which Stream Deck button you press — no config changes needed.

| Button | Bat File | Tracks | Writes To |
|---|---|---|---|
| FH5 | toggle_fh5.bat | Forza Horizon 5 | Forza Horizon 5 spreadsheet |
| FH6 | toggle_fh6.bat | Forza Horizon 6 | Forza Horizon 6 spreadsheet |

The `--game` flag is passed automatically through the entire pipeline:
controller → main.py → telemetry listener, results extractor, sheets writer,
and capture agent all switch behavior based on it.

**Switching mid-session:** Press the running button to stop, then press the other
button to start in the new mode.

---

## Data Flow

```
Forza (Gaming PC)
  └─ UDP telemetry (port 9999) ──────────► telemetry_listener.py (AI Computer)
                                               │ race ended
                                               ▼
                                           main.py sends RACE_END trigger
  capture_agent.py (Gaming PC) ◄──────────── UDP (port 9998)
  │ detects scoreboard (FH5: yellow banner / FH6: lime-green header)
  │ takes screenshot
  └─ saves to \\AI-Computer\StreamCaptures\
                                               │
                                           results_extractor.py picks up PNG
                                               │ sends to Claude API
                                               ▼
                                           sheets_writer.py
                                               │
                                           Google Sheets updated (FH5 or FH6 sheet)
                                           Screenshot deleted
```

---

## Google Sheets

Two separate spreadsheets — one per game. Both use the same tab structure:

**Results tab columns:**
Date | Race ID | Position | Car | Class | Race Type | Track | Total Racers | Best Lap | Race Time | Notes

**Opponents tab columns:**
Race ID | Track | Position | Gamertag | Car | Class | PI | Best Lap | Race Time | Gap To Me

**Cars tab** (inventory, managed manually + by Apps Script):
FH6 | Year | MFG | Model | Car Name | D | OC | Class | Type | Fav | Notes | Tuner | Tune | Races | Wins

Only opponents who finished *ahead* of you are logged. Best Lap is blank for
point-to-point and trail races (no laps to track).

**Races and Wins** (Cars tab columns N and O) are updated automatically after
every race by `sheets_writer.py`. Matching uses the composite key
**(Car Name, Class, Type)** — the same car tuned to different classes or
surface types (Road vs Dirt) is tracked separately.

A Google Apps Script (`Forza Car Updater`) runs separately (manually or on a
daily schedule) and manages additional computed columns — Win Rate, Best Time,
Last Raced — plus the Fav flag logic and the **Best by Track+Class** tab.
Run it from the **Forza → Update Cars** menu in the spreadsheet.

---

## PI Class Ranges

PI class boundaries differ between games. FH6 introduces the **R** class and
removes **E**. The correct ranges are applied automatically based on the game
version selected at startup.

| Class | FH5 PI Range | FH6 PI Range |
|---|---|---|
| E | ≤ 100 | — |
| D | 101 – 500 | 100 – 400 |
| C | 501 – 600 | 401 – 500 |
| B | 601 – 700 | 501 – 600 |
| A | 701 – 800 | 601 – 700 |
| S1 | 801 – 900 | 701 – 800 |
| S2 | 901 – 998 | 801 – 900 |
| R | — | 901 – 998 |
| X | 999 | 999 |

---

## Session Workflow

| Step | Where | Action |
|---|---|---|
| AI computer boots | Automatic | controller.py starts silently |
| Ready to log FH5 | Stream Deck FH5 button | Starts everything in FH5 mode |
| Ready to log FH6 | Stream Deck FH6 button | Starts everything in FH6 mode |
| Race | Play normally | Pipeline runs in background |
| Done logging | Same Stream Deck button | Stops everything |

---

## Recreation Instructions

Follow these steps after a crash or fresh Windows install.

---

### Step 1 — Install Python (both computers)

Download Python 3.13+ from python.org.
During install, check **Add Python to PATH**.

Verify: `python --version`

**If `python` opens the Microsoft Store instead of showing a version number:**

Windows sometimes installs a stub that redirects to the Store. Fix it:
1. Open **Settings → Apps → Advanced app settings → App execution aliases**
2. Turn OFF both **Python** and **Python3** aliases
3. Open a **new** Command Prompt and try `python --version` again

**If Python still isn't found after disabling the alias:**

Locate it manually:
```
where python
```
Or check this common non-standard install location:
```
dir C:\Users\%USERNAME%\AppData\Local\Python\bin\
```
If found there, note the full path (e.g. `C:\Users\Benny\AppData\Local\Python\bin\python.exe`)
and use it anywhere these instructions say `python`.

You will also need to update both bat files to use the full path.
Find this line in `toggle_fh5.bat` and `toggle_fh6.bat`:
```
start "Capture Agent" /min cmd /c "cd C:\StreamAssistant\gaming-pc && python capture_agent.py --game FH5"
```
Replace `python` with the full path:
```
start "Capture Agent" /min cmd /c "cd C:\StreamAssistant\gaming-pc && C:\Users\Benny\AppData\Local\Python\bin\python.exe capture_agent.py --game FH5"
```

---

### Step 2 — Create Base Folders

Only the top-level folders need to exist before cloning. The code creates
`captures/`, `captures/processed/`, and `logs/` automatically on first run.

**AI computer:**
```
mkdir C:\StreamAssistant\ai-computer
mkdir C:\StreamAssistant\ai-computer\credentials
```

**Gaming PC:**
```
mkdir C:\StreamAssistant\gaming-pc
```

---

### Step 3 — Clone This Repository (both computers)

**AI computer:**
```
cd C:\StreamAssistant
git clone https://github.com/YOUR_USERNAME/stream-assistant.git .
git checkout fh6
```

**Gaming PC:**
```
cd C:\StreamAssistant
git clone https://github.com/YOUR_USERNAME/stream-assistant.git .
git checkout fh6
```

---

### Step 4 — Install Python Packages

**AI computer:**
```
pip install anthropic google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client pillow opencv-python numpy flask python-dotenv
```

**Gaming PC:**
```
pip install pillow opencv-python numpy
```

---

### Step 5 — Restore Secret Credentials (AI computer only)

These files are NOT in the repository. You must recreate them after a crash.

#### Anthropic API Key

1. Go to **console.anthropic.com** → API Keys
2. Delete any compromised keys, create a new one named "Forza Stream Assistant"
3. Copy the key immediately — you cannot view it again
4. On AI computer, open Notepad and create `C:\StreamAssistant\ai-computer\credentials\.env`:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```
5. Save. Verify no hidden .txt extension:
```
dir C:\StreamAssistant\ai-computer\credentials\
```
Must show `.env` not `.env.txt`. If it shows `.env.txt`:
```
ren C:\StreamAssistant\ai-computer\credentials\.env.txt .env
```

#### Google Sheets Service Account

1. Go to **console.cloud.google.com**
2. Select or recreate project "Forza Stream Assistant"
3. Enable **Google Sheets API** and **Google Drive API**
4. Go to Credentials → Service Accounts → your service account
5. Keys tab → Add Key → Create New Key → JSON → download
6. Rename to `google_sheets.json`
7. Move to `C:\StreamAssistant\ai-computer\credentials\`
8. Open `google_sheets.json` and copy the `client_email` value
9. Share **both** the FH5 and FH6 Google Sheets with that email address (Editor access)
   - Uncheck "Notify people" when sharing — the service account cannot receive email

---

### Step 6 — Update config.py (AI computer)

Open `C:\StreamAssistant\ai-computer\config.py` and verify:

```python
AI_COMPUTER_IP       = "192.168.137.230"   # update if changed
GAMING_PC_IP         = "192.168.137.63"    # update if changed
FH5_SPREADSHEET_ID   = "your-fh5-sheet-id"  # from Google Sheet URL (between /d/ and /edit)
FH6_SPREADSHEET_ID   = "your-fh6-sheet-id"  # from Google Sheet URL (between /d/ and /edit)
```

---

### Step 7 — Configure Forza Data Out (Gaming PC, in-game)

Settings → HUD and Gameplay → Data Out:
- Data Out: **On**
- Data Out IP Address: **192.168.137.230**
- Data Out IP Port: **9999**
- Data Out Packet Format: **Car Dash** (if shown)

---

### Step 8 — Set Up Windows Shared Folder (AI computer)

1. Right-click `C:\StreamAssistant\ai-computer\captures\` → Properties → Sharing → Advanced Sharing
2. Check **Share this folder**
3. Share name: `StreamCaptures`
4. Click **Permissions** → give Everyone Read/Write access
5. Click OK and Apply

**Map as a network drive on the Gaming PC (recommended)**

Mapping as Z: makes it easy to verify the connection is working at any time
and ensures the Gaming PC can reliably write screenshots to the AI computer.

1. On the Gaming PC, open File Explorer → click **This PC** in the left panel
2. Click **Map network drive** in the toolbar
3. Drive letter: **Z:**
4. Folder: `\\192.168.137.230\StreamCaptures`
5. Check **Reconnect at sign-in**
6. Click **Finish** — it should open the folder automatically

If prompted for credentials, enter the username and password of the account
on the AI computer.

Verify from Gaming PC Command Prompt:
```
dir Z:\
```
Should list the contents of the captures folder without error.

---

### Step 9 — Windows Firewall (AI computer)

Allow port 5000 for the Flask controller:

1. Windows Defender Firewall → Advanced Settings → Inbound Rules → New Rule
2. Port → TCP → 5000 → Allow the connection → All profiles
3. Name: `Stream Assistant Controller`

---

### Step 10 — Controller Auto-Start (AI computer)

1. Press **Win+R** → type `shell:startup` → Enter
2. Copy `C:\StreamAssistant\ai-computer\start_controller_on_boot.bat` into that folder
3. Reboot or double-click the bat to start it now

Verify:
```
curl http://127.0.0.1:5000/health
```
Should return: `{"status": "ok"}`

---

### Step 11 — Stream Deck Buttons (Gaming PC)

Create two buttons — one for each game:

**FH5 button:**
1. Open Stream Deck software
2. Drag **System: Open** onto your chosen button
3. Set App/File to: `C:\StreamAssistant\gaming-pc\toggle_fh5.bat`
4. Label it "FH5"

**FH6 button:**
1. Drag **System: Open** onto a second button
2. Set App/File to: `C:\StreamAssistant\gaming-pc\toggle_fh6.bat`
3. Label it "FH6"

---

### Step 12 — Test the Pipeline

From gaming PC Command Prompt:
```
curl http://192.168.137.230:5000/status
```
Should return: `{"status": "stopped"}`

Press the FH5 Stream Deck button, then:
```
curl http://192.168.137.230:5000/status
```
Should return: `{"status": "running", "game": "FH5", ...}`

Capture Agent window opens on gaming PC (titled "Capture Agent").
main.py window opens on AI computer showing `Game : FH5`.

Run a Forza race, check the FH5 Google Sheet for new rows.
Press the FH5 button again to stop.

---

## Troubleshooting

**Status always stopped after toggle**
Run `python main.py --game FH5` directly on AI computer to see the error.
Most common cause: missing or malformed `.env` file or `google_sheets.json`.

**No Capture Agent window after toggle**
Check IP in both bat files matches AI computer.
Test curl directly: `curl "http://192.168.137.230:5000/toggle?game=FH5"`

**Screenshot never taken (FH5)**
Yellow banner detection may need HSV tuning for your monitor.
Check `gaming-pc\logs\capture_agent.log` for pixel counts.

**Screenshot never taken (FH6)**
Lime-green header detection values are based on pre-release screenshots and
may need tuning against live gameplay. Check `gaming-pc\logs\capture_agent.log`.
Adjust `BANNER_COLOR_LOW`, `BANNER_COLOR_HIGH`, and `BANNER_MIN_PIXELS` in
`capture_agent.py` if needed.

**Google Sheets not updating**
Verify `google_sheets.json` exists in credentials folder.
Verify both sheets are shared with the service account email (Editor access).
Verify `FH5_SPREADSHEET_ID` and `FH6_SPREADSHEET_ID` in config.py are correct.

**Wrong spreadsheet being updated**
Confirm the bat file used matches the game being played.
Check AI computer console — it logs `Game : FH5` or `Game : FH6` on startup.

**Controller unreachable from gaming PC**
Check Windows Firewall port 5000 rule on AI computer.
Test locally first: `curl http://127.0.0.1:5000/health`

---

## Log Files

All logs rotate automatically — max 5MB per file, 3 backups kept (~20MB total max).

| Log | Location | Contains |
|---|---|---|
| stream_assistant.log | ai-computer\logs\ | Main pipeline activity |
| telemetry.log | ai-computer\logs\ | Race detection, field anomaly warnings |
| controller.log | ai-computer\logs\ | Toggle history |
| capture_agent.log | gaming-pc\logs\ | Screenshot detection |

**Packet samples** are saved to `ai-computer\logs\packet_samples\` — one pair
of files per race session:

- `packet_FH6_YYYYMMDD_HHMMSS_Xb.bin` — raw UDP bytes for offline analysis
- `packet_FH6_YYYYMMDD_HHMMSS_Xb.txt` — human-readable field scan: every
  4-byte-aligned offset decoded as float32 and int32, known fields labelled

These are primarily useful during FH6 launch to verify packet structure and
discover any new fields. Compare an FH5 `.txt` against an FH6 `.txt` to spot
offset shifts or new data.

The telemetry log also emits **WARNING** entries if:
- The UDP packet size changes between sessions (protocol shift signal)
- A parsed field value is outside physical bounds (speed > 350mph, position > 24, PI out of 100–999 range)

---

## Race Type Detection and Filtering

Race type is derived from keywords in the track name returned by Claude. The map is evaluated top-to-bottom and the first match wins.

| Keyword in Track Name | Race Type Recorded |
|---|---|
| CROSS COUNTRY CIRCUIT | Cross-Country Circuit |
| CROSS COUNTRY | Cross-Country |
| SCRAMBLE | Dirt Scramble |
| TRAIL | Dirt Trail |
| CIRCUIT | Road Circuit |
| SPRINT | Road Sprint |
| DRAG | Drag Race |
| *(no match)* | Street Race |

**Non-competitive races (recorded normally, flagged so they don't skew win rate):**

| Condition | Notes value |
|---|---|
| `race_mode = "Spec Race"` (all racers same car + PI) | `"Spec Race"` |
| `total_racers = 2` | `"Touge"` |
| `total_racers < 2`, or `race_mode = "Time Attack"` | `"Time Attack"` |

These races are still recorded on the Results tab — a lap/race time is a valid personal best even without real opponents — but rows with one of these Notes values are excluded from the Opponents tab and from the Races/Wins tally in `sheets_writer.py`'s `update_car_stats()`, since position is always 1 in these events and counting them would inflate win rate.

### Known Limitation — Short Drag Races Are Skipped

The telemetry listener requires a minimum race wall-clock duration of **30 seconds** before recording. This filter exists to discard false positives from loading screens, replays, and early quits.

Drag races at the highest car class finish in roughly 11–17 seconds — well under this threshold — so they are discarded before the scoreboard screenshot is even requested.

**Current status:** Accepted for personal use. Drag racing is rarely done outside of storyline requirements and weekly challenges.

**If this ever needs to be fixed:** The threshold is `MIN_RACE_DURATION_SECONDS = 30` in `telemetry_listener.py`. The cleanest fix would be to detect the drag race signature in telemetry (very high speed, very short duration, no laps) and bypass the duration check for that specific case. A simple threshold reduction risks letting early quits through.

---

## What's Not Yet Built

- **FH6 detection tuning** — scoreboard detection values (banner color, region
  coordinates) are based on pre-release information. Verify and tune against live
  FH6 gameplay on launch. Check `capture_agent.log` for pixel counts if the
  scoreboard isn't being detected.
- **FH6 telemetry offsets** — packet structure assumed unchanged from FH5. The
  probe logging (packet samples + field anomaly warnings) will surface any
  differences on first play session. Verify offsets against the FH6 UDP spec.
- **Online/AI race flag** — a planned column in the Results tab to distinguish
  Open online races from AI races, enabling separate win-rate tracking.
- **Car-change detection** — `car_ordinal` changes in telemetry when you switch
  cars in free roam; detecting this would trigger stream overlay events (car
  info popup, stats display) without needing a screen scraper.
- **Stream overlays** — OBS browser-source overlays fed by a local JSON file:
  car stats on car change, race summary at race end, personal record alerts.
- **Module 5: Chat moderation** — Claude API reading Twitch/YouTube chat
  simultaneously; deferred until streaming is established.
- **Stream Deck button color change** — dynamic green/red state indicator,
  tracked separately.

---

## Security Notes

`.env` and `google_sheets.json` are in `.gitignore` and must never be committed.
If either is accidentally committed, rotate immediately:
- Anthropic: console.anthropic.com → API Keys → delete and recreate
- Google: console.cloud.google.com → IAM → Service Accounts → Keys → delete and recreate
