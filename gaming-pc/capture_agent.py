# =============================================================
# Stream Assistant - Module 2: Capture Agent
# Runs on the GAMING PC.
# Watches the screen for the Forza scoreboard and captures
# it when detected, saving to the shared network folder.
#
# Usage: python capture_agent.py
# (started automatically by toggle_stream_assistant.bat)
# =============================================================

import os
import time
import socket
import logging
import argparse
import numpy as np
from logging.handlers import RotatingFileHandler
from PIL import ImageGrab
import cv2

# =============================================================
# Configuration
# =============================================================
AI_COMPUTER_IP          = "192.168.137.230"
SHARED_FOLDER           = r"\\192.168.137.230\StreamCaptures"
CAPTURE_AGENT_PORT      = 9998
SCREEN_POLL_INTERVAL    = 0.5           # seconds between screen checks
MAX_SCOREBOARD_WAIT     = 60            # seconds before giving up
LOG_FILE                = r"C:\StreamAssistant\gaming-pc\logs\capture_agent.log"

# --- Game Version (set by --game argument at startup) ---
_parser = argparse.ArgumentParser(description="Stream Assistant - Capture Agent")
_parser.add_argument(
    "--game",
    choices=["FH5", "FH6"],
    default="FH5",
    help="Game version to track (default: FH5)"
)
GAME_VERSION = _parser.parse_args().game

# Scoreboard detection - HSV color range and screen region
# FH5: yellow track-name banner (top-left, variable width)
# FH6: lime-green column header row (spans full table width)
if GAME_VERSION == "FH6":
    BANNER_COLOR_LOW    = np.array([35,  200, 180])
    BANNER_COLOR_HIGH   = np.array([50,  255, 255])
    BANNER_REGION_X     = (0.15, 0.85)  # green header spans full table
    BANNER_REGION_Y     = (0.18, 0.30)  # header sits lower than FH5 banner
    BANNER_MIN_PIXELS   = 1500          # larger region = higher threshold
else:   # FH5
    BANNER_COLOR_LOW    = np.array([20,  150, 150])
    BANNER_COLOR_HIGH   = np.array([35,  255, 255])
    BANNER_REGION_X     = (0.05, 0.45)  # 5% to 45% of width
    BANNER_REGION_Y     = (0.10, 0.22)  # 10% to 22% of height
    BANNER_MIN_PIXELS   = 500           # minimum yellow pixels to confirm

# =============================================================
# Logging - rotating file, max 5MB, keep 3 backups
# =============================================================
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

_handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        _handler,
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def grab_screen():
    """Capture the full screen and return as numpy array."""
    screen = ImageGrab.grab()
    return np.array(screen)


def detect_scoreboard(screen_bgr):
    """
    Look for the yellow scoreboard banner in the expected screen region.
    Returns True if detected.
    """
    h, w = screen_bgr.shape[:2]

    x1 = int(w * BANNER_REGION_X[0])
    x2 = int(w * BANNER_REGION_X[1])
    y1 = int(h * BANNER_REGION_Y[0])
    y2 = int(h * BANNER_REGION_Y[1])

    region = screen_bgr[y1:y2, x1:x2]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, BANNER_COLOR_LOW, BANNER_COLOR_HIGH)
    yellow_pixels = cv2.countNonZero(mask)

    return yellow_pixels >= BANNER_MIN_PIXELS


def capture_scoreboard(race_id):
    """
    Take a full screenshot and save to the shared network folder.
    Writes to a temp file first, then renames atomically so the
    results extractor never sees a partially-written file.
    Returns the saved file path or None on failure.
    """
    try:
        screen = ImageGrab.grab()
        filename = f"scoreboard_{race_id}.png"
        filepath = os.path.join(SHARED_FOLDER, filename)
        temp_filepath = os.path.join(SHARED_FOLDER, f"_tmp_{filename}")
        screen.save(temp_filepath)
        os.rename(temp_filepath, filepath)
        log.info(f"Scoreboard captured: {filename}")
        return filepath
    except Exception as e:
        log.error(f"Failed to capture screenshot: {e}")
        return None


def wait_for_scoreboard(race_id):
    """
    Poll screen until scoreboard detected or timeout.
    Returns True if captured, False if timed out.
    """
    log.info(f"Watching for scoreboard (race {race_id})...")
    start = time.time()

    while time.time() - start < MAX_SCOREBOARD_WAIT:
        screen = grab_screen()
        screen_bgr = cv2.cvtColor(screen, cv2.COLOR_RGB2BGR)

        if detect_scoreboard(screen_bgr):
            log.info("Scoreboard detected!")
            time.sleep(0.2)     # Let screen fully render
            return capture_scoreboard(race_id) is not None

        time.sleep(SCREEN_POLL_INTERVAL)

    log.warning(f"Scoreboard not detected within {MAX_SCOREBOARD_WAIT}s - giving up")
    return False


class CaptureAgent:
    """
    Listens for race-end triggers from the AI computer via UDP,
    then watches the screen for the scoreboard and captures it.
    """

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", CAPTURE_AGENT_PORT))
        sock.settimeout(1.0)

        log.info(f"Capture Agent listening on port {CAPTURE_AGENT_PORT}")
        log.info(f"Game version : {GAME_VERSION}")
        log.info(f"Saving captures to: {SHARED_FOLDER}")
        log.info("Waiting for race end trigger... (Ctrl+C to stop)")

        try:
            while True:
                try:
                    data, addr = sock.recvfrom(256)
                    message = data.decode("utf-8").strip()

                    if message.startswith("RACE_END:"):
                        race_id = message.split(":")[1]
                        log.info(f"Race end trigger received | Race ID: {race_id}")
                        wait_for_scoreboard(race_id)

                except socket.timeout:
                    pass    # Normal - just keep listening

        except KeyboardInterrupt:
            log.info("Capture Agent stopped.")
        finally:
            sock.close()


# =============================================================
# Entry point
# =============================================================
if __name__ == "__main__":
    agent = CaptureAgent()
    agent.start()
