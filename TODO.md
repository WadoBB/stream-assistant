# Stream Assistant — To Do

Items are loosely ordered by priority. Update this file as things are resolved or added.

---

## Active / In Progress

### Duplicate Entry Investigation
Duplicate rows have appeared in the Results tab a few times. Leading hypothesis: a long
in-game pause causes the telemetry stream to go silent for >3 seconds, triggering a
false race-end via packet timeout. When play resumes, a second race end fires for the
same race, producing a second identical entry.

**Next step:** Leave the next duplicate in place, pull the logs, and compare the
telemetry.log timestamps to confirm the pause theory. See `telemetry_listener.py`
`_handle_race_end` and the packet timeout path.

### Bogus Screenshots in Processed Folder
Screenshots of menus, maps, and pause screens are accumulating in
`ai-computer/captures/processed/`. These appear to be caused by the known false-capture
edge case (quitting a race early — see CLAUDE.md). Claude can't extract a scoreboard
from them so they fail and move to processed instead of being deleted.

**Next step:** Investigate whether these can be identified and discarded earlier
(e.g. Claude returns a recognizable failure signal vs. a JSON parse error), or
tighten the yellow/green banner detection to reduce false triggers in the first place.
Be careful — a previous attempt to tighten banner detection broke normal captures.

---

## Planned

### Online vs Bot Race Detection
Distinguish Open online races (against real players) from AI/drivatar races so
win rates can be tracked separately. A planned "Online" flag column in the Results tab.

**Approach:** Compare telemetry packet samples from an online session vs. a bot session.
Packet samples are saved to `ai-computer/logs/packet_samples/` — diff the `.txt` files
at every 4-byte offset to find a field that changes between modes.

### Multi-Lap Colossus / Goliath
The Colossus and Goliath are mapped as `lap_based=False` (Road Sprint) because they
are normally single-lap races. If a custom race is set up with multiple laps,
best_lap would be captured by Claude but discarded by the code.

**Next step:** Run a multi-lap custom Colossus or Goliath race and check the Results
tab. If best_lap is meaningful, change those two entries to `lap_based=True` in
`RACE_TYPE_MAP` in `results_extractor.py`.

### FH6 Telemetry Verification
Packet structure is assumed unchanged from FH5. The probe logging (packet samples +
field anomaly warnings in telemetry.log) will surface any differences.

**Next step:** After a full online session, compare an FH5 and FH6 `.txt` packet
sample side by side to confirm offsets and look for any new fields.

---

## Known Limitations (No Fix Planned)

### Short Drag Races Are Skipped
Drag races at the highest class finish in ~11-17 seconds, well under the 30-second
minimum race duration (`MIN_RACE_DURATION_SECONDS` in `telemetry_listener.py`).
Accepted for personal use — drag racing is rare. See README for fix details if
this ever needs to change.

### False Capture on Quit Race
Quitting mid-race can occasionally produce a bogus result row if pause menu UI
is mistaken for the scoreboard banner. Rare, left unresolved. See CLAUDE.md for
full details and caution around banner detection changes.
