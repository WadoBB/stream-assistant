@echo off
:: =============================================================
:: Stream Assistant - Personal Monitor Window
:: Runs on the AI COMPUTER (same machine as controller.py) since
:: that's where the Corsair Xeneon Edge is attached - uses
:: localhost rather than the LAN IP for that reason.
::
:: Opens /monitor (both overlays stacked) in a small chromeless window.
::
:: NO forced --window-position/--window-size (removed 2026-09-21) -
:: a guessed position landed the window off the visible desktop
:: entirely (invisible everywhere except a stale Alt-Tab thumbnail),
:: and a second guess had the same problem, with no way to verify the
:: real monitor layout remotely. Brave remembers this app window's
:: last position/size in its own profile across launches (same as any
:: Chromium "app" shortcut), so the fix is: let it open wherever
:: Chromium defaults to (always somewhere visible on the primary
:: display), drag it onto the Xeneon Edge and resize it BY HAND once,
:: and it should reopen there next time without any flags forcing it.
:: =============================================================

set MONITOR_URL=http://localhost:5000/monitor

:: Prefer Brave (Chromium-based --app mode - no address bar/tabs).
:: Checked both per-user and machine-wide install locations.
set BRAVE_EXE=%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe
if not exist "%BRAVE_EXE%" set BRAVE_EXE=%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe
if not exist "%BRAVE_EXE%" set BRAVE_EXE=%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe

if exist "%BRAVE_EXE%" (
    start "" "%BRAVE_EXE%" --new-window --app=%MONITOR_URL%
    exit /b 0
)

:: Fall back to Firefox - same reasoning, no forced position/size.
set FIREFOX_EXE=%ProgramFiles%\Mozilla Firefox\firefox.exe
if not exist "%FIREFOX_EXE%" set FIREFOX_EXE=%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe

if exist "%FIREFOX_EXE%" (
    start "" "%FIREFOX_EXE%" -new-window %MONITOR_URL%
    exit /b 0
)

echo ERROR: Could not find Brave or Firefox in the usual install locations.
echo Edit BRAVE_EXE/FIREFOX_EXE in this script to point at your actual install path.
pause
