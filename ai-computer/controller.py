# =============================================================
# Stream Assistant - Pipeline Controller
# Runs on the AI COMPUTER as a background service.
# Auto-starts at Windows login via start_controller_on_boot.bat
#
# Listens for HTTP requests from the gaming PC Stream Deck
# and starts/stops main.py accordingly.
#
# Usage: python controller.py
# =============================================================

import os
import logging
import subprocess
import threading
from logging.handlers import RotatingFileHandler
from flask import Flask, jsonify, request
from config import CONTROLLER_PORT, GAMING_PC_IP, CAPTURE_AGENT_PORT, LOGS_FOLDER

MAIN_SCRIPT = r"C:\StreamAssistant\ai-computer\main.py"
PYTHON_EXE = r"C:\Users\benny\AppData\Local\Programs\Python\Python313\python.exe"

# =============================================================
# Logging - rotating file, max 5MB, keep 3 backups
# =============================================================
os.makedirs(LOGS_FOLDER, exist_ok=True)

_handler = RotatingFileHandler(
    os.path.join(LOGS_FOLDER, "controller.log"),
    maxBytes=5*1024*1024,
    backupCount=3
)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        _handler,
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# =============================================================
# Flask app
# =============================================================
app = Flask(__name__)
pipeline_process = None
pipeline_lock    = threading.Lock()


def is_running():
    """Check if the pipeline process is currently running."""
    global pipeline_process
    return pipeline_process is not None and pipeline_process.poll() is None


@app.route("/toggle", methods=["GET"])
def toggle():
    """
    Toggle the pipeline on or off.
    Pass ?game=FH5 or ?game=FH6 to select the game version (default: FH5).
    """
    global pipeline_process

    game = request.args.get("game", "FH5").upper()
    if game not in ("FH5", "FH6"):
        return jsonify({"status": "error", "message": f"Unknown game version: {game}"}), 400

    with pipeline_lock:
        if is_running():
            log.info("Stopping pipeline...")
            try:
                pipeline_process.terminate()
                pipeline_process.wait(timeout=5)
            except Exception as e:
                log.error(f"Error stopping pipeline: {e}")
                pipeline_process.kill()

            pipeline_process = None
            log.info("Pipeline stopped")
            return jsonify({"status": "stopped", "message": "Stream Assistant stopped"})

        else:
            log.info(f"Starting pipeline (game: {game})...")
            try:
                pipeline_process = subprocess.Popen(
                    [PYTHON_EXE, MAIN_SCRIPT, "--game", game],
                    cwd=r"C:\StreamAssistant\ai-computer",
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
                log.info(f"Pipeline started (PID: {pipeline_process.pid}, game: {game})")
                return jsonify({
                    "status":   "running",
                    "message":  f"Stream Assistant started ({game})",
                    "pid":      pipeline_process.pid,
                    "game":     game
                })
            except Exception as e:
                log.error(f"Failed to start pipeline: {e}")
                return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/status", methods=["GET"])
def status():
    """Return current pipeline status."""
    return jsonify({
        "status":   "running" if is_running() else "stopped",
        "pid":      pipeline_process.pid if is_running() else None
    })


@app.route("/health", methods=["GET"])
def health():
    """Health check - confirms controller is reachable."""
    return jsonify({"status": "ok"})


# =============================================================
# Entry point
# =============================================================
if __name__ == "__main__":
    log.info("=" * 55)
    log.info("Stream Assistant Controller Starting")
    log.info(f"Listening on http://0.0.0.0:{CONTROLLER_PORT}")
    log.info("Waiting for Stream Deck toggle requests...")
    log.info("=" * 55)

    app.run(host="0.0.0.0", port=CONTROLLER_PORT, debug=False)
