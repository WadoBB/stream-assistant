# Stream Assistant — To Do

Items are loosely ordered by priority. Update this file as things are resolved or added.

---

## Active / In Progress

### Controller Restart Orphans the Pipeline — fixed 2026-09-12
`controller.py` tracked the running pipeline in `pipeline_process`, a plain
in-memory variable. Every time `controller.py` itself restarted (which
happened three times in one night while testing new routes), that variable
reset to `None` - even though a previously-started `main.py` kept running
untouched on the OS. `is_running()` then permanently reported "stopped" for
a pipeline that was actually still alive.

**How this actually broke things:** the Gaming PC's `toggle_fh6.bat` checks
`/status` to decide whether to take the start or stop branch. With
`controller.py`'s tracking desynced from reality, a press intended to *stop*
a running pipeline instead saw "stopped" and took the *start* branch -
spinning up a second `main.py`, which immediately crashed trying to bind
UDP port 9999 (already held by the orphaned first one). From the user's
side this looked like "the toggle stops it and immediately starts it again"
plus "the process fails to start on the AI Computer" - two symptoms, one
root cause. Confirmed via `stream_assistant.log`: two consecutive
`"Starting Telemetry Listener..."` lines with no `"Telemetry listener
started on port 9999"` after either - the crash is confined to
`socket.bind()`, three lines of code nothing tonight had touched - and via
Task Manager showing two live `python.exe` processes.

**Fix:** `is_running()` now falls back to asking Windows directly
(`Get-CimInstance Win32_Process` filtered to a command line containing
`main.py`) whenever its own in-memory handle doesn't know of a running
process, so a `controller.py` restart can no longer permanently orphan the
pipeline. `/toggle`'s stop path now `taskkill`s an orphan found this way
(no `Popen` handle exists to call `.terminate()` on). `/status` was fixed to
not crash calling `.pid` on a `None` handle when a running pipeline was
found via this fallback rather than the in-memory one.

