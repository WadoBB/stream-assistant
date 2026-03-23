# Stream Assistant - Forza Race Tracker

Automatically captures Forza Horizon race results and logs them to Google Sheets.
Built for a two-computer streaming setup — a Gaming PC and a dedicated AI computer.

---

## What It Does

When you finish a Forza Horizon race with the pipeline running:

1. Forza telemetry (UDP) signals the race ended
2. The gaming PC captures a screenshot of the scoreboard
3. Claude AI reads the screenshot and extracts structured data
4. Google Sheets is updated automatically — Results tab and Opponents tab

You control it with a single Stream Deck button. Screenshots are deleted
automatically after successful extraction — nothing accumulates on disk.

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
│   └── toggle_stream_assistant.bat   ← Stream Deck button action
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

If IPs change, update `ai-computer/config.py` and `gaming-pc/toggle_stream_assistant.bat`.

---

## Data Flow

```
Forza (Gaming PC)
  └─ UDP telemetry (port 9999) ──────────► telemetry_listener.py (AI Computer)
                                               │ race ended
                                               ▼
                                           main.py sends RACE_END trigger
  capture_agent.py (Gaming PC) ◄──────────── UDP (port 9998)
  │ detects yellow scoreboard banner
  │ takes screenshot
  └─ saves to \\AI-Computer\StreamCaptures\
                                               │
                                           results_extractor.py picks up PNG
                                               │ sends to Claude API
                                               ▼
                                           sheets_writer.py
                                               │
                                           Google Sheets updated
                                           Screenshot deleted
```

---

## Google Sheets

**Results tab columns:**
Date | Race ID | Position | Car | Class | Race Type | Track | Total Racers | Best Lap | Race Time | Notes

**Opponents tab columns:**
Race ID | Track | Position | Gamertag | Car | Class | PI | Best Lap | Race Time | Gap To Me

Only opponents who finished *ahead* of you are logged. Best Lap is blank for
point-to-point races (no laps to track).

---

## Session Workflow

| Step | Where | Action |
|---|---|---|
| AI computer boots | Automatic | controller.py starts silently |
| Ready to log | Stream Deck button | Starts everything |
| Race | Play normally | Pipeline runs in background |
| Done logging | Stream Deck button | Stops everything |

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

You will also need to update `toggle_stream_assistant.bat` to use the full path.
Find this line:
```
start "Capture Agent" /min cmd /c "cd C:\StreamAssistant\gaming-pc && python capture_agent.py"
```
Replace `python` with the full path:
```
start "Capture Agent" /min cmd /c "cd C:\StreamAssistant\gaming-pc && C:\Users\Benny\AppData\Local\Python\bin\python.exe capture_agent.py"
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
```

**Gaming PC:**
```
cd C:\StreamAssistant
git clone https://github.com/YOUR_USERNAME/stream-assistant.git .
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
8. Open your Forza Google Sheet → Share with the service account email (Editor)

---

### Step 6 — Update config.py (AI computer)

Open `C:\StreamAssistant\ai-computer\config.py` and verify:

```python
AI_COMPUTER_IP        = "192.168.137.230"   # update if changed
GAMING_PC_IP          = "192.168.137.63"    # update if changed
SHEETS_SPREADSHEET_ID = "your-sheet-id"    # from Google Sheet URL (between /d/ and /edit)
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

### Step 11 — Stream Deck Button (Gaming PC)

1. Open Stream Deck software
2. Drag **System: Open** onto your chosen button
3. Set App/File to: `C:\StreamAssistant\gaming-pc\toggle_stream_assistant.bat`
4. Label it "SA Toggle"

---

### Step 12 — Test the Pipeline

From gaming PC Command Prompt:
```
curl http://192.168.137.230:5000/status
```
Should return: `{"status": "stopped"}`

Press Stream Deck button, then:
```
curl http://192.168.137.230:5000/status
```
Should return: `{"status": "running", ...}`

Capture Agent window opens on gaming PC.
main.py window opens on AI computer.

Run a Forza race, check Google Sheet for new rows.
Press Stream Deck button again to stop.

---

## Troubleshooting

**Status always stopped after toggle**
Run `python main.py` directly on AI computer to see the error.
Most common cause: missing or malformed `.env` file or `google_sheets.json`.

**No Capture Agent window after toggle**
Check IP in `toggle_stream_assistant.bat` matches AI computer.
Test curl directly: `curl http://192.168.137.230:5000/toggle`

**Screenshot never taken**
Yellow banner detection may need HSV tuning for your monitor.
Check `gaming-pc\logs\capture_agent.log` for errors.

**Google Sheets not updating**
Verify `google_sheets.json` exists in credentials folder.
Verify sheet is shared with service account email.
Verify SPREADSHEET_ID in config.py is correct (between /d/ and /edit in URL).

**Controller unreachable from gaming PC**
Check Windows Firewall port 5000 rule on AI computer.
Test locally first: `curl http://127.0.0.1:5000/health`

---

## Log Files

All logs rotate automatically — max 5MB per file, 3 backups kept (~20MB total max).

| Log | Location | Contains |
|---|---|---|
| stream_assistant.log | ai-computer\logs\ | Main pipeline activity |
| telemetry.log | ai-computer\logs\ | Race detection detail |
| controller.log | ai-computer\logs\ | Toggle history |
| capture_agent.log | gaming-pc\logs\ | Screenshot detection |

---

## What's Not Yet Built

- **Module 5: Chat moderation** — Claude API reading Twitch/YouTube chat simultaneously, deferred until streaming is established
- **Stream Deck button color change** — dynamic green/red state indicator, tracked separately

---

## Security Notes

`.env` and `google_sheets.json` are in `.gitignore` and must never be committed.
If either is accidentally committed, rotate immediately:
- Anthropic: console.anthropic.com → API Keys → delete and recreate
- Google: console.cloud.google.com → IAM → Service Accounts → Keys → delete and recreate
