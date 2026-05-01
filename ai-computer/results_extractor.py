# =============================================================
# Stream Assistant - Module 3: Results Extractor
# Runs on the AI COMPUTER.
# Monitors the captures folder for new scoreboard screenshots,
# sends them to Claude API for data extraction, returns
# structured race results and opponent data.
# =============================================================

import os
import time
import base64
import json
import logging
from datetime import datetime
from anthropic import Anthropic
from dotenv import load_dotenv
from config import CAPTURES_FOLDER, PROCESSED_FOLDER, LOGS_FOLDER

# =============================================================
# Configuration
# =============================================================
MY_GAMERTAG     = "VenomRider63"    # Used to find your row on scoreboard
POLL_INTERVAL   = 1.0               # seconds between folder checks
ENV_FILE        = r"C:\StreamAssistant\ai-computer\credentials\.env"

# Race type detection - ORDER MATTERS, most specific first
RACE_TYPE_MAP = [
    ("CROSS COUNTRY CIRCUIT",   "Cross-Country Circuit",    True),
    ("CROSS COUNTRY",           "Cross-Country",            False),
    ("SCRAMBLE",                "Dirt Circuit",             True),
    ("TRAIL",                   "Dirt Point to Point",      False),
    ("CIRCUIT",                 "Road Circuit",             True),
    ("SPRINT",                  "Road Sprint",              False),
]
RACE_TYPE_DEFAULT = ("Street Race", False)

# =============================================================
# Logging
# =============================================================
log = logging.getLogger(__name__)


def load_api_client():
    """Load Anthropic client using API key from .env file."""
    load_dotenv(ENV_FILE)
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(f"ANTHROPIC_API_KEY not found in {ENV_FILE}")
    return Anthropic(api_key=api_key)


def derive_race_type(track_name):
    """
    Derive race type and lap_based flag from track name.
    Returns (race_type_string, is_lap_based).
    """
    upper = track_name.upper()
    for keyword, race_type, lap_based in RACE_TYPE_MAP:
        if keyword in upper:
            return race_type, lap_based
    return RACE_TYPE_DEFAULT


def time_to_seconds(time_str):
    """Convert mm:ss.mmm string to float seconds. Returns None if invalid."""
    if not time_str or time_str.strip() in ("", "--:--.---", "N/A"):
        return None
    try:
        parts = time_str.strip().split(":")
        minutes = int(parts[0])
        seconds = float(parts[1])
        return minutes * 60 + seconds
    except Exception:
        return None


def calculate_gap(my_race_time, their_race_time):
    """
    Calculate gap between opponent and player in seconds.
    Positive = they were ahead (finished before you).
    """
    my_secs     = time_to_seconds(my_race_time)
    their_secs  = time_to_seconds(their_race_time)
    if my_secs is None or their_secs is None:
        return None
    gap = my_secs - their_secs
    return f"+{gap:.3f}" if gap > 0 else f"{gap:.3f}"


