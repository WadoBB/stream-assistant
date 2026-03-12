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

# --- Anthropic API ---
# Key is loaded from credentials\.env - never hardcode here
ANTHROPIC_MODEL         = "claude-sonnet-4-6"

# --- Google Sheets ---
SHEETS_CREDENTIALS      = r"C:\StreamAssistant\ai-computer\credentials\google_sheets.json"
SHEETS_SPREADSHEET_ID   = "1Rd1V7z86sJFMumtativB6Tv6kZfBcwbD0JyWFhiy7gY"   # paste your Sheet ID
RESULTS_TAB             = "Results"
OPPONENTS_TAB           = "Opponents"

# --- Ollama (future use) ---
OLLAMA_MODEL            = "llama3.1:latest"
OLLAMA_HOST             = "http://localhost:11434"
