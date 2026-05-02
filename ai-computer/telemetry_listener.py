# =============================================================
# Stream Assistant - Module 1: Telemetry Listener
# Runs on the AI COMPUTER.
# Listens for Forza Horizon 5/6 UDP telemetry and detects
# race state changes.
#
# Packet structure verified against:
# https://github.com/raweceek-temeletry/forza-horizon-5-UDP
# =============================================================

import socket
import struct
import time
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from config import TELEMETRY_PORT, LOGS_FOLDER, GAME_VERSION

# =============================================================
# Logging - rotating file, max 5MB, keep 3 backups
# =============================================================
os.makedirs(LOGS_FOLDER, exist_ok=True)

_handler = RotatingFileHandler(
    os.path.join(LOGS_FOLDER, "telemetry.log"),
    maxBytes=5*1024*1024,
    backupCount=3
)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(_handler)

log = logging.getLogger(__name__)

# =============================================================
# Race validity thresholds
# A "race" must meet ALL of these to be recorded
# =============================================================
MIN_RACE_DURATION_SECONDS   = 30    # ignore races shorter than this
MIN_MAX_SPEED_MPH           = 10    # car must have moved at some point
MIN_VALID_POSITION          = 1     # position must be at least 1
RACE_END_COOLDOWN_SECONDS   = 10    # ignore duplicate end triggers within this window
RACE_END_DEBOUNCE_PACKETS   = 480   # ~8 seconds at 60Hz - confirmed safe threshold

# =============================================================
# FH5 Verified Packet Field Offsets
# =============================================================
OFFSET_IS_RACE_ON           = 0     # int32
OFFSET_CAR_ORDINAL          = 212   # int32
OFFSET_CAR_CLASS            = 216   # int32
OFFSET_CAR_PI               = 220   # int32
OFFSET_DRIVETRAIN_TYPE      = 224   # int32  0=FWD, 1=RWD, 2=AWD
OFFSET_SPEED                = 256   # float  meters/sec
OFFSET_BEST_LAP             = 296   # float  seconds
OFFSET_LAST_LAP             = 300   # float  seconds
OFFSET_CURRENT_LAP          = 304   # float  seconds
OFFSET_CURRENT_RACE_TIME    = 308   # float  seconds
OFFSET_LAP_NUMBER           = 312   # uint16
OFFSET_RACE_POSITION        = 314   # uint8
OFFSET_GEAR                 = 319   # uint8

DRIVETRAIN_NAMES = {
    0: "FWD", 1: "RWD", 2: "AWD"
}


def pi_to_class(pi):
    """Derive car class letter from PI value. Ranges differ between FH5 and FH6."""
    if GAME_VERSION == "FH6":
        if pi <= 100:   return "E"
        if pi <= 400:   return "D"
        if pi <= 500:   return "C"
        if pi <= 600:   return "B"
        if pi <= 700:   return "A"
        if pi <= 800:   return "S1"
        if pi <= 900:   return "S2"
        return "X"
    else:   # FH5
        if pi <= 100:   return "E"
        if pi <= 500:   return "D"
        if pi <= 600:   return "C"
        if pi <= 700:   return "B"
        if pi <= 800:   return "A"
        if pi <= 900:   return "S1"
        if pi <= 998:   return "S2"
        return "X"