def image_to_base64(image_path):
    """Read image file and return base64 encoded string."""
    with open(image_path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def extract_results(client, image_path, race_id, telemetry_summary):
    """
    Send scoreboard screenshot to Claude API and extract structured data.
    Combines with telemetry summary for complete race record.
    Returns (race_result_dict, opponents_list) or (None, None) on failure.
    """
    log.info(f"Sending {os.path.basename(image_path)} to Claude for extraction...")

    image_data = image_to_base64(image_path)

    prompt = f"""You are analyzing a Forza Horizon race results scoreboard screenshot.

My gamertag is "{MY_GAMERTAG}" - find my row and extract my results.
Also extract all opponents who finished AHEAD of me (lower position number than mine).

Return ONLY a JSON object with this exact structure, no other text:
{{
  "track_name": "exact text from yellow banner at top",
  "my_result": {{
    "position": 1,
    "car": "exact car name text",
    "class": "S1",
    "pi": 900,
    "best_lap": "00:46.741",
    "race_time": "02:27.931",
    "total_racers": 12
  }},
  "opponents_ahead": [
    {{
      "position": 2,
      "gamertag": "exact gamertag text, strip club tags like [TAG] if present",
      "car": "exact car name",
      "class": "S1",
      "pi": 900,
      "best_lap": "00:48.592",
      "race_time": "02:30.459"
    }}
  ]
}}

Rules:
- If best_lap is not shown or not applicable, use "--:--.---"
- If race_time is not shown, use "--:--.---"
- total_racers = count of all rows on the scoreboard
- opponents_ahead = only racers with a LOWER position number than mine
- Strip club tags (text in square brackets) from gamertags
- PI is the number shown next to the class badge (e.g. S1 900 → pi: 900)
- Return valid JSON only, no markdown, no explanation"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type":         "base64",
                                "media_type":   "image/png",
                                "data":         image_data
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
        )

        raw = response.content[0].text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)
        log.info(f"Claude extraction successful | Track: {data.get('track_name')} | "
                 f"Position: {data['my_result'].get('position')}")

        track           = data.get("track_name", "Unknown")
        race_type, lap_based = derive_race_type(track)
        my              = data["my_result"]

        race_result = {
            "race_id":      race_id,
            "date":         telemetry_summary.get("date", datetime.now().strftime("%Y-%m-%d")),
            "time":         telemetry_summary.get("time", datetime.now().strftime("%H:%M:%S")),
            "position":     my.get("position"),
            "car":          my.get("car"),
            "class":        telemetry_summary.get("car_class",  my.get("class")),
            "pi":           telemetry_summary.get("car_pi",     my.get("pi")),
            "race_type":    race_type,
            "track":        track,
            "total_racers": my.get("total_racers"),
            "best_lap":     telemetry_summary.get("best_lap")  if lap_based else "",
            "race_time":    my.get("race_time") or telemetry_summary.get("race_time"),
            "notes":        ""
        }

        my_position     = my.get("position", 99)
        my_race_time    = my.get("race_time") or telemetry_summary.get("race_time")
        opponents       = []

        for opp in data.get("opponents_ahead", []):
            gap = calculate_gap(my_race_time, opp.get("race_time"))
            opponents.append({
                "race_id":      race_id,
                "track":        track,
                "position":     opp.get("position"),
                "gamertag":     opp.get("gamertag"),
                "car":          opp.get("car"),
                "class":        opp.get("class"),
                "pi":           opp.get("pi"),
                "best_lap":     opp.get("best_lap") if lap_based else "",
                "race_time":    opp.get("race_time"),
                "gap_to_me":    gap
            })

        log.info(f"Extracted {len(opponents)} opponent(s) ahead of you")
        return race_result, opponents

    except json.JSONDecodeError as e:
        log.error(f"Failed to parse Claude response as JSON: {e}")
        log.error(f"Raw response: {raw[:200]}")
        return None, None
    except Exception as e:
        log.error(f"Claude API error: {e}")
        return None, None


class ResultsExtractor:
    """
    Monitors the captures folder for new PNG files.
    When found, extracts data and passes to callback.
    Successful extractions delete the screenshot automatically.
    Failed extractions move to processed/ for manual review.
    """

    def __init__(self, on_results_ready=None):
        self.on_results_ready   = on_results_ready
        self.client             = load_api_client()
        self.pending_telemetry  = {}
        os.makedirs(CAPTURES_FOLDER,  exist_ok=True)
        os.makedirs(PROCESSED_FOLDER, exist_ok=True)

    def set_telemetry(self, race_id, telemetry_summary):
        """Called by telemetry listener when a race ends."""
        self.pending_telemetry[race_id] = telemetry_summary
        log.info(f"Telemetry stored for race {race_id}")

    def start(self):
        """Start monitoring captures folder. Runs until interrupted."""
        log.info(f"Results Extractor watching: {CAPTURES_FOLDER}")
        try:
            while True:
                self._check_for_new_captures()
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            log.info("Results Extractor stopped.")

    def _check_for_new_captures(self):
        """Look for new PNG files in captures folder and process them."""
        try:
            files = [
                f for f in os.listdir(CAPTURES_FOLDER)
                if f.endswith(".png") and
                not f.startswith("_tmp_") and
                os.path.isfile(os.path.join(CAPTURES_FOLDER, f))
            ]
        except Exception as e:
            log.error(f"Error reading captures folder: {e}")
            return

        for filename in files:
            filepath = os.path.join(CAPTURES_FOLDER, filename)

            try:
                race_id = filename.replace("scoreboard_", "").replace(".png", "")
            except Exception:
                race_id = datetime.now().strftime("%Y-%m%d-%H%M%S")

            telemetry = self.pending_telemetry.pop(race_id, {})
            race_result, opponents = extract_results(
                self.client, filepath, race_id, telemetry
            )

            if race_result:
                if self.on_results_ready:
                    self.on_results_ready(race_result, opponents)

                # Delete screenshot - data is in Google Sheets, image not needed
                try:
                    os.remove(filepath)
                    log.info(f"Deleted screenshot: {filename}")
                except Exception as e:
                    log.warning(f"Could not delete {filename}: {e}")
            else:
                # Move failed extractions to processed for manual review
                processed_path = os.path.join(PROCESSED_FOLDER, filename)
                try:
                    os.rename(filepath, processed_path)
                    log.warning(f"Extraction failed - moved for review: {filename}")
                except Exception as e:
                    log.warning(f"Extraction failed and could not move {filename}: {e}")
