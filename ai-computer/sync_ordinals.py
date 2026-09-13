# =============================================================
# Stream Assistant - Bulk Ordinal Sync
# Runs on the AI COMPUTER, on demand (not part of the live pipeline).
#
# Backfills the Cars tab's Ordinal column from everything already learned
# in car_ordinals.json, for every car whose ordinal->name mapping was
# learned from a past race but whose Cars tab row hasn't caught up yet -
# without needing to race that car again to trigger the per-race lazy
# backfill in sheets_writer.py's learn_car_ordinal().
#
# Usage: python sync_ordinals.py [FH5|FH6]   (default FH6)
# Also triggerable remotely via controller.py's /car_card/sync_ordinals,
# which runs this as a subprocess and returns its JSON output.
# =============================================================

import sys
import json
import logging

# Quiet by default - this prints one JSON object to stdout for the caller
# (controller.py or a human) to parse; logging noise would corrupt that.
logging.basicConfig(level=logging.ERROR)

from sheets_writer import SheetsWriter


def main():
    game = sys.argv[1].upper() if len(sys.argv) > 1 else "FH6"
    if game not in ("FH5", "FH6"):
        print(json.dumps({"status": "error", "message": f"Unknown game version: {game}"}))
        sys.exit(1)

    writer = SheetsWriter(game_version=game)
    result = writer.sync_ordinals_from_seed()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
