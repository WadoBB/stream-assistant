# =============================================================
# Stream Assistant - Apply Ordinal Matches
# Runs on the AI COMPUTER, on demand (not part of the live pipeline).
#
# Writes back ordinal<->Cars-tab-row matches judged outside exact string
# matching (see export_unmatched_ordinals.py's doc comment for why exact
# matching alone leaves a gap) - a human eyeballing the seed list, or an AI
# reasoning over naming differences like scoreboard abbreviations. Backfills
# both the Cars tab's Ordinal column and car_ordinals.json's car_name for
# each match - the same two writes learn_car_ordinal() makes when a car is
# actually raced, just arriving via reasoning instead of a live race.
#
# Usage: python apply_ordinal_matches.py <matches.json> [FH5|FH6]
#   matches.json: [{"row_number": N, "ordinal": "N", "car_name": "..."}]
# Also triggerable remotely via controller.py's POST /car_card/apply_ordinal_matches.
# =============================================================

import sys
import json
import logging

logging.basicConfig(level=logging.ERROR)

from googleapiclient.errors import HttpError
from sheets_writer import SheetsWriter, CARS_TAB, _column_letter


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "matches.json path argument required"}))
        sys.exit(1)

    matches_path = sys.argv[1]
    game = sys.argv[2].upper() if len(sys.argv) > 2 and sys.argv[2] else "FH6"
    if game not in ("FH5", "FH6"):
        print(json.dumps({"status": "error", "message": f"Unknown game version: {game}"}))
        sys.exit(1)

    try:
        with open(matches_path) as f:
            matches = json.load(f)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"Could not read matches file: {e}"}))
        sys.exit(1)

    writer = SheetsWriter(game_version=game)
    writer._load_car_ordinals()
    writer._load_car_card_cache()

    if "Ordinal" not in writer._cars_hmap:
        print(json.dumps({"status": "error", "message": "Cars tab has no 'Ordinal' column yet"}))
        sys.exit(1)

    col_letter = _column_letter(writer._cars_hmap["Ordinal"])
    updates, applied = [], []
    skipped_already_set = 0
    skipped_invalid = 0

    for m in matches:
        row_number = m.get("row_number")
        ordinal = m.get("ordinal")
        car_name = m.get("car_name")
        if not row_number or not ordinal:
            skipped_invalid += 1
            continue
        ordinal_str = str(ordinal)

        row = writer._cars_by_ordinal.get(ordinal_str)
        already_set = row is not None
        if not already_set:
            for candidates in writer._cars_by_name.values():
                for r in candidates:
                    if r["row_number"] == row_number and r.get("ordinal"):
                        already_set = True
                        break
        if already_set:
            skipped_already_set += 1
            continue

        updates.append({
            "range":  f"{CARS_TAB}!{col_letter}{row_number}",
            "values": [[ordinal_str]]
        })
        applied.append({"row_number": row_number, "ordinal": ordinal_str, "car_name": car_name})

        entry = writer._car_ordinals.get(ordinal_str)
        if entry is None:
            writer._car_ordinals[ordinal_str] = {"full_name": None, "car_name": car_name}
        elif not entry.get("car_name") and car_name:
            entry["car_name"] = car_name

    if updates:
        try:
            writer.service.spreadsheets().values().batchUpdate(
                spreadsheetId=writer.spreadsheet_id,
                body={"valueInputOption": "RAW", "data": updates}
            ).execute()
        except HttpError as e:
            print(json.dumps({"status": "error", "message": f"Failed to write Ordinal column: {e}"}))
            sys.exit(1)
        writer._save_car_ordinals()

    print(json.dumps({
        "status": "ok",
        "applied": len(applied),
        "rows": applied,
        "skipped_already_set": skipped_already_set,
        "skipped_invalid": skipped_invalid
    }))


if __name__ == "__main__":
    main()