def parse_packet(data):
    """
    Parse raw UDP bytes from FH5/FH6.
    Returns dict of values, or None if packet is too short.
    """
    if len(data) < 320:
        return None

    try:
        is_race_on  = struct.unpack_from('<i', data, OFFSET_IS_RACE_ON)[0]
        car_pi      = struct.unpack_from('<i', data, OFFSET_CAR_PI)[0]
        best_lap    = struct.unpack_from('<f', data, OFFSET_BEST_LAP)[0]
        last_lap    = struct.unpack_from('<f', data, OFFSET_LAST_LAP)[0]
        current_lap = struct.unpack_from('<f', data, OFFSET_CURRENT_LAP)[0]
        race_time   = struct.unpack_from('<f', data, OFFSET_CURRENT_RACE_TIME)[0]
        lap_number  = struct.unpack_from('<H', data, OFFSET_LAP_NUMBER)[0]
        position    = struct.unpack_from('<B', data, OFFSET_RACE_POSITION)[0]
        speed_ms    = struct.unpack_from('<f', data, OFFSET_SPEED)[0]
        car_ordinal = struct.unpack_from('<i', data, OFFSET_CAR_ORDINAL)[0]
        drivetrain  = struct.unpack_from('<i', data, OFFSET_DRIVETRAIN_TYPE)[0]
        gear        = struct.unpack_from('<B', data, OFFSET_GEAR)[0]

        # Use last_lap as fallback if best_lap is 0
        effective_best = best_lap if best_lap > 0 else (last_lap if last_lap > 0 else 0)

        return {
            "is_race_on":   is_race_on,
            "best_lap":     effective_best,
            "last_lap":     last_lap,
            "current_lap":  current_lap,
            "race_time":    race_time,
            "lap_number":   lap_number,
            "position":     position,
            "speed_mph":    speed_ms * 2.237,
            "car_ordinal":  car_ordinal,
            "car_class":    pi_to_class(car_pi),
            "car_pi":       car_pi,
            "drivetrain":   DRIVETRAIN_NAMES.get(drivetrain, f"?({drivetrain})"),
            "gear":         gear,
        }
    except struct.error as e:
        log.warning(f"Packet parse error ({len(data)} bytes): {e}")
        return None


def format_time(seconds):
    """Convert float seconds to mm:ss.mmm string."""
    if seconds is None or seconds <= 0:
        return "--:--.---"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes:02d}:{secs:06.3f}"


