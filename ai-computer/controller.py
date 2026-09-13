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
import time
import logging
import subprocess
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from flask import Flask, jsonify, request, send_file, Response
from config import (CONTROLLER_PORT, GAMING_PC_IP, CAPTURE_AGENT_PORT, LOGS_FOLDER,
                     OVERLAY_FOLDER, OVERLAY_HTML, OVERLAY_STATE_FILE, OVERLAY_SOUND_FILE,
                     CAR_CARD_HTML, CAR_CARD_STATE_FILE, CAR_IMAGES_FOLDER, CAR_CARD_DEFAULT_IMAGE,
                     CAR_CARD_SOUND_FILE)

SYNC_ORDINALS_SCRIPT = r"C:\StreamAssistant\ai-computer\sync_ordinals.py"

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
# harmless for occasional /toggle calls, but floods the console/log if left
# on for anything higher-frequency. Real problems still surface: Werkzeug
# logs its own errors at WARNING+.
logging.getLogger('werkzeug').setLevel(logging.WARNING)

# =============================================================
# Flask app
# =============================================================
app = Flask(__name__)
pipeline_process = None
pipeline_lock    = threading.Lock()


def _sse_stream(state_file_path):
    """
    Server-Sent Events generator: watches state_file_path for changes (by
    modification time, checked every second in a plain server-side loop) and
    pushes its contents the moment it changes.

    This replaced client-side polling (a page repeatedly fetching /overlay
    or /car_card state on a setTimeout loop) after both overlays proved
    unreliable during a long OBS session - working right after the Browser
    Source was freshly added, then silently going quiet, recoverable only by
    removing and re-adding the source. The polling loop's own code couldn't
    explain that (its reschedule happens unconditionally, outside any error
    path), which points at the browser/OBS throttling or stalling the JS
    timer for a source it considers inactive/backgrounded - a known category
    of behavior for setTimeout/setInterval loops, and not something fixable
    from inside the loop itself. Moving the "wait for new data" responsibility
    server-side (a plain Python loop here, never throttled) and letting the
    client's EventSource - which browsers handle far more robustly, including
    automatic reconnection if this connection drops, e.g. across a
    controller.py restart - just listen, sidesteps the whole problem rather
    than patching around it again.
    """
    last_mtime = None
    while True:
        try:
            if os.path.exists(state_file_path):
                mtime = os.path.getmtime(state_file_path)
                if mtime != last_mtime:
                    last_mtime = mtime
                    with open(state_file_path) as f:
                        data = f.read()
                    yield f"data: {data}\n\n"
                else:
                    # A NAMED event, not a bare SSE comment - visible to the
                    # client via addEventListener('ping', ...), not just
                    # invisible keep-alive noise. This exists so the page can
                    # tell "connection is fine, just quiet" apart from
                    # "connection silently died" and self-heal (see
                    # index.html/car_card.html's watchdog) instead of only
                    # recovering when a human manually refreshes the Browser
                    # Source - which is what happened in practice: the
                    # server-side push was proven correct even while this was
                    # a plain invisible comment, but a long-lived connection
                    # could still end up in a stuck state the browser's own
                    # EventSource reconnection didn't reliably catch.
                    yield "event: ping\ndata: {}\n\n"
            else:
                yield "event: ping\ndata: {}\n\n"
        except GeneratorExit:
            raise
        except Exception as e:
            log.error(f"SSE stream error for {state_file_path}: {e}")
        time.sleep(1)


def _find_orphaned_pipeline_pid():
    """
    Asks Windows directly whether a main.py process is running, independent
    of whether THIS controller.py process remembers starting it. Needed
    because pipeline_process is a plain in-memory variable - it resets to
    None every time controller.py itself restarts, even though a
    previously-started main.py keeps running untouched. Without this, a
    controller.py restart orphans the pipeline: is_running() reports False
    forever after, so /toggle keeps trying to start a second main.py
    instead of ever stopping (or recognizing) the first, which then fails to
    bind port 9999 - see TODO.md's "Controller Restart Orphans the Pipeline"
    entry for the incident that surfaced this. Returns a PID or None.
    """
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*main.py*' } | "
             "Select-Object -First 1 -ExpandProperty ProcessId)"],
            capture_output=True, text=True, timeout=10
        )
        pid_str = result.stdout.strip()
        return int(pid_str) if pid_str.isdigit() else None
    except Exception as e:
        log.error(f"Failed to check OS for an orphaned pipeline process: {e}")
        return None


