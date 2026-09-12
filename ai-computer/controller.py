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
import json
import logging
import subprocess
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from flask import Flask, jsonify, request, send_file
from config import (CONTROLLER_PORT, GAMING_PC_IP, CAPTURE_AGENT_PORT, LOGS_FOLDER,
                     OVERLAY_FOLDER, OVERLAY_HTML, OVERLAY_STATE_FILE, OVERLAY_SOUND_FILE)

# Log files this instance will hand back over /logs - an allowlist, not a
# free-form path, so this can never be used to read arbitrary files.
ALLOWED_LOGS = {
    "controller":       "controller.log",
    "stream_assistant": "stream_assistant.log",
    "telemetry":        "telemetry.log",
}

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

# Flask's dev server logs every request at INFO via the 'werkzeug' logger -
# harmless for occasional /toggle calls, but the overlay page now polls
# /overlay/state every 1.5s, which floods the console/log with routine 200s.
# Real problems still surface: Werkzeug logs its own errors at WARNING+.
logging.getLogger('werkzeug').setLevel(logging.WARNING)

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


@app.route("/overlay", methods=["GET"])
def overlay_page():
    """
    Serves the stream overlay page. Point an OBS Browser Source at
    http://<AI_COMPUTER_IP>:<CONTROLLER_PORT>/overlay - the page polls
    /overlay/state itself, so nothing else needs to be configured in OBS.
    Runs on controller.py (always-on) rather than main.py (toggled per
    session) so the Browser Source doesn't go blank between sessions.
    """
    return send_file(OVERLAY_HTML)


@app.route("/overlay/cheer.mp3", methods=["GET"])
def overlay_sound():
    """Serves the sound clip index.html plays when a new record fires."""
    if not os.path.exists(OVERLAY_SOUND_FILE):
        return jsonify({"status": "error", "message": "No sound file configured"}), 404
    return send_file(OVERLAY_SOUND_FILE)


@app.route("/overlay/state", methods=["GET"])
def overlay_state():
    """
    Returns the current overlay event as JSON, written by sheets_writer.py's
    check_for_new_record() whenever a race beats the cached Best by
    Track+Class time. Returns {} if no event has been recorded yet or the
    file can't be read - the overlay page treats that as "nothing to show".
    """
    if not os.path.exists(OVERLAY_STATE_FILE):
        return jsonify({})
    try:
        with open(OVERLAY_STATE_FILE) as f:
            return jsonify(json.load(f))
    except Exception as e:
        log.warning(f"Could not read overlay state file: {e}")
        return jsonify({})


@app.route("/overlay/test", methods=["GET"])
def overlay_test():
    """
    Manual test trigger for the overlay - writes a fake event straight to
    overlay/state.json, the same file sheets_writer.py writes for a real
    record. Exists so the overlay can be verified end-to-end (page load,
    poll, animation) from a browser or a remote HTTP call without needing to
    hand-craft the JSON file on this machine, or race for real. Every call
    gets a fresh race_id so the overlay page always treats it as new.

    Optional query params override the canned defaults:
    /overlay/test?car=...&track=...&class=...&time=...&previous_time=...
    """
    event = {
        "race_id":       f"test-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "car":           request.args.get("car", "2019 Chevrolet Chevelle SS"),
        "track":         request.args.get("track", "Goliath"),
        "class":         request.args.get("class", "S1"),
        "time":          request.args.get("time", "2:14.902"),
        "previous_time": request.args.get("previous_time", "2:16.310"),
        "timestamp":     datetime.now().isoformat()
    }
    try:
        os.makedirs(OVERLAY_FOLDER, exist_ok=True)
        tmp_path = OVERLAY_STATE_FILE + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(event, f)
        os.replace(tmp_path, OVERLAY_STATE_FILE)
        log.info(f"Test overlay event written: {event}")
        return jsonify({"status": "ok", "event": event})
    except Exception as e:
        log.error(f"Failed to write test overlay event: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/logs", methods=["GET"])
def get_logs():
    """
    Returns the last N lines of one of this machine's log files, so it can
    be read remotely instead of relayed by hand. ?file= must be one of
    ALLOWED_LOGS' keys (default 'controller'); ?lines= defaults to 100 and
    is capped at 2000 to keep the response reasonable.
    """
    file_key = request.args.get("file", "controller")
    if file_key not in ALLOWED_LOGS:
        return jsonify({
            "status": "error",
            "message": f"Unknown file '{file_key}'. Choose from: {', '.join(ALLOWED_LOGS)}"
        }), 400

    try:
        n_lines = min(int(request.args.get("lines", 100)), 2000)
    except ValueError:
        n_lines = 100

    path = os.path.join(LOGS_FOLDER, ALLOWED_LOGS[file_key])
    if not os.path.exists(path):
        return jsonify({"status": "error", "message": f"{path} does not exist yet"}), 404

    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
        return jsonify({
            "status": "ok",
            "file": ALLOWED_LOGS[file_key],
            "lines": [l.rstrip("\n") for l in lines[-n_lines:]]
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


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
