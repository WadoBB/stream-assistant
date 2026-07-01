/**
 * ============================================================================
 *  Forza Car Updater  —  Google Apps Script
 *  ----------------------------------------------------------------------------
 *  Reads the Results tab, computes per-car stats, then updates the Cars tab
 *  (including the Fav flag), refreshes the "Best by Track+Class" tab, and
 *  logs the run to the "Analysis Log" tab.
 *
 *  Author: Benny  |  Version: 1.7  |  Last revised: 2026-07-01
 *
 *  PREREQUISITE: the Cars tab must have a column named "Car Name" — Forza's
 *  short, unique identifier for each car.  This is the only field used to
 *  match Results rows to inventory rows.  A Cars row is uniquely identified
 *  by the triple (Car Name, Class, Type), so the same Car Name can appear
 *  in multiple rows if you've tuned it to different classes.
 *
 *  HOW TO INSTALL
 *  1. Open the Forza Horizon spreadsheet in Google Sheets.
 *  2. Extensions → Apps Script.
 *  3. Delete the placeholder Code.gs contents and paste this entire file.
 *  4. Save (the disk icon).  Name the project "Forza Car Updater".
 *  5. Reload the spreadsheet — a new "Forza" menu appears in the toolbar.
 *  6. The first time you click "Forza → Update Cars", Google asks for
 *     permissions; review and allow (it only touches THIS spreadsheet).
 *  7. To enable the daily scheduled run: "Forza → Enable Daily Schedule".
 *
 *  WHAT IT DOES (per run)
 *   - Reads the Results tab and matches each row to a Cars row by exact
 *     (Car Name, Class, Race Type) match.
 *   - Adds computed columns to Cars (if missing): Win Rate, Races, Best Time,
 *     Last Raced.  Refreshes those values for every row.
 *   - Auto-adds any (Car Name, Class, Race Type) combo seen in Results but
 *     missing from the Cars tab.  New rows always get Car Name, Class, Type,
 *     and (if the FH6 column exists) a '1' in FH6 to flag the car as owned.
 *     If another existing row has the same Car Name, that row's Year/MFG/Model
 *     are carried over so the new row lands in the right place when sorted.
 *   - Rewrites the Fav column per Benny's rules:
 *        Y = holds the record on at least one (Track, Class) — i.e. the car
 *            is competitive somewhere.  M cars are excluded from the contest.
 *        W = ≥3 races AND ≥50% win rate (1st place only)
 *        P = previously Y, no longer holds any record
 *        R = previously W, no longer qualifies
 *        M, N = NEVER touched (manual designations)
 *        blank/other = left as-is
 *      Y wins over W if a car qualifies for both.
 *   - Rebuilds the "Best by Track+Class" tab — one row per (Track, Class)
 *     showing the record-holding car, its time, and its stats.
 *   - Appends a row to "Analysis Log" summarising the run.
 *
 *  TUNING
 *   - Constants are near the top of the file under CONFIG. Change rule
 *     thresholds, sheet names, etc. there without touching the logic.
 * ============================================================================
 */

// ============================== CONFIG =====================================

const CONFIG = {
  CARS_SHEET:    'Cars',
  RESULTS_SHEET: 'Results',
  BEST_BY_SHEET: 'Best by Track+Class',   // was: Best by Class+Type
  LOG_SHEET:     'Analysis Log',

  // Fav rule thresholds
  W_MIN_RACES:    3,
  W_MIN_WIN_RATE: 0.50,   // 50% — fraction of races finished in 1st place

  // Computed columns appended to the Cars tab if they don't already exist
  COMPUTED_COLS: ['Win Rate', 'Races', 'Best Time', 'Last Raced'],

  // Race Type canonicalisation.  Both Cars.Type and Results.'Race Type' are
  // run through this map (case-insensitive).  Anything not listed is kept
  // verbatim, so unknown values still flow through and surface in the log.
  // Keyword fallback in normalizeType_ also catches phrases containing
  // "road", "street", "dirt", or "cross-country" anywhere in them.
  RACE_TYPE_MAP: {
    // Road family
    'road':                    'Road',
    'road circuit':            'Road',
    'road sprint':             'Road',
    'street':                  'Road',
    'street race':             'Road',
    'touge':                   'Road',    // FH6 Touge races are street-based
    // Dirt family
    'dirt':                    'Dirt',
    'dirt circuit':            'Dirt',
    'dirt point to point':     'Dirt',
    'dirt trial':              'Dirt',
    'cross-country':           'Dirt',
    'cross country':           'Dirt',
    'crosscountry':            'Dirt',
    'cross-country circuit':   'Dirt',
    'cross country circuit':   'Dirt'
  },

  // Header names (must match exactly what's in your sheet — case-sensitive)
  CARS_HEADERS: {
    FH6: 'FH6',                                 // ownership flag column (optional)
    YEAR: 'Year', MFG: 'MFG', MODEL: 'Model',
    CAR_NAME: 'Car Name',                       // Forza's unique shorthand
    CLASS: 'Class', TYPE: 'Type', FAV: 'Fav'
  },
  RESULTS_HEADERS: {
    DATE: 'Date', RACE_ID: 'Race ID', POSITION: 'Position', CAR: 'Car',
    CLASS: 'Class', RACE_TYPE: 'Race Type', TRACK: 'Track',
    TOTAL_RACERS: 'Total Racers', BEST_LAP: 'Best Lap',
    RACE_TIME: 'Race Time', NOTES: 'Notes'
  }
};

