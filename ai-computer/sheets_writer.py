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
from config import SHEETS_CREDENTIALS, SHEETS_SPREADSHEET_ID, RESULTS_TAB, OPPONENTS_TAB

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

    def __init__(self):
        self.service = self._build_service()

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
        Appends one row to Results and one row per opponent to Opponents.
        """
        self._append_result(race_result)

        if opponents:
            self._append_opponents(opponents)
            log.info(f"Wrote {len(opponents)} opponent row(s) to Opponents tab")
        else:
            log.info("No opponents ahead of you this race - Opponents tab unchanged")

    def _append_result(self, race_result):
        """Append one row to the Results tab."""
        row = [str(race_result.get(col, "")) for col in RESULTS_COLUMNS]

        try:
            self.service.spreadsheets().values().append(
                spreadsheetId=SHEETS_SPREADSHEET_ID,
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
                spreadsheetId=SHEETS_SPREADSHEET_ID,
                range=f"{OPPONENTS_TAB}!A:J",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows}
            ).execute()

        except HttpError as e:
            log.error(f"Google Sheets API error writing opponents: {e}")
        except Exception as e:
            log.error(f"Unexpected error writing opponents: {e}")
