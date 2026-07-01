@echo off
:: =============================================================
:: Stream Assistant Manual Capture
:: Assigned to Stream Deck button on Gaming PC.
:: Press when the race results screen is visible to capture it
:: immediately — use for Rivals races and any other case where
:: the normal telemetry-driven capture doesn't fire.
:: =============================================================

curl -s --max-time 5 "http://192.168.137.230:5000/capture_now" > nul
