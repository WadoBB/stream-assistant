# =============================================================
# Stream Assistant - Module 4: Sheets Writer
# Runs on the AI COMPUTER.
# Writes race results to the Results tab and opponents who
# finished ahead to the Opponents tab in Google Sheets.
# =============================================================

import os
import re
import json
import logging
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from collections import defaultdict
from config import (SHEETS_CREDENTIALS, FH5_SPREADSHEET_ID, FH6_SPREADSHEET_ID,
                     RESULTS_TAB, OPPONENTS_TAB, CARS_TAB, BEST_BY_TAB,
                     OVERLAY_FOLDER, OVERLAY_STATE_FILE,
                     CAR_ORDINALS_FILE, CAR_CARD_STATE_FILE)
from results_extractor import NON_COMPETITIVE_NOTES, time_to_seconds


def _normalize_str(s):
    """
    Mirrors the Apps Script normalize_() function.
    Collapses smart quotes, em-dashes, non-breaking spaces to ASCII equivalents,
    then lowercases and strips — so car names typed in Sheets match names
    coming from the game even when Google autocorrected the punctuation.
    """
    s = str(s or '')
    s = re.sub(r"[''‚‛′]",  "'", s)   # smart single quotes -> apostrophe
    s = re.sub(r'[""„‟″]',  '"', s)   # smart double quotes -> straight
    s = re.sub(r'[–—―−]',   '-', s)   # en/em-dash -> hyphen
    s = re.sub(r'[   ]', ' ', s)   # non-breaking spaces
    return re.sub(r'\s+', ' ', s).strip().lower()


def _normalize_type(s):
    """
    Mirrors the Apps Script normalizeType_() function.
    Collapses all race type variants to 'Road' or 'Dirt'.
    Unknown values pass through verbatim (title-cased).
    """
    raw = str(s or '').strip()
    key = re.sub(r'\s+', ' ', raw.lower())

    # Exact map — same entries as Apps Script CONFIG.RACE_TYPE_MAP
    _MAP = {
        'road': 'Road', 'road circuit': 'Road', 'road sprint': 'Road',
        'street': 'Road', 'street race': 'Road',
        'dirt': 'Dirt', 'dirt circuit': 'Dirt', 'dirt point to point': 'Dirt',
        'dirt trail': 'Dirt', 'dirt scramble': 'Dirt',
        'cross-country': 'Dirt', 'cross country': 'Dirt',
        'crosscountry': 'Dirt', 'cross-country circuit': 'Dirt',
        'cross country circuit': 'Dirt',
    }
    if key in _MAP:
        return _MAP[key]

    # Keyword fallback — dirt/cross-country checked first (same priority as JS)
    if re.search(r'\bdirt\b|\bcross[\s-]?country\b', key):
        return 'Dirt'
    if re.search(r'\broad\b|\bstreet\b', key):
        return 'Road'

    return raw   # unknown: pass through unchanged


def _car_key(car_name, cls, car_type):
    """Composite match key: normalized (Car Name, Class, Type)."""
    return (_normalize_str(car_name),
            _normalize_str(cls),
            _normalize_str(_normalize_type(car_type)))


def _parse_sheet_time(s):
    """
    Parses a time string in the Apps Script's formatTime_() output format -
    "m:ss.fff" or "h:mm:ss.fff" (no leading zero on minutes unless hours are
    present) - as read back from the Best by Track+Class tab. Different from
    results_extractor.time_to_seconds(), which parses the "MM:SS.mmm" format
    that Claude/telemetry produce. Returns None for blank/unparseable input.
    """
    s = str(s or '').strip()
    if not s:
        return None
    parts = s.split(':')
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, IndexError):
        return None