**Recovery from an already-orphaned state:** find the extra `python.exe`
via `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select
ProcessId, CommandLine` (Task Manager alone doesn't show command-line args),
end the one running `main.py` specifically - not `controller.py` - and
restart the toggle from a clean state.

### New-Record Stream Overlay — built and verified live 2026-09-11/12
Flashes a "NEW RECORD!" alert (with a cheer sound) on stream when a race
beats the cached Best by Track+Class time for that (Track, Class). Confirmed
working end-to-end against real races, including the flag/graphic/sound all
firing correctly together.

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

**2026-09-12 — confirmed against real races.** First test race produced no
alert — the (Track, Class) had only ever been raced in an `M`-flagged car, so
Best by Track+Class had no row for it, exactly the edge case predicted above
(read as "already excluded," not "first time on record," because the cache
had no way to distinguish the two). Removing the `M` flag and re-racing fired
the alert correctly with both the visual and the cheer sound. Cache-load
timing, the "first time on record" wording, and overall timing relative to
the scoreboard-OCR delay all confirmed working as designed. No further
action needed unless the `M`-flag edge case above becomes a real annoyance
in practice — it hasn't been asked for as a fix, just noted as expected
behavior.

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

### Screenshot Read Fails with Permission Denied — new 2026-09-16
Found while investigating a missing-result report (same session as the photo/promo
false-capture note above). `stream_assistant.log`:
```
[ERROR] Failed to read screenshot C:\StreamAssistant\ai-computer\captures\scoreboard_2026-0916-192433.png: [Errno 13] Permission denied: '...'
[WARNING] Extraction failed - moved for review: scoreboard_2026-0916-192433.png
```
This is **not** a false capture — opened the saved image in
`ai-computer/captures/processed/scoreboard_2026-0916-192433.png` and it's a
completely valid, fully-legible scoreboard (Quick Lap Test, Eclipse GSX, position
3, 01:09.686). The result was lost purely because the read failed, moved to
`processed/` alongside the genuine false-captures, and never retried.

**Leading hypothesis:** a Windows file-locking race — something (SMB finishing the
gaming PC's rename-into-place, antivirus scanning the new file, or similar) still
had the file open/locked at the exact moment `results_extractor.py` tried to read
it. The temp-then-rename pattern (see "Race Condition Fix" in CLAUDE.md) prevents
reading a *partially written* file but doesn't guarantee nothing else holds a
transient lock on it immediately after the rename completes.

**Not yet fixed.** Possible directions: retry the read once or twice with a short
backoff before giving up, and/or log a distinct, obviously-different message for
this case vs. a genuine false capture so the two don't look identical in
`processed/` (right now both end up as an unlabeled PNG with no record of *why*
it failed, unless the log is checked at the time). Because the file itself is
intact and unmodified, a race lost this way is also manually recoverable after
the fact — the image just needs to be re-run through extraction by hand.

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

**2026-09-16 — new false-capture variant confirmed, not the pause-menu case
described above.** Investigating a user report of a missing Nissan Z NISMO
result on FH6, `stream_assistant.log` (pulled remotely via `/logs`, see
"Cross-Machine Development" in CLAUDE.md) showed two extractions in the same
~15-minute window failing with `Claude extraction successful` never
appearing — Claude correctly told them apart from a real scoreboard
(`"This image does not show a race results scoreboard... Forza Horizon
Festival photo/promo challenge progress screen"`), not a JSON-parse crash on
a plausible-looking response. Opened both saved images in
`ai-computer/captures/processed/` (`scoreboard_2026-0916-191851.png`,
`scoreboard_2026-0916-193201.png`) and confirmed: both are the **post-race
"Discover Japan" / "Horizon Festival" photo-mode reward screen**, not a pause
menu. Both real races these belonged to finished and were correctly logged
by telemetry (`RACE COMPLETE`) — the scoreboard banner detector just never
found the actual scoreboard before the game had already advanced to this
photo/promo screen, so `capture_agent.py` grabbed whatever was on screen at
detection time instead. In one case (`193201`) the gaming-PC log shows the
scoreboard wasn't detected until 13 seconds after the race-end trigger, well
above this session's usual ~1-2s — consistent with the player already having
navigated past the scoreboard by the time detection caught up. Both races
were on the very short "Quick Lap Test" track, which may give less on-screen
time at the scoreboard before auto-advancing than a normal multiplayer race.
No fix attempted yet — same caution about not touching banner detection
blind applies. A possible angle that doesn't touch detection at all: treat
Claude's plain-English "this is not a scoreboard" response as an expected,
loggable outcome distinct from a real extraction error, so these don't read
as crashes.

**Root cause confirmed by the user, same day:** it's not a detection-timing
bug — the user stops and takes photos during/right after races, which is
exactly what lands the Discover Japan/Horizon Festival photo screen on
screen when the scoreboard detector fires. **It is not safe to pull over and
take photos mid-race**, so the user is going to stop doing this — expect
this specific false-capture variant to become rare going forward without any
code change. Leave banner detection alone; this doesn't need the fix ideas
above.

**Follow-up same day — the user's own risk model was backwards.** They'd
long assumed dismissing the scoreboard too *fast* (hitting continue the
instant the race ends) was the danger and hesitated because of it. Capture
is actually fast enough that this has never caused a miss. The real risk
identified above generalizes beyond photos specifically: **any pause after
the race ends — photos, the pause menu, or rewinding — before dismissing the
scoreboard** is what risks a false capture, not dismissing it quickly. No
code implication; noting this so the user's own habit (dismiss promptly,
avoid lingering) is understood as the actual mitigation, not a risk.

---

## Planned

### Overlay Sounds Need Their Own OBS Mixer Channel — flagged 2026-09-13
`cheer.mp3` (record alert) and `rev.mp3` (car card) currently play through
whichever OBS audio track/mixer channel their Browser Sources default to -
apparently Mic/Aux, since the user reports they come through far louder
than intended, mixed in with mic volume rather than independently
controllable.

The user's existing StreamElements overlay sounds don't have this problem -
they're routed to a separate, dedicated mixer channel named "SE Clean Race
Overlay" with its own independently-lowered volume, via a Browser Source
pointed at:
`https://streamelements.com/overlay/6a08a62516fc5074b2affac6/8_mW-X-aHaBHO__qPEy_ScBVB8MIvJ-bZUiQ3ft7aq_zCKm-`

**Likely mostly an OBS-configuration task, not a code change:** OBS assigns
each audio-producing source (including a Browser Source) to one of its 6
audio tracks via that source's "Advanced Audio Properties" (right-click the
source, or the gear icon in the Audio Mixer panel) - this determines which
mixer strip it shows up under and lets its volume be controlled
independently of Mic/Aux. First thing to try: check what track/mixer
assignment the StreamElements Browser Source uses vs. what the Record
Alert and Car Card Browser Sources use, and either match them to the same
channel or assign them a new dedicated one - entirely in OBS's UI, no
changes to `index.html`/`car_card.html`/`controller.py` needed unless that
turns out not to be sufficient (e.g. if something about how the `<audio>`
tag plays inside a Browser Source bypasses per-source track assignment,
which would need investigating live in OBS rather than guessed at here).

### Car Suggester — scoped 2026-09-12, needs a dedicated session
Suggests which car(s) to pick, before you commit, for whatever track/class
is coming up next — a companion to the record-alert overlay but for the
start of a race instead of the end. Not started; this is a scoping note to
make the eventual build session fast, not a build in progress.

**The core problem, worked out in a brainstorm 2026-09-12:** unlike the
record alert (fine to fire a few seconds after the race ends), a suggestion
only has value if it lands *before* car select. Telemetry can't help here at
all — Forza's UDP stream only starts once `is_race_on = 1`, which is already
past the point of no return. So this needs the same category of work as the
original scoreboard reader: detecting and OCR'ing a *different* screen (the
race notification/lobby/car-select screen) to pull the track name and class
restriction, using a new banner/color signature the way `capture_agent.py`
does for the scoreboard today (see FH5/FH6 Scoreboard Detection in
CLAUDE.md for how much tuning that took the first time - pixel analysis,
region/color thresholds, false-positive handling against pause menus, etc.).
Expect this half of the work to be the hard part.