// ============================== ENTRY POINTS ===============================

/**
 * Adds the "Forza" menu to the spreadsheet.  Runs automatically when the
 * spreadsheet is opened.
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Forza')
    .addItem('Update Cars (run now)', 'runUpdate')
    .addItem('Diagnose Matching (read-only)', 'diagnoseMatching')
    .addSeparator()
    .addItem('Enable Daily Schedule', 'enableDailyTrigger')
    .addItem('Disable Daily Schedule', 'disableDailyTrigger')
    .addToUi();
}

/**
 * Manual entry point — wired to the "Forza → Update Cars" menu.
 */
function runUpdate() {
  const summary = updateForzaCars_();
  // Show a brief popup so the user knows it finished.
  SpreadsheetApp.getActive().toast(
    `Done. ${summary.carsUpdated} cars updated, ` +
    `${summary.carsAdded} added, ${summary.unresolved} unresolved.`,
    'Forza Car Updater', 8
  );
}

/**
 * Scheduled entry point — wired to the time-based trigger.
 * Same behaviour as runUpdate() but no popup.
 */
function runUpdateScheduled() {
  updateForzaCars_();
}

/** Installs a daily trigger that fires runUpdateScheduled() each morning. */
function enableDailyTrigger() {
  disableDailyTrigger();   // clear any duplicates first
  ScriptApp.newTrigger('runUpdateScheduled')
    .timeBased()
    .everyDays(1)
    .atHour(6)             // ~6am in the script's timezone
    .create();
  SpreadsheetApp.getActive().toast('Daily schedule enabled (06:00).',
                                   'Forza Car Updater', 5);
}

/**
 * READ-ONLY diagnostic: figures out which Results rows would match an
 * existing Cars row and which wouldn't, and writes a "Match Debug" tab
 * with side-by-side comparison and character codes for unmatched ones.
 * Does NOT modify the Cars tab or any other data.  Run this before doing
 * a real Update if matching seems off.
 */
function diagnoseMatching() {
  const ss      = SpreadsheetApp.getActive();
  const cars    = readCars_(ss);
  const results = readResults_(ss);

  // Collect unique (carName, class, raceType) keys from Results.  Note that
  // r.raceType has already been canonicalised by normalizeType_() in
  // readResults_(), so this matches the keys built from Cars.
  const seen = new Map();   // matchKey -> { carName, class, raceType, count }
  for (const r of results) {
    if (!r.car) continue;
    const k = makeCarKey_(r.car, r.class, r.raceType);
    if (!seen.has(k)) {
      seen.set(k, { carName: r.car, class: r.class, raceType: r.raceType,
                    count: 0 });
    }
    seen.get(k).count += 1;
  }

  // For each unique Results key, check if it matches a Cars row and, if not,
  // find the closest Cars row by Car Name (case/quote/dash insensitive).
  const out = [];
  for (const [k, v] of seen.entries()) {
    const matched = cars.byKey.has(k);
    let closest = null;
    if (!matched) {
      const target = normalize_(v.carName);
      // First pass: same normalized Car Name
      for (const c of cars.rows) {
        if (normalize_(c.carName) === target) { closest = c; break; }
      }
      // Second pass: substring
      if (!closest) {
        for (const c of cars.rows) {
          const cn = normalize_(c.carName);
          if (cn && (cn.includes(target) || target.includes(cn))) {
            closest = c; break;
          }
        }
      }
    }
    out.push({ matched, k, v, closest });
  }
  // Sort: misses first (so they're at the top), then by car name
  out.sort((a, b) => {
    if (a.matched !== b.matched) return a.matched ? 1 : -1;
    return String(a.v.carName).localeCompare(String(b.v.carName));
  });

  // Write the Match Debug tab.
  let sheet = ss.getSheetByName('Match Debug');
  if (!sheet) sheet = ss.insertSheet('Match Debug');
  sheet.clear();

  const headers = [
    'Status', 'Races', 'Results: Car Name', 'Results: Class',
    'Results: Type (canon)', 'Match Key',
    'Cars: Car Name', 'Cars: Class', 'Cars: Type', 'Cars Match Key',
    'Char Codes (Results Car Name)', 'Char Codes (Cars Car Name)'
  ];
  sheet.getRange(1, 1, 1, headers.length).setValues([headers])
       .setFontWeight('bold');
  sheet.setFrozenRows(1);

  const rows = out.map(o => [
    o.matched ? 'MATCH' : 'MISS',
    o.v.count,
    o.v.carName,
    o.v.class,
    o.v.raceType,
    o.k,
    o.closest ? o.closest.carName : '',
    o.closest ? o.closest.class   : '',
    o.closest ? o.closest.type    : '',
    o.closest ? carObjKey_(o.closest) : '',
    o.matched ? '' : charCodes_(o.v.carName),
    (o.matched || !o.closest) ? '' : charCodes_(o.closest.carName)
  ]);
  if (rows.length) {
    sheet.getRange(2, 1, rows.length, headers.length).setValues(rows);
  }
  sheet.autoResizeColumns(1, headers.length);

  const hits   = out.filter(o => o.matched).length;
  const misses = out.length - hits;
  SpreadsheetApp.getActive().toast(
    `Match Debug written. ${hits} matched, ${misses} unmatched. ` +
    `(See "Match Debug" tab.)`, 'Forza Diagnose', 10);
}