def _format_seconds(secs):
    """Formats seconds as 'm:ss.fff' for display, matching the Apps Script's
    formatTime_() so a previous-record time reads the same way on both sides."""
    if secs is None:
        return None
    total = max(0.0, secs)
    minutes = int(total // 60)
    seconds = total - minutes * 60
    return f"{minutes}:{seconds:06.3f}"


def _column_letter(idx):
    """0-indexed column number -> spreadsheet letter (0 -> A, 13 -> N)."""
    idx += 1
    letters = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters

# Column order must match your sheet headers exactly
RESULTS_COLUMNS = [
    "date", "race_id", "position", "car", "class",
    "race_type", "track", "total_racers", "best_lap", "race_time", "notes"
]

OPPONENTS_COLUMNS = [
    "race_id", "track", "position", "gamertag", "car",
    "class", "pi", "best_lap", "race_time", "gap_to_me"
]

log = logging.getLogger(__name__)


class SheetsWriter:
    """
    Handles all Google Sheets write operations.
    Appends race results and opponent rows to the appropriate tabs.
    """

    def __init__(self, game_version="FH5"):
        self.spreadsheet_id = FH5_SPREADSHEET_ID if game_version == "FH5" else FH6_SPREADSHEET_ID
        self.service        = self._build_service()
        self._best_times        = {}      # (track, class) -> seconds, lazy-loaded
        self._best_times_loaded = False

        # Car Card overlay state - see update_car_card()/learn_car_ordinal().
        self._car_ordinals        = {}    # ordinal (str) -> {full_name, car_name}, from CAR_ORDINALS_FILE
        self._car_ordinals_loaded = False
        self._cars_by_name        = {}    # normalized car name -> [row dict, ...] (variants by class/type)
        self._cars_by_ordinal     = {}    # ordinal (str) -> row dict, only for rows with Ordinal filled in
        self._cars_hmap           = {}    # Cars tab header name -> 0-indexed column, from the same read
        self._car_card_loaded     = False

    def _build_service(self):
        """Authenticate and build the Google Sheets API service."""
        try:
            creds = service_account.Credentials.from_service_account_file(
                SHEETS_CREDENTIALS,
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            service = build("sheets", "v4", credentials=creds)
            log.info("Google Sheets service authenticated successfully")
            return service
        except Exception as e:
            log.error(f"Failed to authenticate Google Sheets: {e}")
            raise

    def write_race(self, race_result, opponents):
        """
        Write a completed race to the sheet.
        Appends one row to Results and one row per opponent to Opponents,
        then refreshes Races/Wins counts in the Cars tab.
        """
        # Checked first and independently of the Sheets writes below - it only
        # needs race_result (already fully extracted) plus a local cache, so
        # the on-screen alert doesn't wait on Results/Opponents/Cars writes.
        # Wrapped defensively: a bug in the overlay path must never prevent
        # the actual Results row from being written.
        try:
            self.check_for_new_record(race_result)
        except Exception as e:
            log.error(f"Record-check/overlay update failed (non-fatal): {e}")

        # Same reasoning as check_for_new_record above - wrapped so a bug in
        # the Car Card learning path can never block the actual Results write.
        try:
            self.learn_car_ordinal(race_result)
        except Exception as e:
            log.error(f"Car ordinal learning failed (non-fatal): {e}")

        self._append_result(race_result)

        if opponents and race_result.get("notes") != "Spec Race":
            self._append_opponents(opponents)
            log.info(f"Wrote {len(opponents)} opponent row(s) to Opponents tab")
        else:
            log.info("No opponents ahead of you this race - Opponents tab unchanged")

        self.update_car_stats()

    def check_for_new_record(self, race_result):
        """
        Compares this race's time against the cached Best by Track+Class
        record for (Track, Class) and writes overlay_state.json if it beats
        it, so a local OBS Browser Source overlay (see controller.py's
        /overlay routes) can flash a "new record" alert.

        Non-competitive races (Spec Race/Touge/Time Attack) are skipped -
        they're excluded from the Y-flag/record system entirely, same as the
        Races/Wins tally (see NON_COMPETITIVE_NOTES).

        The cache is loaded once from the Best by Track+Class tab (read-only -
        this script never writes that tab, the Apps Script owns it) and then
        updated in memory as records fall during the session, so repeated
        checks don't each cost a Sheets API round-trip.
        """
        if race_result.get("notes") in NON_COMPETITIVE_NOTES:
            return

        if not self._best_times_loaded:
            self._load_best_times_cache()

        best_lap_sec  = time_to_seconds(race_result.get("best_lap"))
        race_time_sec = time_to_seconds(race_result.get("race_time"))
        time_sec = best_lap_sec if best_lap_sec is not None else race_time_sec
        if time_sec is None:
            return

        track = race_result.get("track", "")
        cls   = race_result.get("class", "")
        if not track or not cls:
            return

        key = (_normalize_str(track), _normalize_str(cls))
        prior = self._best_times.get(key)
        if prior is not None and time_sec >= prior:
            return   # not a new record

        self._best_times[key] = time_sec   # update cache immediately this session
        self._write_overlay_event(race_result, time_sec, prior)
        log.info(
            f"NEW RECORD: {race_result.get('car')} @ {track} ({cls}) - "
            f"{race_result.get('best_lap') or race_result.get('race_time')} "
            f"(previous: {_format_seconds(prior) if prior is not None else 'none'})"
        )

    def _load_best_times_cache(self):
        """Reads the Best by Track+Class tab into {(track, class): seconds}."""
        try:
            resp = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{BEST_BY_TAB}!A:H"
            ).execute()
        except HttpError as e:
            log.error(f"Failed to read {BEST_BY_TAB} tab for record tracking: {e}")
            self._best_times_loaded = True   # don't retry every single race
            return

        rows = resp.get("values", [])
        if len(rows) < 2:
            self._best_times_loaded = True
            return

        headers = [h.strip().lower() for h in rows[0]]
        try:
            track_col = headers.index("track")
            class_col = headers.index("class")
            time_col  = headers.index("best time")
        except ValueError as e:
            log.error(f"{BEST_BY_TAB} tab missing expected column: {e}")
            self._best_times_loaded = True
            return

        need_cols = max(track_col, class_col, time_col)
        cache = {}
        for row in rows[1:]:
            if len(row) <= need_cols:
                continue
            track = row[track_col].strip()
            cls   = row[class_col].strip()
            secs  = _parse_sheet_time(row[time_col])
            if not track or secs is None:
                continue
            cache[(_normalize_str(track), _normalize_str(cls))] = secs

        self._best_times = cache
        self._best_times_loaded = True
        log.info(f"Loaded {len(cache)} track/class best times for record tracking")

    def _write_overlay_event(self, race_result, time_sec, prior_sec):
        """Writes overlay_state.json (temp-then-rename, same pattern as the
        capture agent's screenshot writes) so the overlay page never reads a
        half-written file."""
        event = {
            "race_id":       race_result.get("race_id"),
            "car":           race_result.get("car"),
            "track":         race_result.get("track"),
            "class":         race_result.get("class"),
            "time":          race_result.get("best_lap") or race_result.get("race_time"),
            "previous_time": _format_seconds(prior_sec),
            "timestamp":     datetime.now().isoformat()
        }
        tmp_path = OVERLAY_STATE_FILE + ".tmp"
        try:
            os.makedirs(OVERLAY_FOLDER, exist_ok=True)
            with open(tmp_path, "w") as f:
                json.dump(event, f)
            os.replace(tmp_path, OVERLAY_STATE_FILE)
        except Exception as e:
            log.error(f"Failed to write overlay state: {e}")

    # ============================================================
    # Car Card overlay - see TODO.md's "Car Card Overlay" entry.
    # Triggered by car_ordinal changing in telemetry (main.py wires this to
    # update_car_card()), independent of race state entirely. Uses two
    # sources of car identity: CAR_ORDINALS_FILE (a local seed/learned
    # database mapping Forza's numeric ordinal to a car's full name and its
    # abbreviated scoreboard name) and the Cars tab itself (for Year/MFG/
    # Model/Tuner/Painter/Races/Wins/Win Rate). All Cars tab columns are
    # found by header text, never hardcoded position.
    # ============================================================

    def _load_car_ordinals(self):
        """Loads the local ordinal->{full_name, car_name} seed/learned file."""
        try:
            with open(CAR_ORDINALS_FILE) as f:
                self._car_ordinals = json.load(f)
        except FileNotFoundError:
            self._car_ordinals = {}
        except Exception as e:
            log.error(f"Failed to load car ordinals file: {e}")
            self._car_ordinals = {}
        self._car_ordinals_loaded = True

    def _save_car_ordinals(self):
        """Writes CAR_ORDINALS_FILE back out (temp-then-rename)."""
        try:
            os.makedirs(os.path.dirname(CAR_ORDINALS_FILE), exist_ok=True)
            tmp_path = CAR_ORDINALS_FILE + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(self._car_ordinals, f, indent=2, sort_keys=True)
            os.replace(tmp_path, CAR_ORDINALS_FILE)
        except Exception as e:
            log.error(f"Failed to save car ordinals file: {e}")

    def _load_car_card_cache(self):
        """
        Reads the whole Cars tab into memory for Car Card lookups, keyed both
        by Ordinal (fast path, once a row has been backfilled) and by
        normalized Car Name (fallback path, used together with the local
        ordinal-seed database). A wide A:ZZ range is used deliberately since
        the exact column where user-added fields like Painter/Ordinal land
        isn't assumed - only the header text is trusted.
        """
        try:
            resp = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{CARS_TAB}!A:ZZ"
            ).execute()
        except HttpError as e:
            log.error(f"Failed to read Cars tab for Car Card: {e}")
            self._cars_by_name = {}
            self._cars_by_ordinal = {}
            self._car_card_loaded = True
            return

        rows = resp.get("values", [])
        if len(rows) < 2:
            self._cars_by_name = {}
            self._cars_by_ordinal = {}
            self._car_card_loaded = True
            return

        headers = [h.strip() for h in rows[0]]
        hmap = {h: i for i, h in enumerate(headers) if h}

        required = ["Car Name", "Class", "Type"]
        missing = [c for c in required if c not in hmap]
        if missing:
            log.error(f"Cars tab missing required column(s) for Car Card: {missing}")
            self._cars_by_name = {}
            self._cars_by_ordinal = {}
            self._car_card_loaded = True
            return

        def cell(row, name, default=""):
            idx = hmap.get(name)
            if idx is None or idx >= len(row):
                return default
            return row[idx].strip()

        by_name = defaultdict(list)
        by_ordinal = {}
        for sheet_row_idx, row in enumerate(rows[1:], start=2):
            car_name = cell(row, "Car Name")
            if not car_name:
                continue
            entry = {
                "row_number": sheet_row_idx,
                "year":       cell(row, "Year"),
                "mfg":        cell(row, "MFG"),
                "model":      cell(row, "Model"),
                "car_name":   car_name,
                "class":      cell(row, "Class"),
                "type":       cell(row, "Type"),
                "tuner":      cell(row, "Tuner"),
                "painter":    cell(row, "Painter"),   # blank until the user adds this column
                "races":      cell(row, "Races"),
                "wins":       cell(row, "Wins"),
                "win_rate":   cell(row, "Win Rate"),
                "ordinal":    cell(row, "Ordinal"),   # blank until backfilled or the column exists
            }
            by_name[_normalize_str(car_name)].append(entry)
            if entry["ordinal"]:
                by_ordinal[entry["ordinal"]] = entry

        self._cars_by_name = dict(by_name)
        self._cars_by_ordinal = by_ordinal
        self._cars_hmap = hmap
        self._car_card_loaded = True
        log.info(f"Loaded {sum(len(v) for v in by_name.values())} Cars tab rows for "
                 f"Car Card ({len(by_ordinal)} with Ordinal already set)")

    def update_car_card(self, ordinal, live_class):
        """
        Called whenever telemetry detects the selected car's ordinal changed
        (car select, free roam, between races - not tied to a race). Resolves
        the ordinal to a car identity and Cars-tab stats, then writes
        car_card_state.json for the overlay. Best-effort by design: an
        unknown ordinal or an unmatched Cars-tab row still produces a card
        (just a sparser one) rather than doing nothing.
        """
        if not self._car_ordinals_loaded:
            self._load_car_ordinals()
        if not self._car_card_loaded:
            self._load_car_card_cache()

        ordinal_str = str(ordinal)
        seed      = self._car_ordinals.get(ordinal_str, {})
        full_name = seed.get("full_name")
        car_name  = seed.get("car_name")

        row = self._cars_by_ordinal.get(ordinal_str)
        if row is None and car_name:
            candidates = self._cars_by_name.get(_normalize_str(car_name), [])
            if candidates:
                # Prefer whichever tune matches telemetry's live class; the
                # Road-vs-Dirt tiebreak (same Class, different Type) is a
                # known open wrinkle - see TODO.md - not solved here.
                row = next((c for c in candidates if c["class"] == live_class), candidates[0])

        card = {
            "ordinal":   ordinal_str,
            "full_name": full_name,
            "car_name":  car_name,
            "known":     row is not None,
            "year":      row["year"]     if row else "",
            "mfg":       row["mfg"]      if row else "",
            "model":     row["model"]    if row else "",
            "class":     row["class"]    if row else (live_class or ""),
            "tuner":     row["tuner"]    if row else "",
            "painter":   row["painter"]  if row else "",
            "races":     row["races"]    if row else "",
            "wins":      row["wins"]     if row else "",
            "win_rate":  row["win_rate"] if row else "",
            "timestamp": datetime.now().isoformat()
        }
        self._write_car_card_state(card)

    def _write_car_card_state(self, card):
        """Writes car_card_state.json (temp-then-rename)."""
        tmp_path = CAR_CARD_STATE_FILE + ".tmp"
        try:
            os.makedirs(OVERLAY_FOLDER, exist_ok=True)
            with open(tmp_path, "w") as f:
                json.dump(card, f)
            os.replace(tmp_path, CAR_CARD_STATE_FILE)
        except Exception as e:
            log.error(f"Failed to write car card state: {e}")

    def learn_car_ordinal(self, race_result):
        """
        Called after every race. This is the one moment telemetry's
        car_ordinal and Claude's OCR'd car name are both known together, so
        it's the only place new ordinal->name knowledge can come from short
        of the community seed file. Also backfills the Cars tab's Ordinal
        column on the matched row if it's blank there - same self-healing
        pattern as the Year/MFG/Model backfill in the Apps Script's
        applyUpdates_, rather than a one-time reconciliation project.
        """
        ordinal = race_result.get("car_ordinal")
        car_name = race_result.get("car")
        if not ordinal or ordinal == "?" or not car_name:
            return
        ordinal_str = str(ordinal)

        if not self._car_ordinals_loaded:
            self._load_car_ordinals()

        entry = self._car_ordinals.get(ordinal_str)
        if entry is None:
            self._car_ordinals[ordinal_str] = {"full_name": None, "car_name": car_name}
            self._save_car_ordinals()
        elif not entry.get("car_name"):
            entry["car_name"] = car_name
            self._save_car_ordinals()

        if not self._car_card_loaded:
            self._load_car_card_cache()
        if not self._cars_hmap or "Ordinal" not in self._cars_hmap:
            return   # "Ordinal" column doesn't exist on the sheet yet

        candidates = self._cars_by_name.get(_normalize_str(car_name), [])
        match = next((c for c in candidates if c["class"] == race_result.get("class")), None) \
                or (candidates[0] if candidates else None)
        if not match or match.get("ordinal"):
            return   # no matching row, or it already has an Ordinal - nothing to do

        col_letter = _column_letter(self._cars_hmap["Ordinal"])
        try:
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"{CARS_TAB}!{col_letter}{match['row_number']}",
                valueInputOption="RAW",
                body={"values": [[ordinal_str]]}
            ).execute()
            match["ordinal"] = ordinal_str
            self._cars_by_ordinal[ordinal_str] = match
            log.info(f"Backfilled Ordinal={ordinal_str} for {car_name} (Cars row {match['row_number']})")
        except HttpError as e:
            log.error(f"Failed to backfill Ordinal for {car_name}: {e}")

    def _build_identity_index(self):
        """
        Maps normalized "{Year} {MFG} {Model}" -> Cars tab row(s), for the
        identity-based half of sync_ordinals_from_seed(). Built from
        self._cars_by_name (already loaded by _load_car_card_cache()), so it
        covers every Cars tab row regardless of whether Ordinal or car_name
        is set yet.

        Deliberately does NOT try to split the seed file's full_name (e.g.
        "1962 Ferrari 250 GTO") into Year/MFG/Model - manufacturer names are
        often multiple words (Alfa Romeo, Aston Martin, Land Rover,
        Mercedes-Benz...) so that split is ambiguous without a hardcoded
        manufacturer list. Instead this builds the same "{Year} {MFG}
        {Model}" string from the Cars tab's own separate columns and compares
        the whole normalized string against full_name directly - sidesteps
        the split entirely, at the cost of only matching entries where the
        wording lines up exactly (a deliberate tradeoff: no fuzzy matching,
        so a mismatch just falls through as unmatched rather than risking a
        wrong car getting an ordinal).
        """
        index = defaultdict(list)
        for candidates in self._cars_by_name.values():
            for row in candidates:
                if row["year"] and row["mfg"] and row["model"]:
                    key = _normalize_str(f"{row['year']} {row['mfg']} {row['model']}")
                    index[key].append(row)
        return index

    def sync_ordinals_from_seed(self):
        """
        Bulk-backfills the Cars tab's Ordinal column from the local seed
        file, without waiting for each car to be raced again individually.
        Tries two matching strategies per seed entry, in order:

        1. By learned car_name (same as learn_car_ordinal()'s backfill step)
           - only available once that car has actually been raced at least
           once, since car_name is the scoreboard's abbreviated name and
           nothing else in the pipeline produces it.
        2. By identity ("{Year} {MFG} {Model}", via _build_identity_index())
           - works for cars never raced yet, as long as the user has already
           manually entered that car's Year/MFG/Model on its Cars tab row.
           This is the path that makes manually typing Ordinal numbers
           unnecessary for a car you haven't raced but have catalogued -
           added 2026-09-12 because waiting on every car to be raced first
           was going to take weeks, and manually cross-referencing an
           ordinal-ordered seed file against the spreadsheet by hand was
           slow and error-prone in the other direction.

        An ordinal identifies the car MODEL, not a specific tune, so if a
        car has multiple rows (different Class/Type builds), all of them get
        backfilled with the same ordinal - unlike learn_car_ordinal(), which
        only touches the one row matching that specific race's live class.
        Returns a summary dict; meant to be called from sync_ordinals.py,
        not the live pipeline.
        """
        if not self._car_ordinals_loaded:
            self._load_car_ordinals()
        if not self._car_card_loaded:
            self._load_car_card_cache()

        if "Ordinal" not in self._cars_hmap:
            return {"status": "error", "message": "Cars tab has no 'Ordinal' column yet"}

        identity_index = self._build_identity_index()

        col_letter = _column_letter(self._cars_hmap["Ordinal"])
        updates, backfilled = [], []
        skipped_no_match = 0
        skipped_already_set = 0

        for ordinal_str, entry in self._car_ordinals.items():
            car_name = entry.get("car_name")
            full_name = entry.get("full_name")

            matched_by = None
            candidates = []
            if car_name:
                candidates = self._cars_by_name.get(_normalize_str(car_name), [])
                if candidates:
                    matched_by = "car_name"
            if not candidates and full_name:
                candidates = identity_index.get(_normalize_str(full_name), [])
                if candidates:
                    matched_by = "identity"

            if not candidates:
                skipped_no_match += 1
                continue
            for row in candidates:
                if row.get("ordinal"):
                    skipped_already_set += 1
                    continue
                updates.append({
                    "range":  f"{CARS_TAB}!{col_letter}{row['row_number']}",
                    "values": [[ordinal_str]]
                })
                row["ordinal"] = ordinal_str
                self._cars_by_ordinal[ordinal_str] = row
                backfilled.append({"car_name": row["car_name"], "row": row["row_number"],
                                    "matched_by": matched_by})

        if not updates:
            return {"status": "ok", "backfilled": 0,
                    "skipped_no_match": skipped_no_match,
                    "skipped_already_set": skipped_already_set}

        try:
            self.service.spreadsheets().values().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"valueInputOption": "RAW", "data": updates}
            ).execute()
        except HttpError as e:
            log.error(f"Failed to bulk-backfill Ordinal column: {e}")
            return {"status": "error", "message": str(e)}

        log.info(f"Bulk-backfilled Ordinal for {len(backfilled)} Cars tab row(s)")
        return {"status": "ok", "backfilled": len(backfilled), "rows": backfilled,
                "skipped_no_match": skipped_no_match,
                "skipped_already_set": skipped_already_set}

    def update_car_stats(self):
        """
        Tally races and wins per (Car Name, Class, Type) from the full Results
        history, then write counts into Races (col N) and Wins (col O) of the
        Cars tab.

        Matching uses the same composite key and normalization as the Apps Script
        so a 2019 Corvette S1 Road and a 2019 Corvette S1 Dirt are counted
        separately.  Unmatched Results rows (car not yet in Cars) are skipped.
        Can be called standalone to rebuild counts from scratch.
        """
        # --- Read Results tab (A:K covers all RESULTS_COLUMNS) ---
        try:
            resp = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{RESULTS_TAB}!A:K"
            ).execute()
        except HttpError as e:
            log.error(f"Failed to read Results tab for car stats: {e}")
            return

        rows = resp.get("values", [])
        if len(rows) < 2:
            log.info("No results rows found — Cars stats not updated")
            return

        headers = [h.lower().replace(" ", "_") for h in rows[0]]
        try:
            car_col       = headers.index("car")
            pos_col       = headers.index("position")
            class_col     = headers.index("class")
            race_type_col = headers.index("race_type")
            notes_col     = headers.index("notes")
        except ValueError as e:
            log.error(f"Results tab missing expected column: {e}")
            return

        need_cols = max(car_col, pos_col, class_col, race_type_col, notes_col)
        races_by_key = defaultdict(int)
        wins_by_key  = defaultdict(int)
        for row in rows[1:]:
            if len(row) <= need_cols:
                continue
            car       = row[car_col].strip()
            pos       = row[pos_col].strip()
            cls       = row[class_col].strip()
            race_type = row[race_type_col].strip()
            notes     = row[notes_col].strip()
            if not car:
                continue
            # Non-competitive races (Spec Race, Touge, Time Attack) have no
            # real opponents to beat — exclude from Races/Wins so win rate
            # isn't inflated by always-win solo/stock-tune events.
            if notes in NON_COMPETITIVE_NOTES:
                continue
            key = _car_key(car, cls, race_type)
            races_by_key[key] += 1
            if pos == "1":
                wins_by_key[key] += 1

        # --- Read Cars tab (A:O covers through Wins column) ---
        try:
            resp = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"{CARS_TAB}!A:O"
            ).execute()
        except HttpError as e:
            log.error(f"Failed to read Cars tab: {e}")
            return

        car_rows = resp.get("values", [])
        if len(car_rows) < 2:
            log.info("Cars tab has no data rows — stats not updated")
            return

        # Cars tab layout:
        #   A=FH6(0)  B=Year(1)  C=MFG(2)   D=Model(3)  E=Car Name(4)
        #   F=D(5)    G=OC(6)    H=Class(7)  I=Type(8)   J=Fav(9)
        #   K=Notes(10) L=Tuner(11) M=Tune(12) N=Races(13) O=Wins(14)
        CAR_NAME_COL = 4
        CLASS_COL    = 7
        TYPE_COL     = 8

        updates = []
        matched = 0
        for sheet_row_idx, row in enumerate(car_rows[1:], start=2):
            if len(row) <= TYPE_COL:
                continue
            key = _car_key(row[CAR_NAME_COL], row[CLASS_COL], row[TYPE_COL])
            if key not in races_by_key:
                continue
            updates.append({
                "range":  f"{CARS_TAB}!N{sheet_row_idx}:O{sheet_row_idx}",
                "values": [[races_by_key[key], wins_by_key[key]]]
            })
            matched += 1

        if not updates:
            log.info("Car stats: no Cars tab rows matched Results — nothing updated")
            return

        try:
            self.service.spreadsheets().values().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"valueInputOption": "RAW", "data": updates}
            ).execute()
            log.info(f"Car stats updated: {matched} car(s) — "
                     f"{sum(races_by_key.values())} total races tallied")
        except HttpError as e:
            log.error(f"Failed to write car stats: {e}")

    def _append_result(self, race_result):
        """Append one row to the Results tab."""
        row = [str(race_result.get(col, "")) for col in RESULTS_COLUMNS]

        try:
            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{RESULTS_TAB}!A:K",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]}
            ).execute()

            log.info(
                f"Results written | Race: {race_result.get('race_id')} | "
                f"Track: {race_result.get('track')} | "
                f"Position: {race_result.get('position')} | "
                f"Car: {race_result.get('car')}"
            )

        except HttpError as e:
            log.error(f"Google Sheets API error writing result: {e}")
        except Exception as e:
            log.error(f"Unexpected error writing result: {e}")

    def _append_opponents(self, opponents):
        """Append one row per opponent to the Opponents tab."""
        rows = [
            [str(opp.get(col, "")) for col in OPPONENTS_COLUMNS]
            for opp in opponents
        ]

        try:
            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=f"{OPPONENTS_TAB}!A:J",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows}
            ).execute()

        except HttpError as e:
            log.error(f"Google Sheets API error writing opponents: {e}")
        except Exception as e:
            log.error(f"Unexpected error writing opponents: {e}")
