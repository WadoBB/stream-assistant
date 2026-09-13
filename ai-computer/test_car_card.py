# =============================================================
# Stream Assistant - Real-Data Car Card Test
# Runs on the AI COMPUTER, on demand (not part of the live pipeline).
#
# Calls sheets_writer.py's update_car_card() directly - the exact same
# function the live pipeline calls when telemetry detects a car change -
# instead of writing canned placeholder data. This is what lets
# controller.py's /car_card/test show a real ordinal's actual identity and
# stats (or correctly show "not yet raced" for one that hasn't matched a
# Cars tab row), without controller.py itself needing the Google API
# dependency - same reasoning as sync_ordinals.py running as a subprocess.
#
# Usage: python test_car_card.py <ordinal> [live_class] [FH5|FH6]
#   live_class may be an empty string to mean "no override".
# =============================================================

import sys
import json
import logging

logging.basicConfig(level=logging.ERROR)

from sheets_writer import SheetsWriter


def main():
    if len(sys.argv) < 2 or not sys.argv[1]:
        print(json.dumps({"status": "error", "message": "ordinal argument required"}))
        sys.exit(1)

    ordinal = sys.argv[1]
    live_class = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
    game = sys.argv[3].upper() if len(sys.argv) > 3 and sys.argv[3] else "FH6"
    if game not in ("FH5", "FH6"):
        print(json.dumps({"status": "error", "message": f"Unknown game version: {game}"}))
        sys.exit(1)

    writer = SheetsWriter(game_version=game)
    writer.update_car_card(ordinal, live_class)
    print(json.dumps({"status": "ok"}))


if __name__ == "__main__":
    main()