/** Returns a "C(99) h(104) a(97) r(114)..." char-by-char dump, useful for
 *  spotting hidden characters that look identical but aren't. */
function charCodes_(s) {
  if (!s) return '';
  return Array.from(String(s))
    .map(c => `${c}(${c.charCodeAt(0)})`).join(' ');
}

/** Removes any existing time-based triggers for this script. */
function disableDailyTrigger() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'runUpdateScheduled')
    .forEach(t => ScriptApp.deleteTrigger(t));
  SpreadsheetApp.getActive().toast('Daily schedule disabled.',
                                   'Forza Car Updater', 5);
}

// ============================== MAIN PIPELINE ==============================

/**
 * The actual work.  Trailing underscore = "private to this script".
 * Returns a summary object used by callers for logging / toasts.
 */
function updateForzaCars_() {
  const ss      = SpreadsheetApp.getActive();
  const cars    = readCars_(ss);
  const results = readResults_(ss);

  // 1. Match each Results row to a Cars row by (Car Name, Class, Race Type).
  //    Any unmatched (Car Name, Class, Race Type) combo is queued for auto-add.
  const newCarSeeds = new Map(); // key -> { carName, class, type }
  const unresolved  = [];        // results with a blank/unusable Car field

  for (const r of results) {
    if (!r.car) { unresolved.push(''); continue; }
    const hit = matchCar_(r.car, r.class, r.raceType, cars.byKey);
    if (!hit) {
      const k = makeCarKey_(r.car, r.class, r.raceType);
      if (!newCarSeeds.has(k)) {
        // If we already have a row with the same Car Name (different
        // Class/Type), inherit its Year/MFG/Model so the new row lands in
        // the right place when you sort.
        const catalog = cars.byCarName.get(normalize_(r.car)) || {};
        newCarSeeds.set(k, {
          carName: r.car, class: r.class, type: r.raceType,
          year:  catalog.year  || '',
          mfg:   catalog.mfg   || '',
          model: catalog.model || ''
        });
      }
    }
  }

  // 2. Append the new (Car Name, Class, Type) entries to Cars.
  const carsAdded = appendNewCars_(ss, cars, [...newCarSeeds.values()]);

  // 3. Reload Cars now that new rows exist, then re-match every result.
  const carsAfter = readCars_(ss);
  const allMatched = [];
  for (const r of results) {
    if (!r.car) continue;
    const hit = matchCar_(r.car, r.class, r.raceType, carsAfter.byKey);
    if (hit) allMatched.push({ result: r, carRow: hit });
  }

  // 4. Per-car aggregation.
  const stats = aggregatePerCar_(allMatched);

  // 5. Identify Y winners (best time per Track+Class, M cars excluded).
  const yWinners = identifyYWinners_(stats, carsAfter);

  // 6. Compute new Fav for each car and write all updates back to Cars tab.
  const favTransitions = applyUpdates_(ss, carsAfter, stats, yWinners);

  // 7. Refresh the "Best by Track+Class" tab.
  rebuildBestByTab_(ss, stats, yWinners, carsAfter);

  // 8. Append a row to the Analysis Log.
  const summary = {
    timestamp:    new Date(),
    racesRead:    results.length,
    carsTotal:    carsAfter.rows.length,
    carsAdded:    carsAdded,
    carsUpdated:  favTransitions.touched,
    unresolved:   unresolved.length,
    yPromoted:    favTransitions.yPromoted,
    wPromoted:    favTransitions.wPromoted,
    yToP:         favTransitions.yToP,
    wToR:         favTransitions.wToR,
    unresolvedSamples: unresolved.slice(0, 5).join(' | ')
  };
  appendLog_(ss, summary);
  return summary;
}

// ============================== READERS ====================================

/**
 * Loads the Cars tab into memory.  Returns:
 *   {
 *     sheet, headerMap, headers,
 *     rows:   [ {rowNum, year, mfg, model, carName, class, type, fav} ],
 *     byKey:  Map("carName||class||type" lowercased -> rowObj)
 *   }
 *
 * The unique key for a row is (Car Name, Class, Type).  The same Car Name
 * may appear in multiple rows if tuned to different classes — each is its
 * own inventory entry with its own stats.
 */
