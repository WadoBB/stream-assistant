# =============================================================
# Stream Assistant - Unmatched Ordinal Export
# Runs on the AI COMPUTER, on demand (not part of the live pipeline).
#
# sync_ordinals_from_seed() only matches by exact learned car_name or an
# exact normalized "{Year} {MFG} {Model}" string - deliberately no fuzzy
# matching there, so a wrong car never gets an ordinal by accident. This
# script instead exports everything that exact pass couldn't place, so a
# human or an AI reasoning over abbreviations/naming differences (e.g.
# "Chevelle SS" scoreboard name vs "2019 Chevrolet Chevelle SS" seed
# full_name) can make the judgment call exact matching can't.
#
# Usage: python export_unmatched_ordinals.py [FH5|FH6]
# Also triggerable remotely via controller.py's /car_card/export_unmatched.
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
    writer._load_car_ordinals()
    writer._load_car_card_cache()

    if "Ordinal" not in writer._cars_hmap:
        print(json.dumps({"status": "error", "message": "Cars tab has no 'Ordinal' column yet"}))
        sys.exit(1)

    cars_missing_ordinal = []
    for candidates in writer._cars_by_name.values():
        for row in candidates:
            if not row.get("ordinal"):
                cars_missing_ordinal.append({
                    "row_number": row["row_number"],
                    "year":       row["year"],
                    "mfg":        row["mfg"],
                    "model":      row["model"],
                    "car_name":   row["car_name"],
                    "class":      row["class"],
                    "type":       row["type"],
                })

    used_ordinals = set(writer._cars_by_ordinal.keys())
    seed_unmatched = [
        {"ordinal": ordinal, "full_name": entry.get("full_name"), "car_name": entry.get("car_name")}
        for ordinal, entry in writer._car_ordinals.items()
        if ordinal not in used_ordinals
    ]

    print(json.dumps({
        "status": "ok",
        "cars_missing_ordinal": cars_missing_ordinal,
        "seed_unmatched": seed_unmatched
    }))


if __name__ == "__main__":
    main()