def is_running():
    """
    Checks whether the pipeline is actually running - first the fast path
    (this controller.py process's own handle to it), then falling back to
    asking the OS directly in case a previous controller.py instance started
    it and this one never knew. See _find_orphaned_pipeline_pid()'s doc
    comment for why the fallback exists.
    """
    global pipeline_process
    if pipeline_process is not None and pipeline_process.poll() is None:
        return True
    return _find_orphaned_pipeline_pid() is not None


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
                if pipeline_process is not None and pipeline_process.poll() is None:
                    pipeline_process.terminate()
                    pipeline_process.wait(timeout=5)
                else:
                    # No in-memory handle (this controller.py didn't start
                    # it, or restarted since) - find and kill it via the OS.
                    orphan_pid = _find_orphaned_pipeline_pid()
                    if orphan_pid:
                        log.info(f"Stopping orphaned pipeline process (PID: {orphan_pid})")
                        subprocess.run(["taskkill", "/f", "/pid", str(orphan_pid)],
                                        capture_output=True, timeout=10)
            except Exception as e:
                log.error(f"Error stopping pipeline: {e}")
                if pipeline_process is not None:
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
    """
    Return current pipeline status. pid is this controller.py process's own
    handle's PID when available; if the pipeline is running but was found
    via the OS fallback (orphaned from a previous controller.py instance -
    see is_running()), pipeline_process is None here, so pid comes back
    null even though the pipeline genuinely is running.
    """
    running = is_running()
    return jsonify({
        "status": "running" if running else "stopped",
        "pid":    pipeline_process.pid if (running and pipeline_process is not None) else None
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


@app.route("/overlay/stream", methods=["GET"])
def overlay_stream():
    """
    Server-Sent Events version of /overlay/state - see _sse_stream()'s doc
    comment for why this replaced client-side polling. index.html uses this;
    /overlay/state is kept for manual/curl verification (e.g. after firing
    /overlay/test) and as a one-shot check independent of the live stream.
    """
    return Response(_sse_stream(OVERLAY_STATE_FILE), mimetype="text/event-stream")


@app.route("/car_card", methods=["GET"])
def car_card_page():
    """
    Serves the Car Card overlay page. Separate Browser Source from /overlay -
    this one is triggered by car_ordinal changing in telemetry (any car
    selection, not tied to a race), not by a race result.
    """
    return send_file(CAR_CARD_HTML)


@app.route("/car_card/state", methods=["GET"])
def car_card_state():
    """
    Returns the current Car Card event as JSON, written by sheets_writer.py's
    update_car_card() whenever telemetry detects a car change. Returns {} if
    nothing has been recorded yet or the file can't be read.
    """
    if not os.path.exists(CAR_CARD_STATE_FILE):
        return jsonify({})
    try:
        with open(CAR_CARD_STATE_FILE) as f:
            return jsonify(json.load(f))
    except Exception as e:
        log.warning(f"Could not read car card state file: {e}")
        return jsonify({})


@app.route("/car_card/stream", methods=["GET"])
def car_card_stream():
    """
    Server-Sent Events version of /car_card/state - see _sse_stream()'s doc
    comment for why this replaced client-side polling. car_card.html uses
    this; /car_card/state is kept for manual/curl verification.
    """
    return Response(_sse_stream(CAR_CARD_STATE_FILE), mimetype="text/event-stream")


@app.route("/car_card/image/<ordinal>", methods=["GET"])
def car_card_image(ordinal):
    """
    Serves the car image for a given ordinal (ai-computer/overlay/car_images/
    <ordinal>.png), falling back to CAR_CARD_DEFAULT_IMAGE if that ordinal
    has no image yet. <ordinal> is used only to build a filename within
    CAR_IMAGES_FOLDER, never as an arbitrary path - basename-only, and it
    must already exist inside that folder or the fallback is served instead.
    """
    safe_name = os.path.basename(str(ordinal)) + ".png"
    image_path = os.path.join(CAR_IMAGES_FOLDER, safe_name)
    if os.path.exists(image_path):
        return send_file(image_path)
    if os.path.exists(CAR_CARD_DEFAULT_IMAGE):
        return send_file(CAR_CARD_DEFAULT_IMAGE)
    return jsonify({"status": "error", "message": "No image and no default configured"}), 404


@app.route("/car_card/rev.mp3", methods=["GET"])
def car_card_sound():
    """Serves the engine-rev sound clip car_card.html plays on a car change."""
    if not os.path.exists(CAR_CARD_SOUND_FILE):
        return jsonify({"status": "error", "message": "No sound file configured"}), 404
    return send_file(CAR_CARD_SOUND_FILE)


@app.route("/car_card/sync_ordinals", methods=["GET"])
def sync_ordinals():
    """
    Bulk-backfills the Cars tab's Ordinal column from everything already
    learned in car_ordinals.json, instead of waiting for each car to be
    raced again individually to trigger the per-race lazy backfill. Runs as
    a subprocess (not imported directly into this process) so controller.py
    itself stays free of the Google API dependency - same reasoning as why
    /toggle launches main.py as a subprocess rather than importing it.
    ?game=FH5|FH6 selects which spreadsheet (default FH6).
    """
    game = request.args.get("game", "FH6").upper()
    if game not in ("FH5", "FH6"):
        return jsonify({"status": "error", "message": f"Unknown game version: {game}"}), 400
    try:
        result = subprocess.run(
            [PYTHON_EXE, SYNC_ORDINALS_SCRIPT, game],
            cwd=r"C:\StreamAssistant\ai-computer",
            capture_output=True, text=True, timeout=60
        )
        try:
            return jsonify(json.loads(result.stdout.strip()))
        except (ValueError, AttributeError):
            return jsonify({
                "status": "error",
                "message": "sync_ordinals.py did not return valid JSON",
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip()
            }), 500
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "sync_ordinals.py timed out"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/car_card/test", methods=["GET"])
def car_card_test():
    """
    Manual test trigger for the Car Card - writes a fake event straight to
    car_card_state.json. Optional query params override the canned defaults:
    /car_card/test?full_name=...&car_name=...&year=...&mfg=...&model=...
      &tuner=...&painter=...&races=...&wins=...&win_rate=...&ordinal=...
    """
    event = {
        "ordinal":   request.args.get("ordinal", "999999"),
        "full_name": request.args.get("full_name", "2019 Chevrolet Chevelle SS"),
        "car_name":  request.args.get("car_name", "Chevelle SS"),
        "known":     True,
        "year":      request.args.get("year", "2019"),
        "mfg":       request.args.get("mfg", "Chevrolet"),
        "model":     request.args.get("model", "Chevelle SS"),
        "class":     request.args.get("class", "S1"),
        "tuner":     request.args.get("tuner", "Benny"),
        "painter":   request.args.get("painter", "Benny"),
        "races":     request.args.get("races", "7"),
        "wins":      request.args.get("wins", "2"),
        "win_rate":  request.args.get("win_rate", "0.2857"),
        "timestamp": datetime.now().isoformat()
    }
    try:
        os.makedirs(OVERLAY_FOLDER, exist_ok=True)
        tmp_path = CAR_CARD_STATE_FILE + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(event, f)
        os.replace(tmp_path, CAR_CARD_STATE_FILE)
        log.info(f"Test car card event written: {event}")
        return jsonify({"status": "ok", "event": event})
    except Exception as e:
        log.error(f"Failed to write test car card event: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


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

    # threaded=True is required now that /overlay/stream and /car_card/stream
    # hold a connection open indefinitely (Server-Sent Events) - without it,
    # Werkzeug's dev server handles one request at a time, and a single open
    # SSE connection would block /toggle, /status, everything else.
    app.run(host="0.0.0.0", port=CONTROLLER_PORT, debug=False, threaded=True)