function readCars_(ss) {
  const sheet = ss.getSheetByName(CONFIG.CARS_SHEET);
  if (!sheet) throw new Error(`Sheet "${CONFIG.CARS_SHEET}" not found.`);

  const values = sheet.getDataRange().getValues();
  if (values.length < 2) throw new Error('Cars tab appears empty.');

  const headers = values[0].map(h => String(h).trim());
  const headerMap = makeHeaderMap_(headers);

  // Make sure Car Name column exists; if not, fail loudly because the whole
  // matching strategy depends on it.
  if (headerMap[CONFIG.CARS_HEADERS.CAR_NAME] === undefined) {
    throw new Error(
      `Cars tab is missing the "${CONFIG.CARS_HEADERS.CAR_NAME}" column. ` +
      `Add it (Forza's shorthand car name) and run again.`);
  }

  ensureComputedColumns_(sheet, headers, headerMap);  // may grow the sheet
  // Re-read after possibly adding columns
  const valuesNow = sheet.getDataRange().getValues();
  const headersNow = valuesNow[0].map(h => String(h).trim());
  const hmap = makeHeaderMap_(headersNow);

  const rows       = [];
  const byKey      = new Map();
  const byCarName  = new Map();   // normalize(carName) -> {year, mfg, model}
                                  // catalog used when auto-adding a new
                                  // (Car Name, Class, Type) row so we can
                                  // inherit Year/MFG/Model from another row
                                  // that has the same Car Name.

  for (let i = 1; i < valuesNow.length; i++) {
    const r = valuesNow[i];
    const carName = String(r[hmap[CONFIG.CARS_HEADERS.CAR_NAME]] || '').trim();
    const cls     = String(r[hmap[CONFIG.CARS_HEADERS.CLASS]]    || '').trim();
    const typ     = normalizeType_(r[hmap[CONFIG.CARS_HEADERS.TYPE]]);
    if (!carName) continue;   // skip rows that lack the matching key

    const obj = {
      rowNum:  i + 1,
      year:    String(r[hmap[CONFIG.CARS_HEADERS.YEAR]]  || '').trim(),
      mfg:     String(r[hmap[CONFIG.CARS_HEADERS.MFG]]   || '').trim(),
      model:   String(r[hmap[CONFIG.CARS_HEADERS.MODEL]] || '').trim(),
      carName: carName,
      class:   cls,
      type:    typ,
      fav:     String(r[hmap[CONFIG.CARS_HEADERS.FAV]] || '').trim().toUpperCase()
    };
    rows.push(obj);
    byKey.set(makeCarKey_(carName, cls, typ), obj);

    // Track first row for each Car Name that has catalog data filled in.
    // If multiple rows share a Car Name we prefer the one with the most
    // populated catalog fields — typically there's only one with the data
    // and the script-added duplicates have it blank anyway.
    const nameKey = normalize_(carName);
    const existing = byCarName.get(nameKey);
    const score = (obj.year ? 1 : 0) + (obj.mfg ? 1 : 0) + (obj.model ? 1 : 0);
    if (!existing || score > existing.score) {
      byCarName.set(nameKey, {
        year: obj.year, mfg: obj.mfg, model: obj.model, score
      });
    }
  }

  return { sheet, headerMap: hmap, headers: headersNow,
           rows, byKey, byCarName };
}

/**
 * Loads the Results tab.  Returns array of normalised result rows.
 * Rows with Notes = "Spec Race", "Touge", or "Time Attack" are excluded —
 * these are non-competitive races where position 1 is always guaranteed,
 * so including them would inflate win rates.
 */
function readResults_(ss) {
  const sheet = ss.getSheetByName(CONFIG.RESULTS_SHEET);
  if (!sheet) throw new Error(`Sheet "${CONFIG.RESULTS_SHEET}" not found.`);

  const values = sheet.getDataRange().getValues();
  if (values.length < 2) return [];

  const headers = values[0].map(h => String(h).trim());
  const h = makeHeaderMap_(headers);
  const H = CONFIG.RESULTS_HEADERS;

  const out = [];
  for (let i = 1; i < values.length; i++) {
    const r = values[i];
    const car = String(r[h[H.CAR]] || '').trim();
    if (!car) continue;  // ignore rows with no car

    // Non-competitive races are recorded in Results for time-tracking purposes
    // but excluded from car stats — position is always 1 in these events so
    // counting them would falsely inflate win rates and race counts.
    const notes = String(r[h[H.NOTES]] || '').trim();
    if (notes === 'Spec Race' || notes === 'Touge' || notes === 'Time Attack') continue;

    out.push({
      rowNum:      i + 1,
      date:        toDate_(r[h[H.DATE]]),
      raceId:      String(r[h[H.RACE_ID]] || '').trim(),
      position:    toIntOrNull_(r[h[H.POSITION]]),
      car:         car,
      class:       String(r[h[H.CLASS]]      || '').trim(),
      raceType:    normalizeType_(r[h[H.RACE_TYPE]]),    // Road/Dirt canonical
      track:       String(r[h[H.TRACK]]      || '').trim(),
      totalRacers: toIntOrNull_(r[h[H.TOTAL_RACERS]]),
      bestLapSec:  parseTime_(r[h[H.BEST_LAP]]),
      raceTimeSec: parseTime_(r[h[H.RACE_TIME]]),
      notes:       String(r[h[H.NOTES]] || '').trim()
    });
  }
  return out;
}

