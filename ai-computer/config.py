# =============================================================
# Stream Assistant - Configuration
# AI COMPUTER only - do not copy to gaming PC
# =============================================================

# --- Network Settings ---
AI_COMPUTER_IP          = "192.168.137.230"
GAMING_PC_IP            = "192.168.137.63"

# --- Telemetry Settings ---
TELEMETRY_PORT          = 9999          # Must match Forza Data Out port

# --- Capture Agent ---
CAPTURE_AGENT_PORT      = 9998          # Port capture_agent.py listens on

# --- Controller ---
CONTROLLER_PORT         = 5000          # Flask HTTP port for Stream Deck toggle

# --- File Paths ---
BASE_FOLDER             = r"C:\StreamAssistant\ai-computer"
CAPTURES_FOLDER         = r"C:\StreamAssistant\ai-computer\captures"
PROCESSED_FOLDER        = r"C:\StreamAssistant\ai-computer\captures\processed"
LOGS_FOLDER             = r"C:\StreamAssistant\ai-computer\logs"
CREDENTIALS_FOLDER      = r"C:\StreamAssistant\ai-computer\credentials"

# --- Stream Overlay (personal record alert) ---
# controller.py serves OVERLAY_HTML at /overlay and OVERLAY_STATE_FILE's
# contents at /overlay/state; sheets_writer.py writes OVERLAY_STATE_FILE
# whenever a race beats the cached Best by Track+Class record. Point an OBS
# Browser Source at http://<AI_COMPUTER_IP>:<CONTROLLER_PORT>/overlay.
OVERLAY_FOLDER          = r"C:\StreamAssistant\ai-computer\overlay"
OVERLAY_HTML            = r"C:\StreamAssistant\ai-computer\overlay\index.html"
OVERLAY_STATE_FILE      = r"C:\StreamAssistant\ai-computer\overlay\state.json"
OVERLAY_SOUND_FILE      = r"C:\StreamAssistant\ai-computer\overlay\cheer.mp3"

# --- Stream Overlay (car card) ---
# Separate from the record-alert overlay above - triggered by car_ordinal
# changing in telemetry (any time you select a different car, not tied to a
# race), not by a race result. controller.py serves CAR_CARD_HTML at
# /car_card, CAR_CARD_STATE_FILE's contents at /car_card/state, and images
# from CAR_IMAGES_FOLDER (named "<ordinal>.png") at /car_card/image/<ordinal>,
# falling back to CAR_CARD_DEFAULT_IMAGE if that ordinal has no image yet.
CAR_CARD_HTML           = r"C:\StreamAssistant\ai-computer\overlay\car_card.html"
CAR_CARD_STATE_FILE     = r"C:\StreamAssistant\ai-computer\overlay\car_card_state.json"
CAR_IMAGES_FOLDER       = r"C:\StreamAssistant\ai-computer\overlay\car_images"
CAR_CARD_DEFAULT_IMAGE  = r"C:\StreamAssistant\ai-computer\overlay\car_images\default_shadow.svg"
CAR_CARD_SOUND_FILE     = r"C:\StreamAssistant\ai-computer\overlay\rev.mp3"

# --- Personal monitor dashboard (not an OBS source) ---
# Stacks both overlay pages (record alert on top, car card below) in one
# page via iframes, for the streamer's own second-monitor viewing - not
# meant to be captured as a Browser Source itself (both pages still render
# with their own transparent backgrounds; MONITOR_HTML just gives them a
# dark background to be visible outside OBS). controller.py serves it at
# /monitor. See gaming-pc/open_stream_monitor.bat for launching it sized
# and positioned for a narrow secondary display (e.g. Corsair Xeneon Edge
# run in portrait).
MONITOR_HTML            = r"C:\StreamAssistant\ai-computer\overlay\monitor.html"

# --- OBS auto-refresh (obs-websocket on the gaming PC) ---
# controller.py presses a Browser Source's "Refresh cache of current page"
# button remotely whenever an overlay event goes unacknowledged by the OBS
# copy of the page - automating the manual refresh that was always the fix
# for the OBS/CEF render-stall bug (see docs/OBS overlay reliability plan.md
# and TODO.md). Requires OBS > Tools > WebSocket Server Settings enabled on
# the gaming PC, an inbound firewall rule for OBS_WS_PORT there, and
# `pip install obsws-python` on this machine. The password is NOT kept here
# (this file is in git) - set OBS_WS_PASSWORD in credentials\.env; while
# it's missing or still the CHANGE_ME placeholder, auto-refresh stays off and
# controller.py just logs the missed acks.
# The source names must match the Browser Source names in OBS exactly
# (case-sensitive) - these are placeholders until confirmed.
OBS_WS_HOST             = GAMING_PC_IP
OBS_WS_PORT             = 4455
OBS_OVERLAY_SOURCE      = "Record Alert"
OBS_CAR_CARD_SOURCE     = "Car Card"
ENV_FILE                = r"C:\StreamAssistant\ai-computer\credentials\.env"

# Local seed/learned database mapping Forza's internal car_ordinal to a car's
# full name (Year+MFG+Model, from a community FH6 ordinal list, seeded once -
# see TODO.md's "Car Card Overlay" entry) and its abbreviated scoreboard name
# (car_name, learned automatically the first time that car is actually
# raced). Never a live external dependency - this file is the local copy.
CAR_ORDINALS_FILE       = r"C:\StreamAssistant\ai-computer\data\car_ordinals.json"

# --- Anthropic API ---
# Key is loaded from credentials\.env - never hardcode here
ANTHROPIC_MODEL         = "claude-sonnet-4-6"

# --- Google Sheets ---
SHEETS_CREDENTIALS      = r"C:\StreamAssistant\ai-computer\credentials\google_sheets.json"
FH5_SPREADSHEET_ID      = "1Kk7Z35YZJQn9ZdkBl5-_Eso_nzKXxCmauszChrME9hE"   # Forza Horizon 5
FH6_SPREADSHEET_ID      = "1Rd1V7z86sJFMumtativB6Tv6kZfBcwbD0JyWFhiy7gY"   # Forza Horizon 6
RESULTS_TAB             = "Results"
OPPONENTS_TAB           = "Opponents"
CARS_TAB                = "Cars"
BEST_BY_TAB             = "Best by Track+Class"

# --- Ollama (future use) ---
OLLAMA_MODEL            = "llama3.1:latest"
OLLAMA_HOST             = "http://localhost:11434"
