# =============================================================
# Stream Assistant - Module 4: Sheets Writer
# Runs on the AI COMPUTER.
# Writes race results to the Results tab and opponents who
# finished ahead to the Opponents tab in Google Sheets.
# =============================================================

import re
import logging
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from collections import defaultdict
from config import SHEETS_CREDENTIALS, FH5_SPREADSHEET_ID, FH6_SPREADSHEET_ID, RESULTS_TAB, OPPONENTS_TAB, CARS_TAB


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
        self._append_result(race_result)

        if opponents and race_result.get("notes") != "Spec Race":
            self._append_opponents(opponents)
            log.info(f"Wrote {len(opponents)} opponent row(s) to Opponents tab")
        else:
            log.info("No opponents ahead of you this race - Opponents tab unchanged")

        self.update_car_stats()

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
        except ValueError as e:
            log.error(f"Results tab missing expected column: {e}")
            return

        need_cols = max(car_col, pos_col, class_col, race_type_col)
        races_by_key = defaultdict(int)
        wins_by_key  = defaultdict(int)
        for row in rows[1:]:
            if len(row) <= need_cols:
                continue
            car       = row[car_col].strip()
            pos       = row[pos_col].strip()
            cls       = row[class_col].strip()
            race_type = row[race_type_col].strip()
            if not car:
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