// ============================== AGGREGATION ================================

/**
 * Aggregates per-car stats from matched (result -> car) pairs.
 * Returns Map(carKey -> stats):
 *   { car: rowObj, races, wins, winRate,
 *     bestTimeSec, bestTimeSource ('lap' | 'race'),
 *     bestTimeRace,                       // result row that produced bestTime
 *     lastRaced (Date),
 *     classCounts: Map(class -> n),
 *     typeCounts:  Map(type  -> n) }
 */
function aggregatePerCar_(matched) {
  const stats = new Map();

  for (const m of matched) {
    const k = carObjKey_(m.carRow);
    if (!stats.has(k)) {
      stats.set(k, {
        car: m.carRow,
        races: 0, wins: 0,
        bestTimeSec: null, bestTimeSource: null, bestTimeRace: null,
        lastRaced: null,
        classCounts: new Map(),
        typeCounts:  new Map(),
        // Per-(Track, Class) best times — used for Y-flag ranking.  A car
        // becomes Y if it holds the record in at least one (Track, Class)
        // bucket.  Keyed "track||class" (lower-cased), value carries time,
        // source, and the result row that produced the time.
        bestByTC:   new Map()
      });
    }
    const s = stats.get(k);
    s.races += 1;
    if (m.result.position === 1) s.wins += 1;

    // Best time: prefer Best Lap; fall back to Race Time.
    const t   = m.result.bestLapSec  != null ? m.result.bestLapSec  :
                m.result.raceTimeSec != null ? m.result.raceTimeSec : null;
    const src = m.result.bestLapSec  != null ? 'lap' :
                m.result.raceTimeSec != null ? 'race' : null;

    // Global best (used for the Cars tab "Best Time" column).
    if (t != null && (s.bestTimeSec == null || t < s.bestTimeSec)) {
      s.bestTimeSec = t;
      s.bestTimeSource = src;
      s.bestTimeRace   = m.result;
    }

    // Per-(Track, Class) best (used for Y ranking).
    if (t != null && m.result.track && m.result.class) {
      const tc = `${normalize_(m.result.track)}||${normalize_(m.result.class)}`;
      const cur = s.bestByTC.get(tc);
      if (!cur || t < cur.time) {
        s.bestByTC.set(tc, {
          time: t, source: src, race: m.result,
          track: m.result.track, class: m.result.class
        });
      }
    }

    if (m.result.date && (!s.lastRaced || m.result.date > s.lastRaced)) {
      s.lastRaced = m.result.date;
    }
    incCount_(s.classCounts, m.result.class);
    incCount_(s.typeCounts,  m.result.raceType);
  }

  // Finalise winRate.
  for (const s of stats.values()) {
    s.winRate = s.races > 0 ? s.wins / s.races : 0;
  }
  return stats;
}

/**
 * For each (Track, Class) in the data, find the best car (lowest best time),
 * EXCLUDING cars currently flagged Fav = M.
 *
 * Returns Map(tcKey -> carKey).  tcKey = "track||class" (lower-cased).
 */
function identifyYWinners_(stats, cars) {
  const groups = new Map();   // tcKey -> [{carKey, time}]

  for (const [carKey, s] of stats.entries()) {
    if (s.car.fav === 'M') continue;
    for (const [tc, rec] of s.bestByTC.entries()) {
      pushMulti_(groups, tc, { carKey, time: rec.time });
    }
  }

  const winners = new Map();
  for (const [tc, list] of groups.entries()) {
    list.sort((a, b) => a.time - b.time);
    winners.set(tc, list[0].carKey);
  }
  return winners;
}

/**
 * Returns the (Track, Class) groups in which the given car holds the record.
 * A car can hold Y in many groups if it has set fastest times at several
 * tracks within its class.  The single Fav cell still stores just 'Y' —
 * the per-track detail shows up on the "Best by Track+Class" tab.
 */
function carYGroups_(carKey, yWinners) {
  const groups = [];
  for (const [tc, winner] of yWinners.entries()) {
    if (winner === carKey) groups.push(tc);
  }
  return groups;
}

// ============================== WRITERS ====================================

/**
 * Writes computed columns and updated Fav back to the Cars tab.
 * Returns counts for the analysis log.
 */
