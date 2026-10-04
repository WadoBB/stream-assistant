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
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from flask import Flask, jsonify, request, send_file, Response
from config import (CONTROLLER_PORT, GAMING_PC_IP, CAPTURE_AGENT_PORT, LOGS_FOLDER,
                     OVERLAY_FOLDER, OVERLAY_HTML, OVERLAY_STATE_FILE, OVERLAY_SOUND_FILE,
                     CAR_CARD_HTML, CAR_CARD_STATE_FILE, CAR_IMAGES_FOLDER, CAR_CARD_DEFAULT_IMAGE,
                     CAR_CARD_SOUND_FILE, CAR_ORDINALS_FILE, MONITOR_HTML,
                     OBS_WS_HOST, OBS_WS_PORT, OBS_OVERLAY_SOURCE, OBS_CAR_CARD_SOURCE,
                     ENV_FILE)

SYNC_ORDINALS_SCRIPT = r"C:\StreamAssistant\ai-computer\sync_ordinals.py"
SYNC_CAR_NAMES_SCRIPT = r"C:\StreamAssistant\ai-computer\sync_car_names_from_sheet.py"
TEST_CAR_CARD_SCRIPT = r"C:\StreamAssistant\ai-computer\test_car_card.py"
EXPORT_UNMATCHED_SCRIPT = r"C:\StreamAssistant\ai-computer\export_unmatched_ordinals.py"
APPLY_ORDINAL_MATCHES_SCRIPT = r"C:\StreamAssistant\ai-computer\apply_ordinal_matches.py"

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
                    # Ordinary pings/errors were already logged, but a
                    # successful push never was - meaning "did the server
                    # actually see this car change and push it" was
                    # unanswerable after the fact. Logging every real push
                    # (not pings) turns "the card didn't fire" into a
                    # yes/no on which side of the pipe the failure is: if
                    # this line is missing for a car change that's in
                    # car_card_state.json, the generator itself stalled;
                    # if it's present, the break is client-side (stuck
                    # EventSource/watchdog not rendering it).
                    log.info(f"SSE push: {os.path.basename(state_file_path)} -> {data}")
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


# =============================================================
# Render acknowledgements + OBS auto-refresh
#
# Root cause of the "overlay stops updating until the Browser Source is
# manually refreshed" problem is a confirmed, still-open Chromium/CEF bug in
# the browser OBS 31-32 bundle (obs-studio #12796) - the page's JS and SSE
# connection keep running but OBS stops receiving repainted frames. Nothing
# in-page can fix that; a refresh always did. So:
#   1. Both overlay pages GET /overlay/ack for every new event they receive,
#      after the browser has actually produced a frame showing it (double
#      requestAnimationFrame - a page whose rendering is stalled may never
#      get there, which is the point). obs=1 marks the OBS copy, as opposed
#      to the /monitor copy in Brave.
#   2. _watch_state_files() notices every write to either state file -
#      regardless of which process wrote it (main.py's sheets_writer, this
#      controller's /test and /trigger routes, test_car_card.py) - and if no
#      obs=1 ack for that event's timestamp arrives within OBS_ACK_TIMEOUT_S,
#      presses that Browser Source's refresh button over obs-websocket. The
#      reloaded page's new SSE connection receives the current state on
#      connect and shows it (still well inside its 60s MAX_AGE_MS).
# Every missed ack is logged even while auto-refresh is unconfigured, so
# /logs?file=controller shows which side failed for any missed event: no
# "SSE push" line = server; push but no ack = page never processed/painted
# it; ack with shown=0 = page received it but rejected it (e.g. clock skew).
# =============================================================
OBS_ACK_TIMEOUT_S          = 3.0   # SSE loop polls mtime every 1s, so push lag alone can be ~1s
OBS_RECOVERY_CHECK_S       = 6.0   # after a refresh, how long to wait before checking it worked
OBS_REFRESH_MIN_INTERVAL_S = 3.0   # rapid repeated refreshes are themselves known to break CEF rendering
OBS_PLACEHOLDER_PASSWORD   = "CHANGE_ME"

OVERLAY_PAGES = {
    "overlay":  {"state_file": OVERLAY_STATE_FILE,  "obs_source": OBS_OVERLAY_SOURCE},
    "car_card": {"state_file": CAR_CARD_STATE_FILE, "obs_source": OBS_CAR_CARD_SOURCE},
}

