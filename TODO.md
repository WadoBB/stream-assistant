# Stream Assistant — To Do

Items are loosely ordered by priority. Update this file as things are resolved or added.

---

## Active / In Progress

### New-Record Stream Overlay — built 2026-09-11, needs live testing
Flashes a "NEW RECORD!" alert on stream when a race beats the cached Best by
Track+Class time for that (Track, Class). Not yet tested against a live race —
this is a first pass that needs a real session to confirm end-to-end.

**How it works:** `sheets_writer.py`'s `check_for_new_record()` runs first
thing in `write_race()` (before the Results/Opponents/Cars writes, so the
alert doesn't wait on those). It lazily loads a `{(track, class): seconds}`
cache from the Best by Track+Class tab (read-only — this script never writes
that tab) on first use, then compares each race's best_lap/race_time (same
lap-preferred-over-race-time logic as the Apps Script) against the cached
time. A new record updates the in-memory cache immediately (so a second
similar race in the same session doesn't double-fire) and writes
`ai-computer/overlay/state.json` (temp-then-rename, same pattern as the
capture agent's screenshot writes). Non-competitive races (Spec Race/Touge/
Time Attack — see Filtering Rules in CLAUDE.md) are skipped, consistent with
how those are excluded from the Y-flag/record system everywhere else.

`controller.py` (always-running, not the toggled pipeline) serves the overlay
at `/overlay` and the current event as JSON at `/overlay/state`. Point an OBS
Browser Source at `http://192.168.137.230:5000/overlay`. The page
(`ai-computer/overlay/index.html`) polls every 1.5s, shows a 7-second flash
animation on a new `race_id`, and ignores events older than 60 seconds so a
Browser Source reload mid-stream doesn't replay a stale record.

**Design choice made explicitly, not a limitation to revisit lightly:**
telemetry alone can't identify which track a race happened on (no track
field in the FH5/FH6 UDP packet), so the record check has to happen after the
scoreboard OCR step, same timing as everything else in the pipeline — there's
no faster telemetry-only path for a *track*-specific record. A two-tier
"instant telemetry teaser + confirmed alert" design was considered and
explicitly declined in favor of one simple, always-accurate alert.