function applyUpdates_(ss, cars, stats, yWinners) {
  const sheet = cars.sheet;
  const hmap  = cars.headerMap;
  const colFav   = hmap[CONFIG.CARS_HEADERS.FAV];
  const colType  = hmap[CONFIG.CARS_HEADERS.TYPE];
  const colWinR  = hmap['Win Rate'];
  const colRaces = hmap['Races'];
  const colBestT = hmap['Best Time'];
  const colLastR = hmap['Last Raced'];

  // Bail early if there are no data rows — nothing to update.
  if (!cars.rows.length) {
    return { touched: 0, yPromoted: 0, wPromoted: 0, yToP: 0, wToR: 0 };
  }

  // cars.rows contains row objects with their original rowNum.  We use a
  // contiguous block from the first to the last car row so a single batch
  // setValues() per column suffices.
  const firstRow = cars.rows[0].rowNum;
  const lastRow  = cars.rows[cars.rows.length - 1].rowNum;
  const nRows = lastRow - firstRow + 1;

  // Build per-column write buffers (read-modify-write so blank rows in the
  // middle of the data block aren't overwritten).
  const winR    = sheet.getRange(firstRow, colWinR + 1,  nRows, 1).getValues();
  const races   = sheet.getRange(firstRow, colRaces + 1, nRows, 1).getValues();
  const bestT   = sheet.getRange(firstRow, colBestT + 1, nRows, 1).getValues();
  const lastR   = sheet.getRange(firstRow, colLastR + 1, nRows, 1).getValues();
  const favCol  = sheet.getRange(firstRow, colFav + 1,   nRows, 1).getValues();
  const typeCol = sheet.getRange(firstRow, colType + 1,  nRows, 1).getValues();

  let touched = 0, yPromoted = 0, wPromoted = 0, yToP = 0, wToR = 0;

  for (const car of cars.rows) {
    const idx = car.rowNum - firstRow;   // index into the buffer arrays
    const k   = carObjKey_(car);
    const s   = stats.get(k);

    // --- Type cell: rewrite to canonical (Road/Dirt) value if it normalised
    //     to something different from the cell's current text.  Unknown
    //     types pass through unchanged via normalizeType_.
    typeCol[idx][0] = car.type;
    if (s) {
      winR[idx][0]  = s.winRate;
      races[idx][0] = s.races;
      bestT[idx][0] = s.bestTimeSec != null ? formatTime_(s.bestTimeSec) : '';
      lastR[idx][0] = s.lastRaced || '';
      touched++;
    } else {
      winR[idx][0]  = '';
      races[idx][0] = 0;
      bestT[idx][0] = '';
      lastR[idx][0] = '';
    }

    // --- Fav transitions ---
    if (car.fav === 'M' || car.fav === 'N') continue;   // never modify

    let newFav = car.fav;

    if (s) {
      const isY = carYGroups_(k, yWinners).length > 0;
      const isW = s.races >= CONFIG.W_MIN_RACES &&
                  s.winRate >= CONFIG.W_MIN_WIN_RATE;
      if (isY)                  newFav = 'Y';
      else if (isW)             newFav = 'W';
      else if (car.fav === 'Y') newFav = 'P';
      else if (car.fav === 'W') newFav = 'R';
      // else: preserve (blank, P, R, etc.)
    }

    if (newFav !== car.fav) {
      favCol[idx][0] = newFav;
      if (newFav === 'Y' && car.fav !== 'Y') yPromoted++;
      if (newFav === 'W' && car.fav !== 'W') wPromoted++;
      if (newFav === 'P' && car.fav === 'Y') yToP++;
      if (newFav === 'R' && car.fav === 'W') wToR++;
    }
  }

  // One batch write per column — orders of magnitude faster than per-cell.
  sheet.getRange(firstRow, colWinR + 1,  nRows, 1).setValues(winR)
       .setNumberFormat('0.0%');
  sheet.getRange(firstRow, colRaces + 1, nRows, 1).setValues(races);
  sheet.getRange(firstRow, colBestT + 1, nRows, 1).setValues(bestT);
  sheet.getRange(firstRow, colLastR + 1, nRows, 1).setValues(lastR);
  sheet.getRange(firstRow, colFav + 1,   nRows, 1).setValues(favCol);
  sheet.getRange(firstRow, colType + 1,  nRows, 1).setValues(typeCol);

  return { touched, yPromoted, wPromoted, yToP, wToR };
}

/**
 * Appends rows to the Cars tab for (Car Name, Class, Type) combos seen in
 * Results but missing from inventory.  Fills Car Name, Class, and Type
 * always; Year/MFG/Model are filled when seed.year/mfg/model are present
 * (carried over from another existing row with the same Car Name).  Also
 * stamps the FH6 ownership column with '1' if that column exists, since the
 * fact that this car appears in Results means the user has acquired it.
 * Returns the number of rows added.
 */
function appendNewCars_(ss, cars, seeds) {
  if (!seeds.length) return 0;
  const sheet = cars.sheet;
  const hmap  = cars.headerMap;
  const ncols = cars.headers.length;
  const fh6Col = hmap[CONFIG.CARS_HEADERS.FH6];   // undefined if no FH6 col

  const newRows = seeds.map(seed => {
    const row = new Array(ncols).fill('');
    row[hmap[CONFIG.CARS_HEADERS.CAR_NAME]] = seed.carName;
    row[hmap[CONFIG.CARS_HEADERS.CLASS]]    = seed.class || '';
    row[hmap[CONFIG.CARS_HEADERS.TYPE]]     = seed.type  || '';
    if (seed.year)  row[hmap[CONFIG.CARS_HEADERS.YEAR]]  = seed.year;
    if (seed.mfg)   row[hmap[CONFIG.CARS_HEADERS.MFG]]   = seed.mfg;
    if (seed.model) row[hmap[CONFIG.CARS_HEADERS.MODEL]] = seed.model;
    if (fh6Col !== undefined) row[fh6Col] = '1';     // own-the-car marker
    return row;
  });

  sheet.getRange(sheet.getLastRow() + 1, 1, newRows.length, ncols)
       .setValues(newRows);
  return newRows.length;
}

