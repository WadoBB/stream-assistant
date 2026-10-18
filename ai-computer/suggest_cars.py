# =============================================================
# Stream Assistant - Car Suggester
# Runs on the AI COMPUTER, on demand (not part of the live pipeline).
#
# Ranks the Cars tab's cars for one (Class, Type) - e.g. A + Dirt - and
# writes suggest_state.json for the suggester overlay. Triggered by a Stream
# Deck button via controller.py's /suggest, which runs this as a subprocess
# (keeps controller.py itself free of the Google API dependency - same
# reasoning as sync_ordinals.py/test_car_card.py).
#
# Why buttons instead of detection: the original Car Suggester plan (see
# TODO.md) needed a new screen detector for the race lobby/car-select screen
# to learn the upcoming track and class. Pressing a button supplies the
# Class and Road/Dirt directly, so this only has to do the Sheets lookup.
# There is no track, so ranking is per car, not per (Track, Class).
#
# Usage: python suggest_cars.py <class> <Road|Dirt> [FH5|FH6] [ranked|unraced]
# Prints one JSON object to stdout for the caller to parse.
# =============================================================

import os
import sys
import json
import logging
from datetime import datetime, timezone

# Quiet by default - stdout is the JSON result; logging noise would corrupt it.
logging.basicConfig(level=logging.ERROR)

import config
from config import CARS_TAB, BEST_BY_TAB, OVERLAY_FOLDER, SUGGEST_STATE_FILE
from sheets_writer import SheetsWriter, _normalize_str, _normalize_type


def _int(s):
    try:
        return int(float(str(s).strip()))
    except (ValueError, TypeError):
        return 0


def _read_tab(writer, tab):
    """Returns (header -> column index, data rows) for a whole tab."""
    resp = writer.service.spreadsheets().values().get(
        spreadsheetId=writer.spreadsheet_id, range=f"{tab}!A:ZZ").execute()
    rows = resp.get("values", [])
    if not rows:
        return {}, []
    hmap = {h.strip(): i for i, h in enumerate(rows[0]) if h.strip()}
    return hmap, rows[1:]


def _cell(row, hmap, name):
    idx = hmap.get(name)
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx]).strip()


