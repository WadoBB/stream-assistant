# =============================================================
# Stream Assistant - Module 4: Sheets Writer
# Runs on the AI COMPUTER.
# Writes race results to the Results tab and opponents who
# finished ahead to the Opponents tab in Google Sheets.
# =============================================================

import logging
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from collections import defaultdict
from config import SHEETS_CREDENTIALS, FH5_SPREADSHEET_ID, FH6_SPREADSHEET_ID, RESULTS_TAB, OPPONENTS_TAB, CARS_TAB

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

        if opponents:
            self._append_opponents(opponents)
            log.info(f"Wrote {len(opponents)} opponent row(s) to Opponents tab")
        else:
            log.info("No opponents ahead of you this race - Opponents tab unchanged")

        self.update_car_stats()

    def update_car_stats(self):
        """
        Tally races and wins per car from the full Results tab history,
        then write the counts into Races (col N) and Wins (col O) of the Cars tab.

        Matching is on car name exactly as it appears in both sheets.
        Cars in the Results tab that have no row in Cars are silently skipped.
        Can also be called standalone to rebuild counts from scratch.
        """
        # --- Read Results tab ---
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

        headers = [h.lower() for h in rows[0]]
        try:
            car_col = headers.index("car")
            pos_col = headers.index("position")
        except ValueError as e:
            log.error(f"Results tab missing expected column: {e}")
            return

        races_by_car = defaultdict(int)
        wins_by_car  = defaultdict(int)
        for row in rows[1:]:
            if len(row) <= max(car_col, pos_col):
                continue
            car  = row[car_col].strip()
            pos  = row[pos_col].strip()
            if not car:
                continue
            races_by_car[car] += 1
            if pos == "1":
                wins_by_car[car] += 1

        # --- Read Cars tab ---
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

        # Cars tab layout: A=FH6, B=Year, C=MFG, D=Model, E=Car Name, ..., N=Races, O=Wins
        CAR_NAME_COL = 4   # column E (0-based)

        updates = []
        matched = 0
        for sheet_row_idx, row in enumerate(car_rows[1:], start=2):  # row 2 = first data row
            if len(row) <= CAR_NAME_COL:
                continue
            car_name = row[CAR_NAME_COL].strip()
            if car_name not in races_by_car:
                continue
            updates.append({
                "range":  f"{CARS_TAB}!N{sheet_row_idx}:O{sheet_row_idx}",
                "values": [[races_by_car[car_name], wins_by_car[car_name]]]
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
                     f"{sum(races_by_car.values())} total races tallied")
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