/**
 * Wipes & rebuilds the "Best by Track+Class" tab — one row per (Track, Class)
 * combo, with the car holding the record at that track in that class.
 */
function rebuildBestByTab_(ss, stats, yWinners, cars) {
  let sheet = ss.getSheetByName(CONFIG.BEST_BY_SHEET);
  if (!sheet) sheet = ss.insertSheet(CONFIG.BEST_BY_SHEET);
  sheet.clear();

  const headers = ['Track', 'Class', 'Type', 'Car Name',
                   'Year', 'MFG', 'Model',
                   'Best Time', 'Source', 'Races', 'Win Rate',
                   'Last Raced', 'Updated'];
  sheet.getRange(1, 1, 1, headers.length).setValues([headers])
       .setFontWeight('bold');

  const now = new Date();
  const rows = [];

  // Build display rows — preserve the original (non-lower-cased) Track and
  // Class strings by reading them from the per-car bestByTC record we kept.
  for (const [tcKey, carKey] of yWinners.entries()) {
    const s = stats.get(carKey);
    if (!s) continue;
    const rec = s.bestByTC.get(tcKey);
    if (!rec) continue;
    rows.push({
      track: rec.track, class: rec.class, type: s.car.type,
      carName: s.car.carName,
      year: s.car.year, mfg: s.car.mfg, model: s.car.model,
      bestTimeSec: rec.time, source: rec.source,
      races: s.races, winRate: s.winRate, lastRaced: s.lastRaced || ''
    });
  }
  // Sort by Class, then Track, for predictable browsing.
  rows.sort((a, b) => {
    if (a.class !== b.class) return a.class < b.class ? -1 : 1;
    return a.track < b.track ? -1 : (a.track > b.track ? 1 : 0);
  });

  const data = rows.map(r => [
    r.track, r.class, r.type, r.carName,
    r.year, r.mfg, r.model,
    formatTime_(r.bestTimeSec), r.source,
    r.races, r.winRate, r.lastRaced, now
  ]);

  if (data.length) {
    sheet.getRange(2, 1, data.length, headers.length).setValues(data);
    const wrCol = headers.indexOf('Win Rate') + 1;
    sheet.getRange(2, wrCol, data.length, 1).setNumberFormat('0.0%');
  }
  sheet.autoResizeColumns(1, headers.length);
  sheet.setFrozenRows(1);
}

/**
 * Appends a one-line summary to the Analysis Log tab.
 */
function appendLog_(ss, summary) {
  let sheet = ss.getSheetByName(CONFIG.LOG_SHEET);
  if (!sheet) {
    sheet = ss.insertSheet(CONFIG.LOG_SHEET);
    sheet.appendRow([
      'Run Timestamp', 'Races Read', 'Cars Total',
      'Cars Added', 'Cars Updated',
      'Y Promoted', 'W Promoted', 'Y→P', 'W→R',
      'Unresolved', 'Unresolved Samples'
    ]);
    sheet.getRange(1, 1, 1, 11).setFontWeight('bold');
  }
  sheet.appendRow([
    summary.timestamp, summary.racesRead, summary.carsTotal,
    summary.carsAdded, summary.carsUpdated,
    summary.yPromoted, summary.wPromoted, summary.yToP, summary.wToR,
    summary.unresolved, summary.unresolvedSamples
  ]);
}

// ============================== HELPERS ====================================

/** Builds {headerName -> 0-based column index}.  Missing names map to undefined. */
function makeHeaderMap_(headers) {
  const m = {};
  headers.forEach((h, i) => { m[h] = i; });
  return m;
}

/**
 * Ensures the Cars sheet has the four computed columns appended at the end.
 * Mutates the sheet — adds missing headers.
 */
function ensureComputedColumns_(sheet, headers, hmap) {
  let lastCol = headers.length;
  for (const name of CONFIG.COMPUTED_COLS) {
    if (hmap[name] === undefined) {
      lastCol += 1;
      sheet.getRange(1, lastCol).setValue(name).setFontWeight('bold');
      hmap[name] = lastCol - 1;   // keep map in sync
      headers.push(name);
    }
  }
}

/**
 * Exact match: a Cars row is identified by (Car Name, Class, Type).  The
 * Forza-supplied Car field in Results is treated as the Car Name verbatim.
 */
function matchCar_(carName, cls, typ, byKey) {
  if (!carName) return null;
  return byKey.get(makeCarKey_(carName, cls, typ)) || null;
}

/** Composite key for a car row: lowercased "carName||class||type". */
function makeCarKey_(carName, cls, typ) {
  return `${normalize_(carName)}||${normalize_(cls)}||${normalize_(typ)}`;
}
function carObjKey_(c) { return makeCarKey_(c.carName, c.class, c.type); }