**The easy half, once track/class is known:** looking up "top car(s) for
this (Track, Class)" is mostly a Sheets read - the Best by Track+Class tab
already tracks exactly this (the current record holder per Track+Class is
arguably already *the* suggestion), and/or the Cars tab's Win Rate/Races
columns can rank alternatives. No new aggregation logic needed, just a
query against data that already exists.

**Considered during the brainstorm and split off into its own project
(see "Car Card Overlay" below), not a substitute for this one:** triggering
off `car_ordinal` changes in telemetry to react to a car switch - simpler
technically (pure telemetry, no new screen detection) but reactive ("here's
how this car has done") rather than predictive ("pick this car"). The two
are genuinely separate features with separate triggers and separate
purposes; they only happen to share one piece of infrastructure, the
car-ordinal database being built for the Car Card project.

**Next step (not code):** watch the actual race notification/car-select
screen closely during normal play - what it looks like, when it appears,
how long it's on screen, what colors/text are present, whether pause menus
or other screens could be confused for it (the scoreboard detector already
has a known false-positive mode here, see "Known Issue — False Capture on
Quit Race" in CLAUDE.md - worth having that in mind while observing). That
observation is the actual prerequisite for scoping the detection work; there's
nothing to build correctly without it.

### Car Card Overlay — built 2026-09-12, needs the sheet columns added + live testing
Shows a graphic (car image, tuner, painter, Races/Wins/Win %) whenever the
selected car changes — triggered by `car_ordinal` changing in telemetry, in
free roam or otherwise, not tied to a race starting or ending. Same overlay
plumbing as the record alert (state file, `controller.py` serving it,
`ai-computer/overlay` page polling and animating). Unlike Car Suggester,
this needs **no new screen detection** — pure telemetry plus Sheets lookups
— so it doesn't share that project's blocker.

**Built, not yet live-tested:**
`telemetry_listener.py` now tracks `car_ordinal` on every packet (not just at
race start) and fires `on_car_change(ordinal, class, pi, drivetrain)` on any
change, independent of race state - a persistent `_last_known_ordinal` that
survives `_reset_race_state()` so finishing a race doesn't look like a car
change. `main.py` wires this to `SheetsWriter.update_car_card()` on a
background thread (never blocking the telemetry socket loop with a Sheets
API round-trip). `sheets_writer.py` gained: a Cars-tab cache keyed both by
Ordinal and by normalized Car Name (all columns found by header text),
`update_car_card()` (resolve ordinal -> identity + stats -> write
`car_card_state.json`), and `learn_car_ordinal()` (called from `write_race()`,
wrapped so a failure there can't block the actual Results write - same
defensive pattern as `check_for_new_record()`). `controller.py` serves
`/car_card`, `/car_card/state`, `/car_card/image/<ordinal>` (falls back to
`default_shadow.svg`), and `/car_card/test` for manual triggering the same
way `/overlay/test` works for the record alert. `ai-computer/data/
car_ordinals.json` seeded with all 671 entries from the Gist, visually
verified in-browser for both the "known car" and "never raced yet" card
states.

**One manual step before this can be tested live:** the user needs to add
**Painter** and **Ordinal** as new columns on the real Cars tab - nothing in
this codebase can do that. Everything reads those columns by header name and
degrades gracefully (blank fields, not a crash) if they don't exist yet, but
the feature has nothing to show without them.

**A neat side-discovery while scoping this:** Forza's "long descriptive"
full car name (e.g. "1969 Toyota 2000 GT" — Year + MFG + Model) is the exact
same text CLAUDE.md's "Known Issue — False Capture on Quit Race" describes
showing up when a bad scoreboard capture grabs the wrong screen. Same
underlying game text, two very different contexts.

**Why this needs new car-identity infrastructure:** `car_ordinal` is
Forza's internal numeric ID for the car model - nothing in the pipeline
today maps it to a car name; that only ever happens via Claude's OCR of the
post-race scoreboard. Design worked out with the user 2026-09-12:

- **Seed file** `ai-computer/data/car_ordinals.json`, one entry per ordinal,
  seeded once (not a live dependency) from a community FH6 ordinal list
  (671 entries, gist.github.com/HDR/0659d1717bc61504bf83750628963f4f,
  format inverted from name→ordinal to ordinal→name on import). Each entry:
  `{"full_name": "...", "car_name": null}`. `full_name` is the Gist's
  Year+MFG+Model string, used as a friendly display fallback for a car with
  no Cars-tab row yet. `car_name` is the abbreviated scoreboard string used
  everywhere else in this system to key a Cars-tab row - starts `null` for
  all 671 seeded entries and gets filled in automatically the first time
  that car is actually raced (the one moment telemetry's ordinal and
  Claude's OCR'd name are both known together).
- **Cars tab gets exactly one new column: Ordinal.** No separate "Full Name"
  column needed there - for any car that already has a row, Year + MFG +
  Model (already three separate existing columns) concatenated *is* the
  full name, so the seed file's `full_name` field is redundant once a Cars
  tab row exists and only matters before one does.
- **Backfilling Ordinal on already-existing Cars tab rows happens lazily,
  not as a one-time reconciliation project** - the user confirmed the
  abbreviated "Car Name" field is too inconsistent (sometimes includes MFG,
  sometimes an abbreviation, sometimes neither) to reliably auto-match
  against the Gist's full names in bulk. Instead: next time a car is raced,
  telemetry's ordinal + the matched Cars-tab row backfills Ordinal there if
  missing - same self-healing pattern already used for Year/MFG/Model in
  `applyUpdates_`.
- **New Cars tab column: Painter** (manual credit field, like Tuner already
  is - not computed by any script).
- **Car images**, one file per ordinal (`ai-computer/overlay/car_images/
  <ordinal>.png`), with a single `default_shadow.png` served whenever a
  specific ordinal's image doesn't exist yet. Images themselves need to come
  from the user - no legitimate source to pull them from otherwise.
- All new Sheets lookups for this feature read columns **by header name**,
  never hardcoded position - the whole Races/Wins saga earlier this project
  was caused by hardcoded positions, and this is a chance to not repeat it.

**Open wrinkle, not urgent:** ordinal identifies the car model, not the tune
- a car with both a Road and a Dirt build at the Cars tab is two different
rows. Telemetry's live PI resolves Class the same way race results already
do, but if both tunes land in the same Class there's no signal to pick
between them. Fine to just pick a tiebreak default (e.g. Road) rather than
solve this properly.

**FH6-only for now** - the Gist is FH6 specific; FH5 would need its own
separate ordinal source if this is ever extended there.

**2026-09-12 additions, after the two Cars-tab columns were added:**
- **Engine-rev sound** on car change, same pattern as the record alert's
  cheer sound - `overlay/rev.mp3` served at `/car_card/rev.mp3`, played via
  `.play().catch(() => {})` in `car_card.html`. Needs the actual audio file
  from the user, same as `cheer.mp3` before.
- **Bulk Ordinal sync**, `sheets_writer.py`'s `sync_ordinals_from_seed()` +
  standalone `sync_ordinals.py` (triggerable locally or remotely via
  `controller.py`'s `/car_card/sync_ordinals?game=FH5|FH6`, run as a
  subprocess so controller.py itself stays free of the Google API
  dependency). Complements the per-race lazy backfill in
  `learn_car_ordinal()`: catches every Cars-tab row up to what's already
  been learned in one pass, rather than needing each car re-raced
  individually.
  - **2026-09-12 update:** originally only matched by learned `car_name`
    (scoreboard's abbreviated name), which meant every one of the 671 seeded
    entries reported 0 backfilled until that specific car had actually been
    raced - going to take weeks, and manually cross-referencing the
    ordinal-ordered seed file against the spreadsheet by hand (to type
    Ordinal in directly) was slow, since scanning by ordinal number doesn't
    match how you'd look a car up by name. Added a second matching path,
    `_build_identity_index()`: builds "{Year} {MFG} {Model}" from the Cars
    tab's own columns and compares the whole normalized string against the
    seed's `full_name` directly, deliberately without trying to split
    `full_name` itself (manufacturer names are often multi-word - Alfa
    Romeo, Aston Martin, Land Rover... - so that split is ambiguous without
    a hardcoded MFG list). This matches any car you've already catalogued
    with Year/MFG/Model filled in, even if never raced, at the cost of only
    matching when the wording lines up exactly (no fuzzy matching - a
    mismatch just falls through as unmatched rather than risking the wrong
    car getting an ordinal). Each backfilled row's summary now includes
    `matched_by: "car_name"` or `"identity"` so it's clear which path found
    it. Also fixed two wrong seed entries found this way: ordinal 249 was
    listed as "1964 Ferrari 250 GTO" (should be 1962), ordinal 255 as "1991
    Ferrari 512 TR" (should be 1992).
  - **Important distinction, caused real confusion once:** this identity
    match only ever writes the Cars tab's Ordinal column. It does **not**
    touch `car_ordinals.json`'s `car_name` field - that field is null for
    every unraced car and can only be filled in by `learn_car_ordinal()`
    when a car is actually raced (it needs the scoreboard's OCR'd name,
    which this sync has no access to). Seeing `car_name: null` in the JSON
    after running the sync is expected, not a sign the sync failed.
    - **2026-09-13 fix, this gap closed properly:** found via ordinal 1009
      (2008 Mitsubishi Lancer Evolution X GSR) - fully resolved on the
      Sheet (Ordinal + Car Name both set, matched by the very first
      identity-match run) but still `car_name: null` in the JSON, since
      that run predates this file even being touched for `car_name` at
      all. Added `sync_car_names_from_sheet()` (also standalone
      `sync_car_names_from_sheet.py`, also `GET
      /car_card/sync_car_names?game=FH5|FH6`) - the reverse direction of
      `sync_ordinals_from_seed()`: for every Cars-tab row that already has
      an Ordinal, backfills `car_ordinals.json`'s `car_name` for that
      ordinal if still null. Read-only against the Sheet, only ever writes
      the local JSON. Run once after the identity-match sync (or anytime
      the two are suspected to have drifted) to close this gap in bulk.
  - **2026-09-12, later - AI-matching pass for what exact matching can't
    place.** `sync_ordinals_from_seed()` deliberately never fuzzy-matches
    (a wrong car getting an ordinal is worse than a missed one), which
    leaves real naming differences unmatched - e.g. a scoreboard's
    abbreviated "Chevelle SS" vs. the seed's "2019 Chevrolet Chevelle SS" -
    that a human or an AI can resolve by reasoning about it but a strict
    string comparison can't. Added a read/reason/write-back pipeline for
    that gap, at the user's request ("this is exactly the kind of thing I
    would want to use AI for") rather than having them eyeball 400+ entries
    manually against the ordinal-ordered seed list:
    - `export_unmatched_ordinals.py` (also `GET /car_card/export_unmatched
      ?game=FH5|FH6`) - dumps every Cars-tab row still missing Ordinal
      alongside every seed entry not yet claimed by any row. Read-only,
      makes no matching decision itself.
    - `apply_ordinal_matches.py` (also `POST /car_card/apply_ordinal_matches`,
      body `{"game": ..., "matches": [{"row_number", "ordinal", "car_name"}]}`)
      - writes back a judged match list: Ordinal on the Cars tab row, and
      car_name on the matching car_ordinals.json entry (only if not already
      set) - the same two writes `learn_car_ordinal()` makes for a real
      race, just arriving via reasoning instead of racing. Re-checks the
      row/ordinal are still unset at write time, so a stale match list from
      an earlier export can't clobber anything.
      - **2026-09-13 fix:** the Sheet write and the JSON write shared one
        "already done" check, keyed off the Sheet's Ordinal being set. Found
        the hard way: after applying 216 matches, an unrelated git incident
        on the AI Computer (`git merge --abort` reverting to a stale commit,
        then `git reset --hard origin/main` to recover - see below) wiped
        the uncommitted `car_ordinals.json` changes, but the Sheet writes
        survived untouched (Sheets aren't git-tracked). Re-running the same
        match list to repair just the JSON side skipped every single row,
        because the Sheet already had Ordinal set - the shared skip
        condition never let execution reach the JSON backfill at all. Also,
        `_save_car_ordinals()` was only called inside the "Sheet needed
        writing" branch, so even a successful in-memory JSON backfill with
        nothing left to write to the Sheet would never actually persist to
        disk. Fixed by giving the two writes independent skip conditions
        and gating the save on whether the JSON was actually backfilled,
        not on whether the Sheet was.
    - **2026-09-13 - unrelated git incident during the same session, while
      trying to commit `car_ordinals.json` from the AI Computer:** its local
      `main` was still sitting on 2 unpushed commits from well before this
      project's current two-machine workflow existed
      (track-name-capture and scoreboard-time-preference fixes - both
      already reflected in current `main`'s behavior per this doc), miles
      behind origin's 55 newer commits. An earlier, unattended `git pull`
      hit a conflict in `gaming-pc/capture_agent.py` and was left
      unresolved, which blocked every subsequent git command
      ("could not write index") until diagnosed as a stuck merge and
      cleared with `git merge --abort` - which itself reset the working
      tree all the way back to that old pre-merge commit, wiping
      `car_ordinals.json` (a file that didn't exist yet at that old commit)
      along with the uncommitted 216-match backfill. Recovered cleanly with
      `git fetch origin && git reset --hard origin/main` (safe here since
      nothing on GitHub was ever at risk - only the AI Computer's local,
      never-pushed state), then re-ran the same match list through the
      now-fixed `apply_ordinal_matches.py` to redo just the JSON side (the
      Sheet was never affected, since it isn't git-tracked at all).
      **Takeaway for next time a machine's `main` is this far behind:**
      check `git log --oneline -5` for stray local-only commits *before*
      attempting a plain `git pull` on a heavily diverged branch - a clean
      `git fetch && git reset --hard origin/main` (after confirming any
      local-only commits are already reflected upstream) avoids the merge
      entirely when there's no real local work worth preserving as distinct
      commits.
- **Card position still needs live tuning** - current CSS puts it at
  `top: 6%; left: 3%` in `car_card.html`, picked without having seen it in
  OBS yet. Iterate the same way the record alert's position was confirmed:
  fire `/car_card/test`, look at the OBS preview, adjust the CSS, repeat.
- **`/car_card/test?ordinal=N` now pulls real data instead of always
  showing a canned Chevelle SS** - found while testing ordinals 2793/3670
  (both unraced): the test endpoint ignored `car_ordinals.json` entirely, so
  it showed fake stats on the correct image, misleading about what the
  overlay would actually show for that car. `test_car_card.py` (new,
  mirrors `sync_ordinals.py`'s pattern) runs `update_car_card()` - the same
  function the live pipeline calls on a car change - as a subprocess, and
  `/car_card/test?ordinal=N` uses it whenever no other override param is
  given, correctly showing "not yet raced" for an unmatched car instead of
  fake stats. Passing any of the old override params (`full_name`,
  `car_name`, etc.) still uses the original fully-canned path, for testing
  card layout with arbitrary data; bare `/car_card/test` (no params) also
  stays canned, for a quick connectivity/animation smoke test.

**2026-09-12 debugging note - a false alarm worth remembering, not a real
bug:** repeated `/car_card/test` and `/overlay/test` calls with default
params appeared to "stop working," only recovering after refreshing the
Browser Source/removing and re-adding it. Root cause: both pages
deliberately suppress re-showing the *same* event twice in a row (only fire
when `ordinal`/`race_id` changes from the last one shown - correct,
intentional behavior, otherwise the card would flash every 1.5s poll while
sitting in the same car). Every test call was reusing the same default
`ordinal: "999999"`, so the page correctly treated repeated test-fires as
"nothing new." A refresh resets that "last seen" memory, which is why it
looked temporarily fixed each time. Confirmed with two back-to-back
`/car_card/test?ordinal=...` calls using *different* ordinals, no refresh in
between - both displayed correctly. **Not a dependability concern for real
use** - every genuine car change or race produces a distinct
ordinal/race_id, so this never comes up in actual gameplay. If this pattern
resurfaces during future testing, vary the test parameters before assuming
something is broken.

**Correction, later the same night - there WAS also a real, separate
reliability problem, now fixed.** After the false-alarm above was resolved,
both overlays kept intermittently going quiet again even with varied test
ordinals/race_ids - working right after a Browser Source was freshly added,
then silently no longer reacting, recoverable only by removing and
re-adding the source, repeatedly, unpredictably. See "Overlay Delivery
Switched to Server-Sent Events" below for the actual fix - the client-side
polling loop was very likely being throttled/stalled by the browser/OBS for
a source it considered backgrounded, which is a different failure mode than
the dedup false-alarm above and wasn't fully explained by it.

### Overlay Delivery Switched to Server-Sent Events — fixed 2026-09-12
Both the record alert and Car Card overlays originally worked by having the
page repeatedly `fetch()` their state endpoint on a 1.5s `setTimeout` loop.
This proved unreliable over a real OBS session on both overlays: each would
work correctly right after its Browser Source was freshly added, then go
quiet at some point after, only recoverable by removing and re-adding the
source - a refresh alone wasn't always enough. Nothing in the polling loop's
own code explained this (the `setTimeout` reschedule ran unconditionally,
outside any try/catch), which points at the browser/OBS itself throttling or
stalling a JS timer it considers backgrounded - a known category of browser
behavior, not something fixable from inside the loop.

**Fix:** moved the "wait for new data" responsibility server-side.
`controller.py`'s `_sse_stream()` is a plain Python generator that watches a
state file's modification time once a second and pushes its contents over a
persistent Server-Sent Events connection the moment it changes - a
server-side loop is never subject to browser tab-visibility throttling.
`/overlay/stream` and `/car_card/stream` serve this; both pages now use
`EventSource` instead of a polling loop, which also gets automatic
reconnection on a dropped connection (e.g. a `controller.py` restart) for
free from the browser, rather than needing a manual retry loop. The old
`/overlay/state` and `/car_card/state` JSON endpoints are kept as-is for
manual/curl verification (e.g. after `/overlay/test`) - only the pages
themselves changed how they receive updates.

**Required alongside this:** `app.run(..., threaded=True)` in
`controller.py` - Werkzeug's dev server handles one request at a time by
default, and a single open SSE connection would otherwise block `/toggle`,
`/status`, and every other route for as long as a Browser Source stays
connected (which is indefinitely).

**2026-09-12, same night - the underlying push mechanism was confirmed
correct, but a real gap surfaced anyway.** Both overlays went quiet again on
both a plain browser tab and OBS, "fixed" only by a manual page refresh. A
direct raw connection to `/overlay/stream` (opened, then `/overlay/test`
fired while it was already connected) proved the server-side push itself
works correctly - the new event arrived live, no refresh involved. So the
gap wasn't the data delivery; it was that **a long-lived `EventSource`
connection can end up stuck without the browser's own reconnection logic
reliably noticing and recovering** - a refresh always "worked" because a
fresh connection immediately receives whatever's currently in the state
file, which masked the real problem rather than proving the live-push path
was healthy.

**Second fix, same session:** heartbeats were originally sent as bare SSE
comments (`: heartbeat`), deliberately invisible to `EventSource.onmessage`
by design - which also meant the page had no way to distinguish "connection
fine, just quiet" from "connection silently dead." Heartbeats are now a
named `ping` event the page actually listens for
(`addEventListener('ping', ...)`), and both pages track the timestamp of
the last real event *or* ping; a client-side watchdog (`setInterval`, every
3s, checked against an 8s threshold) force-closes and reopens the
`EventSource` if too long passes with neither. This is a second timer, but
its job is only "notice a stall and reconnect," not "be the sole delivery
mechanism" the way the original polling loop was - even if browser
throttling delays the watchdog itself, it still eventually recovers the
connection rather than never doing so until a human intervenes.

**Still not confirmed against a real, long OBS session** - this needs
observation over an actual multi-hour stream to know whether the watchdog
actually eliminates manual-refresh dependency or just makes recovery
faster/quieter. Both overlays fired correctly immediately after this was
deployed and the Browser Sources re-added (2026-09-12) - worth noting this
was already the expected baseline even before the watchdog existed (fresh
connections always worked; only long-lived ones went stale), so this alone
doesn't yet confirm the actual fix. Real confirmation is a normal session
where the sources are added once and never touched again for hours.

**2026-09-12, later same night - root cause of the recurrence found, this
time with hard evidence, not another guess.** After a controller.py
restart, a real Car Card page still needed a manual refresh despite the
watchdog. Reproduced independently (not on the user's OBS/browser at all)
by loading `/car_card` in a separate browser and watching its network log
across a controller.py restart: the `/car_card/stream` connection cycled
through `ERR_ABORTED` alternating with `ERR_CONNECTION_TIMED_OUT` for a
long stretch before finally reaching `200 OK` on its own. That alternation
was the tell - **two reconnect mechanisms were fighting each other.**
`EventSource` already retries on a dropped connection natively, with no
code needed; right after a server restart, a retry attempt can legitimately
take a while to succeed (Windows doesn't always release/rebind the port
instantly). But the watchdog (`setInterval`, checked every 3s) was calling
`connect()` unconditionally whenever quiet for 8+ seconds - `connect()`
calls `source.close()` on whatever's currently there, so it was aborting
the browser's own in-progress native retry attempt and starting a brand
new one from scratch, potentially repeatedly, each time cancelling a
connection that might have been about to succeed. The watchdog built to
fix the reliability problem was itself extending it.

**Fix:** the watchdog now checks `source.readyState` before reconnecting -
if it's already `CONNECTING` (a native retry in flight), leave it alone;
only force a fresh `EventSource` if it's genuinely stuck `OPEN` (silently
not receiving pings, the original stuck-connection case this watchdog was
built for) or `CLOSED`. Applied identically to both `index.html` and
`car_card.html`.

**Confirmed live the same night, immediately after deploying the fix
above:** watched `/car_card`'s network log through an actual controller.py
restart. The old connection got `ERR_CONNECTION_RESET` (expected - the
process died), then exactly **one** new `/car_card/stream` request
succeeded immediately - no `ERR_ABORTED`/`ERR_CONNECTION_TIMED_OUT`
alternation, `readyState` back to `OPEN` and receiving pings within ~200ms.
The abort-loop is gone.

**A second, unrelated bug turned up during that same test:** firing
`/car_card/test` right after the clean reconnect updated `lastOrdinal`
(event received) but the card never visually appeared. Cause:
`datetime.now().isoformat()` (used for every overlay/car-card event's
`timestamp`, in both `controller.py` and `sheets_writer.py`) produces a
**naive** local-time string with no UTC offset. Both pages' `new
Date(event.timestamp)` parse that ambiguously as the *viewing browser's*
local timezone, not the server's - so `ageMs` in the `MAX_AGE_MS` freshness
check can be wrong by however many hours the two clocks' timezones differ,
silently treating a brand-new event as stale (`lastOrdinal` still updates,
since that check has no age gate, but `showCard()`/`showAlert()` never
fires). Didn't show up earlier because the Gaming PC and AI Computer are
presumably on the same timezone; only surfaced testing from a browser
sandboxed in a different one. Fixed by switching all four call sites to
`datetime.now(timezone.utc).isoformat()`, so parsing is unambiguous
regardless of either machine's local clock/timezone setting - closes a
latent gap (DST edge cases, future clock drift) even though it likely
wasn't the cause of any manual-refresh incident reported so far.

**This is genuinely buildable soon, unlike Car Suggester** - no blocked R&D,
just a few Sheets/config additions and threading `car_ordinal` through
`telemetry_listener.py` to fire on any change, not just at race start.

**2026-09-18 - recurred live, worse pattern than anything above, still not
fixed.** During an actual multi-hour stream, the Car Card fired
automatically on the very first car change of the session, then required a
manual OBS Browser Source refresh for every car change after that -
refresh always then showed the correct car (confirmed by the user
directly - important, since it rules out the update/write path as the
cause: `update_car_card()` logged zero errors all night and its last write
matched the last car selected exactly). This also ruled out a theory raised
mid-investigation that repeated manual refreshes might exhaust the
browser's per-origin connection limit (Chromium/CEF caps at 6 concurrent
connections per host on HTTP/1.1) - a live test reloading `/car_card`
seven times in a row against the real controller.py showed every reload
getting the correct current state instantly, no queuing. That's not what
happened live: the failure showed up on the very first automatic push
after the very first success, before any refresh had occurred, so it
points back at the already-open `EventSource` going stuck and the
2026-09-12 watchdog fix (readyState check, see above) not actually
catching/recovering it in the real OBS environment - only confirmed
working in "a separate browser" per that entry's own caveat, never in a
real long OBS session until now.

**Not yet fixed - diagnostic added instead of guessing at another fix.**
`_sse_stream()` in `controller.py` only ever logged pings and errors; a
successful push was invisible in the log, so there was no way to tell
after the fact whether the server actually pushed the update and the
client failed to render it, or the generator itself silently stalled and
never pushed at all. Added a `log.info(...)` for every real (non-ping)
push, naming the state file and the pushed data. Next time this happens,
check the controller log against `car_card_state.json`'s timestamp for the
car that didn't show: if the push was logged, the bug is client-side
(stuck connection/watchdog); if it wasn't, the generator itself is the
problem. Deployed in commit `7ff88c6` - needs a `controller.py` restart on
the AI Computer to take effect, not yet confirmed against a real stream.

**Also added, same commit:** `/monitor` (both overlays stacked via
iframes, `ai-computer/overlay/monitor.html`) and
`gaming-pc/open_stream_monitor.bat` to open it as a small positioned
window on a secondary display (built for a Corsair Xeneon Edge run in
portrait) - lets the user watch both overlays live while streaming instead
of only finding out they didn't fire after the fact. The window
position/size in the `.bat` file are placeholder values (assumes the
Xeneon Edge sits right of a 1920x1080 primary at Y=0) - not yet confirmed
against the user's actual monitor layout.

### Channel Command to Trigger Car Card — idea 2026-09-15
Let a viewer chat command (or channel-point redemption) re-fire the Car Card
overlay for whichever car is currently selected — a viewer-interaction hook
on top of the Car Card feature above, not a new data path. The hard part
(telemetry → ordinal → Sheets lookup → render) already exists; this only
needs a trigger source and a way to fire it.

**User is running Streamerbot already — wire in through that rather than
building a standalone Twitch chat bot.** Streamerbot already handles the
Twitch/chat-command/channel-point-redemption side (including auth, cooldowns,
permission levels) and can call an arbitrary HTTP endpoint as an action, so
the only new code needed here is on the `controller.py` side:

- A new endpoint, e.g. `GET /car_card/trigger` — unlike `/car_card/test`
  (which can inject arbitrary fake data for layout testing), this should
  fire the overlay using whatever car is *actually* currently selected
  (the same ordinal `telemetry_listener.py` already tracks as
  `_last_known_ordinal`), not a param-supplied one — the whole point is
  "show me my current car again," not "show me an arbitrary car."
- A short server-side cooldown on this endpoint specifically, independent of
  whatever cooldown Streamerbot's own command config applies — a chat
  command reaching the server at all (even once past Streamerbot's cooldown)
  shouldn't be able to spam-refire the on-stream flash animation back to
  back.
- On the Streamerbot side: a Command action (or Twitch channel-point
  redemption action) with an HTTP Request sub-action pointed at
  `http://192.168.137.230:5000/car_card/trigger` — no tunnel/port-forwarding
  needed since Streamerbot presumably already runs on a machine with LAN
  access to the AI Computer, same as the Stream Deck's existing `/toggle`
  calls.

**Not yet scoped:** whether to gate this to mods only vs. any viewer (a
Streamerbot-side permission setting, not a code change here), and whether a
redemption-based trigger should have its own distinct cooldown from a
chat-command-based one if both end up calling the same endpoint.

### M (Meta) Flag — Best by Track+Class Exclusion — considered, deferred 2026-09-12
`identifyYWinners_` in `forza_car_updater.gs` excludes `M`-flagged cars
entirely from Y-ranking (`if (s.car.fav === 'M') continue;`), which means an
M car's time can never appear on Best by Track+Class even if it's the
objectively fastest. Floated changing this so Best by Track+Class reflects
truly fastest times regardless of Meta status, while still keeping M cars
out of the Y/W Fav-flag contest itself (a separate, already-correct
exclusion on the `applyUpdates_` side).

**Decided against for now:** an always-dominant Meta car would perpetually
occupy every (Track, Class) record slot, masking whichever non-Meta ("legit
build") car is actually the best performer among the cars the user
considers fair game — which defeats the point of the leaderboard, since
racing Meta is a self-imposed no-go. Revisit only if the motivation changes.

**Confirmed still correctly protected, not in question:** the M flag itself
is never altered by the script (`applyUpdates_` line ~877 skips the entire
Fav-recompute block for `fav === 'M' || fav === 'N'`) — the duplicate-column
incident that made it look like M was being touched was actually a stale
`hmap` lookup resolving to the wrong physical column entirely (see "Win and
Race Counts" below), not the Fav logic misbehaving.

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

**2026-09-16 — same 30s floor is now also hitting a non-drag track regularly.**
While investigating a missing-result report, `stream_assistant.log` showed 4 of
~17 race-end events in one ~24-minute session silently discarded as "too short"
(`10.8s`, `6.0s`, `23.0s`, `27.7s`) — all on the "Quick Lap Test" track, which
apparently often finishes well under 30 seconds even outside drag mode. The
original "rare, drag-only" framing above may not hold for however this track is
actually being used now. No fix made — flagging in case the 30s floor needs
revisiting for short non-drag tracks too, not just the drag-race case.

### False Capture on Quit Race
Quitting mid-race can occasionally produce a bogus result row if pause menu UI
is mistaken for the scoreboard banner. Rare, left unresolved. See CLAUDE.md for
full details and caution around banner detection changes.