_acks      = {}     # (page, event timestamp) -> {"shown": bool, "age": int|None, "at": float}
_acks_lock = threading.Lock()

_obs_client       = None
_obs_lock         = threading.Lock()
_obs_last_refresh = {}   # page -> time.time() of last refresh press
_obs_refresh_lock = threading.Lock()


def _obs_password():
    """
    Reads OBS_WS_PASSWORD from credentials\\.env (gitignored) rather than
    config.py, which is committed. Returns None while unset or still the
    placeholder, which keeps auto-refresh switched off.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE)
    except ImportError:
        pass
    pw = os.environ.get("OBS_WS_PASSWORD")
    if not pw or pw == OBS_PLACEHOLDER_PASSWORD:
        return None
    return pw


def _obs_disabled_reason():
    """Returns why auto-refresh can't run, or None if it's configured."""
    try:
        import obsws_python  # noqa: F401
    except ImportError:
        return "obsws-python not installed (pip install obsws-python)"
    if _obs_password() is None:
        return "OBS_WS_PASSWORD not set in credentials\\.env"
    return None


def _obs_call(fn):
    """
    Runs fn(client) against a lazily-created, reused obs-websocket client.
    Any failure drops the client so the next call reconnects from scratch -
    OBS restarting or the gaming PC sleeping shouldn't need a controller.py
    restart to recover. Serialized by a lock: the underlying websocket client
    is not safe to share across threads.
    """
    global _obs_client
    import obsws_python as obs
    # obsws-python logs its connection parameters, password included, at
    # DEBUG - keep it quiet regardless of what the root logger is set to.
    logging.getLogger("obsws_python").setLevel(logging.WARNING)
    with _obs_lock:
        try:
            if _obs_client is None:
                _obs_client = obs.ReqClient(host=OBS_WS_HOST, port=OBS_WS_PORT,
                                            password=_obs_password(), timeout=3)
            return fn(_obs_client)
        except Exception:
            try:
                if _obs_client is not None:
                    _obs_client.disconnect()
            except Exception:
                pass
            _obs_client = None
            raise


def _refresh_obs_source(page, reason):
    """
    Presses the Browser Source's own "Refresh cache of current page" button
    (property "refreshnocache") - exactly the manual fix, done remotely.
    Rate-limited per source. Note obs-websocket reports success even when
    this is a no-op because the source has no live browser ("Shutdown source
    when not visible" on and the source hidden) - keep that setting off.
    Returns (ok, message).
    """
    disabled = _obs_disabled_reason()
    if disabled:
        return False, f"auto-refresh disabled: {disabled}"

    with _obs_refresh_lock:
        now = time.time()
        if now - _obs_last_refresh.get(page, 0) < OBS_REFRESH_MIN_INTERVAL_S:
            return False, "skipped: refreshed under {:.0f}s ago".format(OBS_REFRESH_MIN_INTERVAL_S)
        _obs_last_refresh[page] = now

    source = OVERLAY_PAGES[page]["obs_source"]
    try:
        _obs_call(lambda c: c.press_input_properties_button(source, "refreshnocache"))
        log.warning(f"OBS auto-refresh: pressed refresh on '{source}' ({reason})")
        return True, f"refreshed '{source}'"
    except Exception as e:
        log.error(f"OBS auto-refresh failed for '{source}': {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {e}"


def _get_ack(page, ts):
    with _acks_lock:
        return _acks.get((page, ts))


def _check_ack(page, ts):
    """
    Runs on its own thread per event: waits for the OBS copy of the page to
    acknowledge ts, refreshes the source if it didn't, then checks whether
    the refresh actually got the event on screen.
    """
    time.sleep(OBS_ACK_TIMEOUT_S)
    ack = _get_ack(page, ts)
    if ack is not None:
        if not ack["shown"]:
            age = ack["age"]
            hint = (" - negative age means the gaming PC's clock is behind this one; "
                    "the page rejects events from the future") if age is not None and age < 0 else ""
            log.warning(f"Overlay ack: {page} {ts} received by OBS but NOT shown (age={age}ms){hint}")
        return

    log.warning(f"Overlay ack: no OBS ack for {page} {ts} within {OBS_ACK_TIMEOUT_S:.0f}s")
    ok, msg = _refresh_obs_source(page, f"no ack for {ts}")
    if not ok:
        log.info(f"Overlay ack: {page} {ts} not refreshed - {msg}")
        return

    time.sleep(OBS_RECOVERY_CHECK_S)
    if _get_ack(page, ts) is not None:
        log.info(f"Overlay ack: {page} {ts} recovered after refresh")
    else:
        log.warning(f"Overlay ack: {page} {ts} still unacknowledged {OBS_RECOVERY_CHECK_S:.0f}s after refresh")


def _watch_state_files():
    """
    Background thread: spots every write to either overlay state file by
    mtime (same approach as _sse_stream()) and starts an ack check for it.
    Watching the files rather than hooking each writer is deliberate - the
    record alert is written by main.py, a separate process, so this is the
    one place that sees every event no matter who wrote it.
    """
    last_mtimes = {}
    for page, cfg in OVERLAY_PAGES.items():
        path = cfg["state_file"]
        last_mtimes[page] = os.path.getmtime(path) if os.path.exists(path) else None

    while True:
        for page, cfg in OVERLAY_PAGES.items():
            path = cfg["state_file"]
            try:
                if not os.path.exists(path):
                    continue
                mtime = os.path.getmtime(path)
                if mtime == last_mtimes[page]:
                    continue
                with open(path) as f:
                    ts = json.load(f).get("timestamp")
                # Only advance past this write once it's been read cleanly -
                # a read racing os.replace() just retries on the next pass.
                last_mtimes[page] = mtime
                if ts:
                    threading.Thread(target=_check_ack, args=(page, ts), daemon=True).start()
            except Exception as e:
                log.debug(f"State watcher read failed for {path}: {e}")
        _prune_acks()
        time.sleep(0.25)


def _prune_acks():
    cutoff = time.time() - 300
    with _acks_lock:
        for key in [k for k, v in _acks.items() if v["at"] < cutoff]:
            del _acks[key]


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


@app.route("/monitor", methods=["GET"])
def monitor_page():
    """
    Serves the personal monitor dashboard (both overlays stacked via
    iframes) - not an OBS Browser Source, just a convenience view for a
    second screen. See config.py's MONITOR_HTML comment and
    gaming-pc/open_stream_monitor.bat.
    """
    return send_file(MONITOR_HTML)


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


@app.route("/car_card/sync_car_names", methods=["GET"])
def sync_car_names():
    """
    Reverse of /car_card/sync_ordinals: for every Cars-tab row that already
    has an Ordinal set, backfills car_ordinals.json's car_name for that
    ordinal if it's still null. Closes a gap where a car matched by the
    identity-match sync (or the AI-reasoning pass) ends up fully resolved
    on the Sheet but still shows car_name: null in the JSON, since neither
    of those paths writes car_name. Runs as a subprocess, same reasoning as
    /car_card/sync_ordinals. ?game=FH5|FH6 selects which spreadsheet
    (default FH6).
    """
    game = request.args.get("game", "FH6").upper()
    if game not in ("FH5", "FH6"):
        return jsonify({"status": "error", "message": f"Unknown game version: {game}"}), 400
    try:
        result = subprocess.run(
            [PYTHON_EXE, SYNC_CAR_NAMES_SCRIPT, game],
            cwd=r"C:\StreamAssistant\ai-computer",
            capture_output=True, text=True, timeout=60
        )
        try:
            return jsonify(json.loads(result.stdout.strip()))
        except (ValueError, AttributeError):
            return jsonify({
                "status": "error",
                "message": "sync_car_names_from_sheet.py did not return valid JSON",
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip()
            }), 500
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "sync_car_names_from_sheet.py timed out"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/car_card/export_unmatched", methods=["GET"])
def export_unmatched():
    """
    Exports everything sync_ordinals_from_seed()'s exact matching (learned
    car_name, or exact "{Year} {MFG} {Model}" string) couldn't place: Cars
    tab rows still missing Ordinal, and seed entries not yet used by any
    row. Deliberately read-only and unopinionated about matching itself -
    exact string matching was a deliberate choice to avoid a wrong car
    getting an ordinal, so closing the gap for real naming differences
    (abbreviations, reordered words) needs judgment this can't safely
    automate; this just hands over the raw data for that judgment call to
    be made elsewhere (by a human or by an AI reasoning over it) before
    /car_card/apply_ordinal_matches writes anything back.
    ?game=FH5|FH6 selects which spreadsheet (default FH6).
    """
    game = request.args.get("game", "FH6").upper()
    if game not in ("FH5", "FH6"):
        return jsonify({"status": "error", "message": f"Unknown game version: {game}"}), 400
    try:
        result = subprocess.run(
            [PYTHON_EXE, EXPORT_UNMATCHED_SCRIPT, game],
            cwd=r"C:\StreamAssistant\ai-computer",
            capture_output=True, text=True, timeout=60
        )
        try:
            return jsonify(json.loads(result.stdout.strip()))
        except (ValueError, AttributeError):
            return jsonify({
                "status": "error",
                "message": "export_unmatched_ordinals.py did not return valid JSON",
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip()
            }), 500
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "export_unmatched_ordinals.py timed out"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/car_card/apply_ordinal_matches", methods=["POST"])
def apply_ordinal_matches():
    """
    Writes back a set of ordinal<->Cars-tab-row matches that couldn't be
    made by exact string matching (see /car_card/export_unmatched) and were
    instead judged by a human or an AI reasoning over the raw data. Backfills
    both the Cars tab's Ordinal column AND car_ordinals.json's car_name for
    each match, the same two writes learn_car_ordinal() does when a car is
    actually raced - this is the same knowledge, just arriving via reasoning
    over names instead of a live race. POST body:
    {"game": "FH5"|"FH6", "matches": [{"row_number": N, "ordinal": "N",
     "car_name": "..."}]}
    Only fills car_name if not already set, and only writes Ordinal if the
    row's Ordinal is still blank at write time - never overwrites existing
    data, so a stale or duplicate match list can't clobber anything.
    """
    body = request.get_json(silent=True) or {}
    game = str(body.get("game", "FH6")).upper()
    if game not in ("FH5", "FH6"):
        return jsonify({"status": "error", "message": f"Unknown game version: {game}"}), 400
    matches = body.get("matches")
    if not isinstance(matches, list) or not matches:
        return jsonify({"status": "error", "message": "matches must be a non-empty list"}), 400

    tmp_path = os.path.join(LOGS_FOLDER, "_ordinal_matches.tmp.json")
    try:
        with open(tmp_path, "w") as f:
            json.dump(matches, f)
        result = subprocess.run(
            [PYTHON_EXE, APPLY_ORDINAL_MATCHES_SCRIPT, tmp_path, game],
            cwd=r"C:\StreamAssistant\ai-computer",
            capture_output=True, text=True, timeout=60
        )
        try:
            return jsonify(json.loads(result.stdout.strip()))
        except (ValueError, AttributeError):
            return jsonify({
                "status": "error",
                "message": "apply_ordinal_matches.py did not return valid JSON",
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip()
            }), 500
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "apply_ordinal_matches.py timed out"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


@app.route("/car_card/lookup", methods=["GET"])
def car_card_lookup():
    """
    Reads car_ordinals.json directly and reports exactly what a given
    ordinal resolves to right now - unlike /car_card/test, which always
    writes canned/overridable fake data and never touches the real lookup
    file. Exists to answer "what does ordinal N actually know about it"
    without needing to hand-inspect car_ordinals.json on this machine.
    ?ordinal=<N> is required. Does not call the Sheets API (no Cars-tab
    cross-reference) - same reasoning as /car_card/sync_ordinals running as
    a subprocess: keep controller.py itself free of that dependency.
    """
    ordinal = request.args.get("ordinal")
    if not ordinal:
        return jsonify({"status": "error", "message": "ordinal query param is required"}), 400

    if not os.path.exists(CAR_ORDINALS_FILE):
        return jsonify({"status": "error", "message": f"{CAR_ORDINALS_FILE} does not exist"}), 404

    try:
        with open(CAR_ORDINALS_FILE) as f:
            ordinals = json.load(f)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Could not read car_ordinals.json: {e}"}), 500

    entry = ordinals.get(str(ordinal))
    if entry is None:
        return jsonify({"status": "ok", "ordinal": ordinal, "found": False,
                         "message": "No entry for this ordinal in car_ordinals.json"})

    return jsonify({"status": "ok", "ordinal": ordinal, "found": True, "entry": entry})


@app.route("/car_card/test", methods=["GET"])
def car_card_test():
    """
    Manual test trigger for the Car Card.

    /car_card/test?ordinal=<N> (with no other override params) now pulls
    REAL data by running update_car_card() - the exact same function the
    live pipeline calls on a car change - as a subprocess (keeps
    controller.py itself free of the Google API dependency, same reasoning
    as /car_card/sync_ordinals). Correctly shows "not yet raced" / blank
    stats for a car that hasn't matched a Cars tab row, instead of always
    showing canned placeholder data regardless of the ordinal - added
    2026-09-12 after that mismatch caused confusion testing ordinals like
    2793/3670 that had never been raced. Optional ?class=...&game=FH5|FH6
    (default FH6) apply to this path only.

    Passing any of full_name/car_name/year/mfg/model/tuner/painter/races
    /wins/win_rate (with or without ordinal) instead uses the original
    fully-canned override behavior - useful for testing card layout/UI with
    arbitrary data unrelated to any real car:
    /car_card/test?full_name=...&car_name=...&year=...&mfg=...&model=...
      &tuner=...&painter=...&races=...&wins=...&win_rate=...&ordinal=...
    Bare /car_card/test (no params at all) also uses this canned path, for
    a quick connectivity/animation smoke test independent of real data.
    """
    override_keys = ("full_name", "car_name", "year", "mfg", "model",
                      "tuner", "painter", "races", "wins", "win_rate")
    has_override = any(request.args.get(k) is not None for k in override_keys)
    has_ordinal = request.args.get("ordinal") is not None

    if has_ordinal and not has_override:
        ordinal = request.args.get("ordinal")
        live_class = request.args.get("class", "")
        game = request.args.get("game", "FH6").upper()
        if game not in ("FH5", "FH6"):
            return jsonify({"status": "error", "message": f"Unknown game version: {game}"}), 400
        try:
            result = subprocess.run(
                [PYTHON_EXE, TEST_CAR_CARD_SCRIPT, ordinal, live_class, game],
                cwd=r"C:\StreamAssistant\ai-computer",
                capture_output=True, text=True, timeout=30
            )
            try:
                parsed = json.loads(result.stdout.strip())
            except (ValueError, AttributeError):
                return jsonify({
                    "status": "error",
                    "message": "test_car_card.py did not return valid JSON",
                    "stdout": result.stdout.strip(),
                    "stderr": result.stderr.strip()
                }), 500
            if parsed.get("status") != "ok":
                return jsonify(parsed), 500

            event = {}
            if os.path.exists(CAR_CARD_STATE_FILE):
                with open(CAR_CARD_STATE_FILE) as f:
                    event = json.load(f)
            log.info(f"Real-data car card test fired for ordinal {ordinal}: {event}")
            return jsonify({"status": "ok", "event": event, "source": "real"})
        except subprocess.TimeoutExpired:
            return jsonify({"status": "error", "message": "test_car_card.py timed out"}), 500
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

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
        "timestamp": datetime.now(timezone.utc).isoformat()
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


@app.route("/car_card/trigger", methods=["GET"])
def car_card_trigger():
    """
    Re-fires the Car Card overlay for whichever car is ACTUALLY currently
    selected - built for a viewer chat command / channel-point redemption
    via Streamerbot (see TODO.md's "Channel Command to Trigger Car Card"),
    not for testing. Unlike /car_card/test, this never accepts an ordinal
    or any override data; it just re-timestamps the existing
    car_card_state.json (already the resolved result of the last real car
    change) and lets _sse_stream() pick up the mtime change normally - no
    Sheets API call needed, since the data was already resolved when the
    car was actually selected. Rejects with 404 if no car has been
    selected yet this session (car_card_state.json doesn't exist).

    Deliberately no cooldown/rate-limit here - the user wants that
    controlled entirely from Streamerbot's own command cooldown setting,
    not duplicated/hardcoded in this code. This endpoint always fires
    immediately when called.
    """
    if not os.path.exists(CAR_CARD_STATE_FILE):
        return jsonify({"status": "error", "message": "No car selected yet this session"}), 404

    try:
        with open(CAR_CARD_STATE_FILE) as f:
            card = json.load(f)
        card["timestamp"] = datetime.now(timezone.utc).isoformat()
        tmp_path = CAR_CARD_STATE_FILE + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(card, f)
        os.replace(tmp_path, CAR_CARD_STATE_FILE)
        log.info(f"Car Card trigger fired (viewer command): {card}")
        return jsonify({"status": "ok", "event": card})
    except Exception as e:
        log.error(f"Failed to fire Car Card trigger: {e}")
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
        "timestamp":     datetime.now(timezone.utc).isoformat()
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


@app.route("/overlay/ack", methods=["GET"])
def overlay_ack():
    """
    Render acknowledgement from an overlay page - see the "Render
    acknowledgements + OBS auto-refresh" section above.
    ?page=overlay|car_card (allowlist) &ts=<event timestamp> &obs=0|1
    &shown=0|1 &age=<ms the page computed the event's age as>.
    Only obs=1 acks count toward the auto-refresh decision; /monitor's
    (obs=0) are logged for comparison but otherwise ignored.
    """
    page = request.args.get("page", "")
    ts = request.args.get("ts", "")
    if page not in OVERLAY_PAGES or not ts:
        return jsonify({"status": "error", "message": "page must be overlay|car_card and ts is required"}), 400

    is_obs = request.args.get("obs") == "1"
    shown = request.args.get("shown") == "1"
    try:
        age = int(float(request.args.get("age", "")))
    except ValueError:
        age = None

    log.info(f"Overlay ack: {page} {ts} obs={int(is_obs)} shown={int(shown)} age={age}ms")
    if is_obs:
        with _acks_lock:
            _acks[(page, ts)] = {"shown": shown, "age": age, "at": time.time()}
    return jsonify({"status": "ok"})


@app.route("/obs/status", methods=["GET"])
def obs_status():
    """
    Reports whether auto-refresh is configured and whether OBS on the gaming
    PC is actually reachable over obs-websocket (via GetVersion). Same narrow
    remote-diagnosis pattern as /logs - checkable from either machine.
    """
    disabled = _obs_disabled_reason()
    result = {
        "host": OBS_WS_HOST,
        "port": OBS_WS_PORT,
        "sources": {page: cfg["obs_source"] for page, cfg in OVERLAY_PAGES.items()},
        "configured": disabled is None,
    }
    if disabled:
        result.update(status="disabled", message=disabled)
        return jsonify(result)
    try:
        version = _obs_call(lambda c: c.send("GetVersion", raw=True))
        result.update(status="ok",
                      obs_version=version.get("obsVersion"),
                      obs_websocket_version=version.get("obsWebSocketVersion"))
        return jsonify(result)
    except Exception as e:
        result.update(status="error", message=f"{type(e).__name__}: {e}")
        return jsonify(result), 502


@app.route("/obs/refresh", methods=["GET"])
def obs_refresh():
    """
    Manually presses refresh on one overlay's Browser Source in OBS -
    ?page=overlay|car_card (allowlist, never a free-form source name). Goes
    through the same rate limit as auto-refresh. For verifying the
    obs-websocket path end to end without waiting for a missed event.
    """
    page = request.args.get("page", "")
    if page not in OVERLAY_PAGES:
        return jsonify({"status": "error", "message": "page must be overlay|car_card"}), 400
    ok, msg = _refresh_obs_source(page, "manual /obs/refresh")
    return jsonify({"status": "ok" if ok else "error", "message": msg}), (200 if ok else 503)


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
    obs_disabled = _obs_disabled_reason()
    log.info(f"OBS auto-refresh: {'OFF - ' + obs_disabled if obs_disabled else 'ON'} "
             f"(obs-websocket {OBS_WS_HOST}:{OBS_WS_PORT})")
    log.info("=" * 55)

    threading.Thread(target=_watch_state_files, daemon=True).start()

    # threaded=True is required now that /overlay/stream and /car_card/stream
    # hold a connection open indefinitely (Server-Sent Events) - without it,
    # Werkzeug's dev server handles one request at a time, and a single open
    # SSE connection would block /toggle, /status, everything else.
    app.run(host="0.0.0.0", port=CONTROLLER_PORT, debug=False, threaded=True)