/**
 * Normalises a string for matching.  Crucial for car names because Google
 * Sheets auto-replaces straight quotes with curly ones (' -> ') as you type,
 * while Forza emits straight ASCII quotes — so two strings that look identical
 * to the eye fail an exact comparison.  This collapses Unicode "smart"
 * variants of quotes, dashes, and spaces into their ASCII equivalents,
 * lowercases, trims, and collapses internal whitespace.
 */
function normalize_(s) {
  return String(s || '')
    // Smart single quotes & low-9 quote -> ASCII apostrophe
    .replace(/[''‚‛′]/g, "'")
    // Smart double quotes -> ASCII straight double
    .replace(/[""„‟″]/g, '"')
    // En-dash, em-dash, minus, figure-dash -> ASCII hyphen
    .replace(/[–—―−]/g, '-')
    // Non-breaking space, narrow no-break, thin space -> regular space
    .replace(/[   ]/g, ' ')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ');
}

/**
 * Canonicalises a Race Type to "Road" or "Dirt".  Recognises both exact
 * matches in CONFIG.RACE_TYPE_MAP and longer phrases that contain the
 * telltale keyword anywhere in them — so "Road Racing", "Street Scene",
 * "Cross-Country Racing", "Dirt Racing", etc. all resolve correctly.
 * Unknown values pass through verbatim (just trimmed) so we never silently
 * drop data.
 */
function normalizeType_(s) {
  const raw = String(s || '').trim();
  if (!raw) return '';
  const key = raw.toLowerCase().replace(/\s+/g, ' ');

  // 1. Exact-key override from the map (lets user pin specific phrases).
  if (CONFIG.RACE_TYPE_MAP[key]) return CONFIG.RACE_TYPE_MAP[key];

  // 2. Keyword detection — order matters: "cross-country" implies dirt
  //    even when accompanied by "country road" etc.  Check Dirt first.
  if (/\bdirt\b|\bcross[\s-]?country\b/.test(key))    return 'Dirt';
  if (/\broad\b|\bstreet\b/.test(key))                return 'Road';

  // 3. Unknown — pass through.
  return raw;
}

/**
 * Parses a time string into seconds.  Supports formats like:
 *    "1:23.456"        -> 83.456
 *    "0:01:23.456"     -> 83.456
 *    "01:23"           -> 83
 *    "83.456"          -> 83.456
 *    Date or numeric value (Sheets sometimes auto-converts)
 * Returns null for blank/unparseable.
 */
function parseTime_(v) {
  if (v == null || v === '') return null;

  // If Sheets returned a Date (possible when cells are time-formatted), convert.
  if (Object.prototype.toString.call(v) === '[object Date]') {
    const h = v.getHours(), m = v.getMinutes(),
          sec = v.getSeconds(), ms = v.getMilliseconds();
    return h * 3600 + m * 60 + sec + ms / 1000;
  }

  // Pure number = seconds (or fraction of a day if Sheets gave us a serial)
  if (typeof v === 'number') {
    // If it's a sheet duration serial (< 1), convert from days to seconds.
    if (v > 0 && v < 1) return v * 86400;
    return v;
  }

  const s = String(v).trim();
  if (!s) return null;

  // h:mm:ss.fff or mm:ss.fff or ss.fff
  const parts = s.split(':');
  let secs;
  try {
    if (parts.length === 3) {
      secs = parseInt(parts[0], 10) * 3600 +
             parseInt(parts[1], 10) * 60 +
             parseFloat(parts[2]);
    } else if (parts.length === 2) {
      secs = parseInt(parts[0], 10) * 60 + parseFloat(parts[1]);
    } else {
      secs = parseFloat(parts[0]);
    }
  } catch (e) { return null; }
  return isFinite(secs) ? secs : null;
}

/** Formats seconds back into "m:ss.fff" for display. */
function formatTime_(secs) {
  if (secs == null || !isFinite(secs)) return '';
  const total = Math.max(0, secs);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total - h * 3600) / 60);
  const s = total - h * 3600 - m * 60;
  const sStr = s.toFixed(3).padStart(6, '0');
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${sStr}`
               : `${m}:${sStr}`;
}

function toIntOrNull_(v) {
  if (v === '' || v == null) return null;
  const n = Number(v);
  return isFinite(n) ? Math.round(n) : null;
}
function toDate_(v) {
  if (!v) return null;
  if (Object.prototype.toString.call(v) === '[object Date]') return v;
  const d = new Date(v);
  return isNaN(d.getTime()) ? null : d;
}

/** Map<string,number> incrementer. */
function incCount_(map, key) {
  if (!key) return;
  map.set(key, (map.get(key) || 0) + 1);
}
/** Returns the most-frequent key from a Map<string,number>, or null. */
function mode_(map) {
  let best = null, bestN = -1;
  for (const [k, n] of map.entries()) {
    if (n > bestN) { best = k; bestN = n; }
  }
  return best;
}
/** push-to-multimap helper. */
function pushMulti_(map, key, val) {
  if (!key) return;
  const list = map.get(key);
  if (list) list.push(val); else map.set(key, [val]);
}
