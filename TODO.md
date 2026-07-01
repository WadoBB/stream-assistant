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

### Pause Behavior — Telemetry Dropout
Long in-game pauses cause the telemetry stream to go silent, triggering a false
race-end via the 3-second packet timeout. This is the leading cause of duplicate
entries and potentially other misfires. Need to review the timeout threshold and
whether pauses can be distinguished from a genuine race end (e.g. by checking
whether `is_race_on` returns cleanly when play resumes, or by extending the
silence window before declaring race end).

**See also:** Duplicate Entry Investigation item above.

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

### Win and Race Counts — Verify Correctness
Current Races and Wins counts in the Cars tab may be incorrect. A column order fix
may have resolved the Wins column but a full review is needed to confirm both counts
are being tallied and written correctly. Run the `update_car_stats()` function against
a known set of results and manually verify the output matches expected counts.


### Google Apps Script — Consider Moving to Python
The Apps Script runs manually or on a schedule and manages computed columns
(Win Rate, Best Time, Last Raced, Fav flag, Best by Track+Class tab). Consider
migrating this logic into Python alongside `update_car_stats()` so all sheet
updates happen automatically after every race, no manual intervention needed.
Alternatively, review and improve the existing script if full migration is too large.

### Online vs Bot Race Detection
Distinguish Open online races (against real players) from AI/drivatar races so
win rates can be tracked separately. A planned "Online" flag column in the Results tab.

**Approach:** Compare telemetry packet samples from an online session vs. a bot session.
Packet samples are saved to `ai-computer/logs/packet_samples/` — diff the `.txt` files
at every 4-byte offset to find a field that changes between modes.

### Rivals Race Capture
Rivals races do not set the `is_race_on` telemetry flag, so the system never detects
them as a race and no screenshot is triggered. Rivals times would be valuable personal
best data, particularly for track+class combinations.

**Next step:** Design a capture path that doesn't rely on `is_race_on`. Options include:
- A manual trigger (Stream Deck button) to force a scoreboard screenshot at race end
- Detecting the Rivals results screen via the capture agent using a different visual signal
- A separate Rivals mode that watches for the results screen independently of telemetry

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