**2026-09-11 — end-to-end plumbing confirmed working**, via the new
`GET /overlay/test` endpoint on `controller.py` (see "Cross-Machine
Development" in CLAUDE.md) triggered remotely: state.json write, `/overlay`
page polling, and the flash/fade animation all confirmed live in OBS at the
correct position, layered correctly over the game capture. This only proves
the display path, not the actual record-detection logic against real data.

**OBS gotcha hit along the way, worth remembering:** the first Browser
Source added never showed the alert (blank in both scene preview and
Interact view) despite the exact same URL working fine in a plain desktop
browser on the same machine, and despite `/overlay`/`/overlay/state`
confirmed correct server-side. This OBS/Chromium-Embedded-Framework
install's Browser Source had no "Refresh Cache of Current Page" option to
force a reload. Fix was removing the source entirely and re-adding it fresh
(new name, same URL/settings) — that one loaded correctly on the first try.
If a Browser Source ever silently shows nothing again despite the server
being verifiably fine, don't waste time on network/URL troubleshooting -
just delete and re-add the source.

**Still needed — an actual live race:** (1) does the Best by Track+Class
cache load correctly from the real sheet on startup, (2) does a genuinely
new (Track, Class) combo read sensibly as "first time on record", (3) does
the timing feel right relative to the few-second scoreboard-OCR delay when
triggered by `sheets_writer.py` for real instead of `/overlay/test`.

### Results Extractor Background Thread — Silent Death
**Defect confirmed and hardened 2026-08-21; exact trigger for the 2026-08-20
incident NOT yet confirmed from a real traceback — this is a code-review
finding, not a confirmed root cause.** `ResultsExtractor.start()` (in
`results_extractor.py`) runs in a daemon thread and only caught
`KeyboardInterrupt` — any other uncaught exception kills the thread silently
while the rest of the pipeline (telemetry listener, Flask controller) keeps
running normally, so nothing looks down. One confirmed leak: `image_to_base64()`
was called outside the try/except block in `extract_results()`, so a file-read
failure there would propagate out and kill the thread. This is consistent with
the "processes one race, then stops until manually restarted, then catches up"
symptom, but hasn't been matched against an actual traceback yet.

Hardened: wrapped the `image_to_base64()` call so a bad file fails cleanly
(moves to `processed/` like any other extraction failure) instead of raising,
and made the polling loop catch and log any other exception instead of dying.
This closes the specific leak found and adds a safety net against any other
uncaught exception, but doesn't confirm it was THE cause of the 8/20 incident.

**Next step:** Pull `ai-computer/logs/stream_assistant.log` from the AI Computer
covering 2026-08-20 ~23:28–23:35 (first race posted, then silence until the
manual restart) and look for the exception/traceback right after the first
race's "Results written" line. That will confirm or rule out this theory and
point at the real one if it's something else — e.g. a Google Sheets API auth
error, a Claude API error not going through the normal error path, or something
else entirely. Also check whether `stream_assistant.log` was even being written
to during that window (see the log-rotation note below).

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
**Partial fix 2026-08-21, still under investigation.** Both `sheets_writer.py`'s
`update_car_stats()` (real-time, after every race) and the Apps Script (nightly
batch) legitimately need to write Races/Wins — Python can't write to a car that
has no Cars row yet, and only the Apps Script's `appendNewCars_` can create that
row (keyed on the exact abbreviated Car Name from the scoreboard), so the normal
"race all night, run Update Cars once at the end" workflow depends on the Apps
Script populating stats for newly-added cars in the same run.

One confirmed bug: the Apps Script wrote Races but never Wins, and since it does
a full rebuild every run that zeroes any Cars row it fails to match, Wins could
go stale while Races got reset to 0 out from under it. Fixed by having
`forza_car_updater.gs` write Races and Wins together, always as a pair.

**Still unexplained:** a car (Audi TT RS, 2026-08-20 session) with 6 Results rows
that same night still showed 0 Races/0 Wins in the Cars tab after running Update
Cars. Leading hypothesis: those 6 rows have Notes = `Spec Race`/`Touge`/`Time
Attack` and are being *correctly* excluded per the Filtering Rules in CLAUDE.md
— never confirmed either way. Ruled out along the way: the live Cars tab header
is `Cnt, Year, MFG, Model, Car Name, D, OC, Class, Type, Fav, Notes, Tuner, Tune,
Races, Wins, ...` — Races/Wins are at the expected columns N/O, matching
`sheets_writer.py`'s hardcoded positions, so it's not a header-name/duplicate-
column mismatch between the two writers.

Added 2026-08-21: `readResults_` now attaches an `excludedByNotes` count to
what it returns, surfaced in both the "Update Cars" toast and the Analysis Log
(new "Excluded (Notes)" column — existing Analysis Log sheets won't retroactively
get the header label, only new ones), and in the "Diagnose Matching" toast. Next
time either runs, that number alone will show whether Notes exclusion is
eating a meaningful chunk of races without needing to hunt through Results
manually.

**2026-08-23 update — this got stranger, not simpler.** After deploying the
Races+Wins fix, a real run updated **Wins for every car but left the entire
Races column (N) blank for every row** — not one car, all of them. This
shouldn't be possible from the code as written: Races and Wins are computed
from the same `stats` object in the same loop iteration and written via the
same batch pattern (`applyUpdates_`) — there's no code path that writes one
and skips the other. Re-reading the code found nothing wrong by inspection,
which means the bug is very likely in how `hmap['Races']` resolves against the
*live* sheet at run time (a duplicate/stale header, a column shift, something
not visible from a code read alone) rather than in the aggregation logic
itself.

Rather than keep guessing over chat, added `resolveCol_()` — every computed
column lookup in `applyUpdates_` now re-verifies the live header cell at the
resolved index still says exactly what's expected, and throws a specific,
named error (e.g. `Column mismatch: expected "Races" at column N but found
"X"`) instead of silently writing to the wrong place. This turns whatever is
actually happening into an immediate, actionable error message on the very
next run instead of a silent mismatch nobody can see without manually
diffing cells.

**2026-08-23 — FH6 header layout re-confirmed, ruled out (again).** Walked the
full FH6 Cars tab header with the user: `Cnt, Year, MFG, Model, Car Name, D,
OC, Class, Type, Fav, Notes, Tuner, Tune, Races, Wins, [build/tune columns]` —
matches what `sheets_writer.py` assumes exactly. A theory that a missing
"Tune" column was shifting Races/Wins by one turned out to be the user's own
transcription slip, not a real layout difference. So the underlying
blank-Races/populated-Wins mystery from 2026-08-23 above is **still
unexplained** — `resolveCol_()` should catch it definitively next time
"Update Cars" actually runs with that version deployed, which hadn't been
confirmed as of this note.

**2026-08-23 — script rewrite in progress per user request**, scoped to:
keep Results/Opponents/Notes-exclusion/Best-by-Track+Class logic untouched;
simple clean Races/Wins overwrite by (Car Name, Class, Type) match (already
the design, just needs the underlying mystery resolved); auto-copy Year/MFG/
Model from an existing same-Car-Name row onto any row missing them (not just
at creation time — added a backfill pass in `applyUpdates_`, surfaced as
"Catalog Filled" in the toast and Analysis Log); fix the Analysis Log
header-shift bug (see below); trim overly alarming comment/error wording
around `resolveCol_`/`COMPUTED_COLS` that was creating confusion about what
actually has to match (column *position* never has to match anything fixed —
only the header *text* does, since lookup is always by name).

**Next step:** Re-paste the updated `forza_car_updater.gs`, run Update Cars,
and report back either the success toast (now includes a "filled from
catalog" count) or the exact error text if `resolveCol_` throws.

**2026-08-23 — concrete repro found: Chevelle '70 (Cars row 180).** Raced 7
times tonight, 2 wins, confirmed correct on Best by Track+Class. Cars tab
showed Races blank, Wins `1` — a state that's impossible to produce from any
code path in this script (a matched row always gets a real count in both
cells; unmatched always gets explicit `0` in both, never blank in one and a
stale value in the other). Conclusion: that row's Races cell has never
actually been written by any run of this script — something is excluding it
from the write, not miscalculating its value. Added **Forza → Debug One Car**
(prompts for a Car Name substring, shows every matching Cars/Results row,
its computed key, whether the Cars row falls inside the block the script
writes to, and whether each Results row matches) to get a direct answer
instead of more guessing. Not yet run against Chevelle as of this note.

**2026-08-23 — Best by Track+Class had a duplicate "Races" column with a
different meaning, likely the actual source of confusion.** User confirmed
Best by Track+Class showed the *correct* race count for the Chevelle, which
proves `aggregatePerCar_` itself is right — the two tabs just had columns
both labeled "Races" (one a per-car total, one implicitly tied to whichever
track/class record row it's on), and it was never clear which one was being
read when comparing notes. Removed Races and Win Rate from Best by
Track+Class entirely (`rebuildBestByTab_`) — those are per-car totals and now
exist in exactly one place, the Cars tab. Also pinned Win Rate to a fixed
column (P) on the Cars tab via `ensureWinRateAtColumnP_`, instead of leaving
it wherever historical auto-append happened to land it — safe to relocate
since it's fully recomputed every run, never user-entered.

**Next step:** deploy this version, run Update Cars, then run Debug One Car
on "Chevelle" and see whether row 180 now falls inside the write block and
gets a real Races value. If it still doesn't, Debug One Car's output should
show exactly why (not in `cars.rows`, key mismatch, etc.) instead of leaving
it a mystery.

**2026-08-23 — likely actual root cause found: hidden-character mismatch in
the Cars tab's own "Races" header text.** A real execution log from
`applyUpdates_` (added this session - one line per row plus a header line
showing resolved column letters) showed Win Rate/Races/Best Time/Last Raced
all resolving to four *consecutive* far-right columns (e.g. BM-BP), in the
exact order they'd be auto-appended in, while Wins correctly resolved to its
native column. That pattern only happens if `ensureComputedColumns_` couldn't
find an exact string match for "Races" among the real headers — meaning the
literal text in the visible "Races" header cell doesn't exactly equal the
string `Races` (most likely a stray invisible character - trailing space,
non-breaking space, or a lookalike Unicode character - survived a past
type/paste). The Apps Script had likely been silently reading/writing this
duplicate cluster instead of the real, visible columns for a long time.

User worked around it manually: moved the clean Races/Win Rate data into the
real N/O columns by hand and deleted the stray Best Time/Last Raced columns,
then re-ran - "it looks like it is working now." Added **Forza → Inspect Cars
Header Row** (read-only) this session to get definitive char-code proof of
the exact corruption if this resurfaces, but it wasn't run before the manual
fix landed, so the precise hidden character was never confirmed. Also
removed Best Time/Last Raced from the Cars tab entirely (v2.1) so they can't
silently get re-created by `ensureComputedColumns_` if deleted again.

**Status: believed resolved via manual cleanup, not fully root-caused in the
sheet itself.** If Races/Wins ever go blank again on the Cars tab, run
**Forza → Inspect Cars Header Row** first — it directly flags any header cell
that looks right but doesn't exactly match, before assuming a code bug.


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
Rivals races do not produce a standard scoreboard — the entire existing pipeline
(banner detection, screenshot, Claude extraction) depends on that scoreboard and
cannot be adapted with a simple trigger. A purpose-built process would be required
to read the Rivals results screen, which has a completely different layout.

**Status:** Abandoned for now. Do not attempt a quick fix — this needs a separate
purpose-built pipeline if it is ever revisited.

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
