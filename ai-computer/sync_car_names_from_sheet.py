# =============================================================
# Stream Assistant - Reverse Car Name Sync
# Runs on the AI COMPUTER, on demand (not part of the live pipeline).
#
# Reverse direction of sync_ordinals.py: for every Cars-tab row that
# already has an Ordinal set, backfills car_ordinals.json's car_name for
# that ordinal if it's still null. Closes a gap where a car matched by
# sync_ordinals_from_seed()'s identity path (or the AI-reasoning pass
# before its own JSON write existed) ends up fully resolved on the Sheet
# but still shows car_name: null in the JSON.
#
# Usage: python sync_car_names_from_sheet.py [FH5|FH6]
# Also triggerable remotely via controller.py's /car_card/sync_car_names.
# =============================================================

import sys
import json
import logging

logging.basicConfig(level=logging.ERROR)

from sheets_writer import SheetsWriter


def main():
    game = sys.argv[1].upper() if len(sys.argv) > 1 else "FH6"
    if game not in ("FH5", "FH6"):
        print(json.dumps({"status": "error", "message": f"Unknown game version: {game}"}))
        sys.exit(1)

    writer = SheetsWriter(game_version=game)
    result = writer.sync_car_names_from_sheet()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