def _record_counts(writer, cls, car_type):
    """
    Counts how many Best by Track+Class records each car holds in this
    (Class, Type), keyed by normalized Car Name. That tab is rebuilt by the
    Apps Script and already excludes Meta cars, so these are "legit" records.
    """
    hmap, rows = _read_tab(writer, BEST_BY_TAB)
    counts = {}
    for row in rows:
        if _cell(row, hmap, "Class").upper() != cls:
            continue
        if _normalize_type(_cell(row, hmap, "Type")) != car_type:
            continue
        key = _normalize_str(_cell(row, hmap, "Car Name"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _display_name(row, hmap):
    """Garage-style name (Year MFG Model) - what you scroll to in-game - falling
    back to the abbreviated scoreboard Car Name when the catalog fields are blank."""
    parts = [_cell(row, hmap, h) for h in ("Year", "MFG", "Model")]
    if parts[1] and parts[2]:
        return " ".join(p for p in parts if p)
    return _cell(row, hmap, "Car Name")


def rank_cars(candidates):
    """
    Scores each candidate (dicts with races/wins/records) in place and
    returns them best-first.

    Raw win rate would put a 1-for-1 car above an 18-for-25 one, so each
    car's rate is blended with the group's average as if it had
    SUGGEST_PRIOR_RACES extra races at that average - a car needs a real
    sample before it can climb past proven ones. Each Best by Track+Class
    record held in this class then adds SUGGEST_RECORD_BONUS.
    """
    total_races = sum(c["races"] for c in candidates)
    total_wins = sum(c["wins"] for c in candidates)
    group_rate = total_wins / total_races if total_races else 0.5
    prior = config.SUGGEST_PRIOR_RACES

    for c in candidates:
        adjusted = (c["wins"] + prior * group_rate) / (c["races"] + prior)
        c["score"] = round(adjusted + config.SUGGEST_RECORD_BONUS * c["records"], 4)
    return sorted(candidates, key=lambda c: (c["score"], c["races"]), reverse=True)


def build_suggestions(cls, car_type, game, mode):
    writer = SheetsWriter(game_version=game)
    hmap, rows = _read_tab(writer, CARS_TAB)
    missing = [h for h in ("Car Name", "Class", "Type") if h not in hmap]
    if missing:
        raise RuntimeError(f"Cars tab missing column(s): {missing}")

    records = _record_counts(writer, cls, car_type) if mode == "ranked" else {}
    # The Apps Script stamps a per-game ownership column (header "FH6") on
    # auto-added rows - when it exists, unraced mode only lists cars you own.
    owned_col = game if game in hmap else None
    excluded = {f.upper() for f in config.SUGGEST_EXCLUDE_FAV}

    candidates = []
    for row in rows:
        car_name = _cell(row, hmap, "Car Name")
        if not car_name:
            continue
        if _cell(row, hmap, "Class").upper() != cls:
            continue
        if _normalize_type(_cell(row, hmap, "Type")) != car_type:
            continue
        if _cell(row, hmap, "Fav").upper() in excluded:
            continue

        races = _int(_cell(row, hmap, "Races"))
        wins = _int(_cell(row, hmap, "Wins"))
        if mode == "ranked" and races == 0:
            continue
        if mode == "unraced":
            if races > 0:
                continue
            if owned_col and not _cell(row, hmap, owned_col):
                continue

        candidates.append({
            "name":     _display_name(row, hmap),
            "car_name": car_name,
            "tuner":    _cell(row, hmap, "Tuner"),
            "tune":     _cell(row, hmap, "Tune"),
            "fav":      _cell(row, hmap, "Fav").upper(),
            "races":    races,
            "wins":     wins,
            "win_rate": round(wins / races, 4) if races else None,
            "records":  records.get(_normalize_str(car_name), 0),
        })

    if mode == "ranked":
        ordered = rank_cars(candidates)
    else:
        ordered = sorted(candidates, key=lambda c: c["name"].lower())

    count = config.SUGGEST_COUNT
    return {
        "class":        cls,
        "type":         car_type,
        "game":         game,
        "mode":         mode,
        "total":        len(ordered),
        "cars":         ordered[:count],
        "show_seconds": config.SUGGEST_SHOW_SECONDS,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
    }


def write_state(event):
    """Writes suggest_state.json (temp-then-rename, same as the other overlays)."""
    os.makedirs(OVERLAY_FOLDER, exist_ok=True)
    tmp_path = SUGGEST_STATE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(event, f)
    os.replace(tmp_path, SUGGEST_STATE_FILE)


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"status": "error", "message": "usage: suggest_cars.py <class> <Road|Dirt> [FH5|FH6] [ranked|unraced]"}))
        sys.exit(1)

    cls = sys.argv[1].strip().upper()
    car_type = _normalize_type(sys.argv[2])
    game = sys.argv[3].upper() if len(sys.argv) > 3 and sys.argv[3] else "FH6"
    mode = sys.argv[4].lower() if len(sys.argv) > 4 and sys.argv[4] else "ranked"

    if car_type not in ("Road", "Dirt"):
        print(json.dumps({"status": "error", "message": f"type must be Road or Dirt, got {sys.argv[2]!r}"}))
        sys.exit(1)
    if game not in ("FH5", "FH6"):
        print(json.dumps({"status": "error", "message": f"Unknown game version: {game}"}))
        sys.exit(1)
    if mode not in ("ranked", "unraced"):
        print(json.dumps({"status": "error", "message": f"mode must be ranked or unraced, got {mode!r}"}))
        sys.exit(1)

    try:
        event = build_suggestions(cls, car_type, game, mode)
        write_state(event)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"}))
        sys.exit(1)
    print(json.dumps({"status": "ok", "event": event}))


if __name__ == "__main__":
    main()
