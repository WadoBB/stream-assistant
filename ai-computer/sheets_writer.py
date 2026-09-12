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
                     OVERLAY_FOLDER, OVERLAY_STATE_FILE)
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
