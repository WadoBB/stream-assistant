# =============================================================
# Stream Assistant - Main Coordinator
# Runs on the AI COMPUTER.
# Starts all modules and wires them together.
#
# Usage: python main.py
# =============================================================

import os
import socket
import logging
import argparse
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from telemetry_listener import TelemetryListener
from results_extractor import ResultsExtractor
from sheets_writer import SheetsWriter
from config import GAMING_PC_IP, CAPTURE_AGENT_PORT, LOGS_FOLDER

# =============================================================
# Logging - rotating file, max 5MB, keep 3 backups
# =============================================================
os.makedirs(LOGS_FOLDER, exist_ok=True)

log_file = os.path.join(LOGS_FOLDER, "stream_assistant.log")
handler  = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(handler)
root_logger.addHandler(logging.StreamHandler())

log = logging.getLogger(__name__)


def generate_race_id():
    """Generate a unique race ID based on date and time."""
    return datetime.now().strftime("%Y-%m%d-%H%M%S")


def send_capture_trigger(race_id):
    """
    Send UDP trigger to the capture agent on the gaming PC.
    Tells it to start watching for the scoreboard.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        message = f"RACE_END:{race_id}".encode("utf-8")
        sock.sendto(message, (GAMING_PC_IP, CAPTURE_AGENT_PORT))
        sock.close()
        log.info(f"Capture trigger sent to gaming PC | Race ID: {race_id}")
    except Exception as e:
        log.error(f"Failed to send capture trigger: {e}")


class StreamAssistant:
    """
    Main coordinator. Wires together:
    - TelemetryListener  (Module 1) → detects race end
    - CaptureAgent       (Module 2) → runs on gaming PC, triggered via UDP
    - ResultsExtractor   (Module 3) → reads screenshot, calls Claude API
    - SheetsWriter       (Module 4) → writes to Google Sheets
    """

    def __init__(self, game_version="FH5"):
        self.game_version   = game_version
        self.extractor      = ResultsExtractor(game_version=game_version, on_results_ready=self.on_results_ready)
        self.writer         = SheetsWriter(game_version=game_version)
        self.listener       = TelemetryListener(game_version=game_version, on_race_end=self.on_race_end)

    def on_race_end(self, telemetry_summary):
        """
        Called by TelemetryListener when a race completes.
        1. Generate race ID
        2. Store telemetry for later merge with scoreboard data
        3. Trigger capture agent on gaming PC
        """
        race_id = generate_race_id()
        telemetry_summary["race_id"] = race_id

        log.info(f"Race end received | Race ID: {race_id}")
        log.info(f"  Position : {telemetry_summary.get('finish_position')}")
        log.info(f"  Best Lap : {telemetry_summary.get('best_lap')}")
        log.info(f"  Race Time: {telemetry_summary.get('race_time')}")

        self.extractor.set_telemetry(race_id, telemetry_summary)
        send_capture_trigger(race_id)

    def on_results_ready(self, race_result, opponents):
        """
        Called by ResultsExtractor when Claude has extracted the data.
        Write everything to Google Sheets.
        """
        log.info(f"Results ready for race {race_result.get('race_id')} - writing to sheets...")
        self.writer.write_race(race_result, opponents)

    def start(self):
        """Start all components."""
        log.info("=" * 55)
        log.info("Stream Assistant Starting")
        log.info(f"Game      : {self.game_version}")
        log.info(f"Gaming PC : {GAMING_PC_IP}")
        log.info(f"Log file  : {log_file}")
        log.info("=" * 55)

        extractor_thread = threading.Thread(
            target=self.extractor.start,
            daemon=True,
            name="ResultsExtractor"
        )
        extractor_thread.start()
        log.info("Results Extractor started in background thread")

        log.info("Starting Telemetry Listener - waiting for races...")
        self.listener.start()


# =============================================================
# Entry point
# =============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stream Assistant")
    parser.add_argument(
        "--game",
        choices=["FH5", "FH6"],
        default="FH5",
        help="Game version to track (default: FH5)"
    )
    args = parser.parse_args()

    assistant = StreamAssistant(game_version=args.game)
    assistant.start()