class TelemetryListener:
    """
    Listens for FH5/FH6 telemetry and tracks race state.
    Filters out false races (loading screens, lobbies, replays).
    Calls on_race_end(summary) only for valid completed races.
    """

    def __init__(self, on_race_end=None):
        self.on_race_end        = on_race_end
        self.last_race_end_time = 0
        self.last_packet_time   = None
        self.race_end_debounce  = 0
        self.race_end_start_time = None
        self._reset_race_state()

    def _reset_race_state(self):
        """Reset all per-race tracking variables."""
        self.in_race            = False
        self.race_start_wall    = None
        self.best_lap           = None
        self.last_position      = None
        self.last_race_time     = 0
        self.car_class          = None
        self.car_pi             = None
        self.car_ordinal        = None
        self.drivetrain         = None
        self.race_packets       = 0
        self.max_speed_seen     = 0
        self.max_position_seen  = 0
        self.race_end_debounce  = 0

    def start(self):
        """Start listening. Runs until Ctrl+C."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", TELEMETRY_PORT))
        sock.settimeout(2.0)

        print(f"Stream Assistant - Telemetry Listener running on port {TELEMETRY_PORT}")
        print(f"Log file: {os.path.join(LOGS_FOLDER, 'telemetry.log')}")
        print("Waiting for a race to begin... (Press Ctrl+C to stop)")
        log.info(f"Telemetry listener started on port {TELEMETRY_PORT}")

        try:
            while True:
                try:
                    data, addr = sock.recvfrom(4096)
                    self._handle_packet(data)
                except socket.timeout:
                    if self.in_race and self.last_packet_time:
                        silence = time.time() - self.last_packet_time
                        if silence > 3.0:
                            log.info("Packet stream stopped - assuming race ended.")
                            self._handle_race_end("packet timeout")
        except KeyboardInterrupt:
            print("\nTelemetry listener stopped.")
            log.info("Telemetry listener stopped by user.")
        finally:
            sock.close()

    def _handle_packet(self, data):
        """Process one incoming UDP packet."""
        t = parse_packet(data)
        if t is None:
            return

        self.last_packet_time = time.time()
        is_race_on = t["is_race_on"] == 1
        position = t["position"]

        # -------------------------------------------------------
        # RACE END: position == 0 AND is_race_on == 0
        # Debounced - must hold for RACE_END_DEBOUNCE_PACKETS
        # consecutive packets (~8 seconds) to avoid false triggers
        # from rewinds (which recover within ~6 seconds).
        # -------------------------------------------------------
        if self.in_race and position == 0 and not is_race_on:
            now = time.time()
            if self.race_end_debounce == 0:
                self.race_end_start_time = now
            self.race_end_debounce += 1
            if self.race_end_debounce >= RACE_END_DEBOUNCE_PACKETS:
                self._handle_race_end("position=0 + is_race_on=0 (debounced)")
            return
        else:
            # Zero-state cleared - false trigger (rewind/reset)
            self.race_end_debounce = 0

        # -------------------------------------------------------
        # No valid position and not in race - silent discard
        # -------------------------------------------------------
        if position < 1:
            return

        # -------------------------------------------------------
        # RACE START: position >= 1 confirms live race
        # -------------------------------------------------------
        if not self.in_race:
            now = time.time()
            if now - self.last_race_end_time < RACE_END_COOLDOWN_SECONDS:
                return

            self.in_race            = True
            self.race_start_wall    = now
            self.car_class          = t["car_class"]
            self.car_pi             = t["car_pi"]
            self.car_ordinal        = t["car_ordinal"]
            self.drivetrain         = t["drivetrain"]
            log.info(f"Race session started | Class: {self.car_class} "
                     f"({self.car_pi} PI) | {self.drivetrain}")

        self.race_packets += 1

        if t["speed_mph"] > self.max_speed_seen:
            self.max_speed_seen = t["speed_mph"]
        if position > self.max_position_seen:
            self.max_position_seen = position

        if t["best_lap"] > 0:
            if self.best_lap is None or t["best_lap"] < self.best_lap:
                self.best_lap = t["best_lap"]

        self.last_position  = position
        self.last_race_time = t["race_time"]

        if self.race_packets % 300 == 0:
            log.info(
                f"  P{self.last_position} | "
                f"Lap {t['lap_number']} | "
                f"Best: {format_time(self.best_lap)} | "
                f"Race: {format_time(self.last_race_time)} | "
                f"Speed: {t['speed_mph']:.0f}mph | "
                f"Gear: {t['gear']}"
            )

    def _handle_race_end(self, trigger="unknown"):
        """
        Validate and record the completed race.
        Discards false positives and duplicate triggers.
        """
        if not self.in_race:
            return

        self.in_race = False
        self.last_race_end_time = time.time()

        wall_duration = (self.last_race_end_time - self.race_start_wall
                         if self.race_start_wall else 0)

        log.info(f"Race session ended (trigger: {trigger}, "
                 f"duration: {wall_duration:.1f}s, "
                 f"max speed: {self.max_speed_seen:.0f}mph, "
                 f"max position: {self.max_position_seen})")

        reasons = []
        if wall_duration < MIN_RACE_DURATION_SECONDS:
            reasons.append(f"too short ({wall_duration:.1f}s < {MIN_RACE_DURATION_SECONDS}s)")
        if self.max_speed_seen < MIN_MAX_SPEED_MPH:
            reasons.append(f"car never moved (max speed {self.max_speed_seen:.0f}mph)")
        if self.max_position_seen < MIN_VALID_POSITION:
            reasons.append(f"position never registered (max pos {self.max_position_seen})")

        if reasons:
            log.info(f"Race discarded: {', '.join(reasons)}")
            self._reset_race_state()
            return

        summary = {
            "date":             datetime.now().strftime("%Y-%m-%d"),
            "time":             datetime.now().strftime("%H:%M:%S"),
            "car_class":        self.car_class      or "?",
            "car_pi":           self.car_pi         or "?",
            "car_ordinal":      self.car_ordinal    or "?",
            "drivetrain":       self.drivetrain     or "?",
            "finish_position":  self.last_position  or "?",
            "best_lap":         format_time(self.best_lap),
            "best_lap_raw":     self.best_lap       or 0,
            "race_time":        format_time(self.last_race_time),
        }

        log.info("=" * 55)
        log.info("RACE COMPLETE")
        log.info(f"  Date/Time       : {summary['date']} {summary['time']}")
        log.info(f"  Car Class       : {summary['car_class']} ({summary['car_pi']} PI)")
        log.info(f"  Drivetrain      : {summary['drivetrain']}")
        log.info(f"  Finish Position : {summary['finish_position']}")
        log.info(f"  Best Lap        : {summary['best_lap']}")
        log.info(f"  Race Time       : {summary['race_time']}")
        log.info("=" * 55)

        if self.on_race_end:
            self.on_race_end(summary)

        self._reset_race_state()


# =============================================================
# Run standalone for testing: python telemetry_listener.py
# =============================================================
if __name__ == "__main__":
    def test_callback(summary):
        print("\n>>> Race summary received by callback:")
        for key, value in summary.items():
            print(f"    {key:20s}: {value}")

    listener = TelemetryListener(on_race_end=test_callback)
    listener.start()
