@echo off
:: =============================================================
:: Stream Assistant - Personal Monitor Window
:: Opens /monitor (both overlays stacked) in a small chromeless
:: window, sized/positioned for a narrow secondary display like
:: the Corsair Xeneon Edge run in portrait (720 wide x 2560 tall).
::
:: PLACEHOLDER POSITION - X/Y below assume the Xeneon Edge sits
:: immediately to the right of a 1920x1080 primary display at
:: Y=0. If it opens on the wrong monitor or is offset, check
:: Settings > System > Display for its actual position/resolution
:: and adjust WIN_X/WIN_Y/WIN_W/WIN_H below.
:: =============================================================

set WIN_X=1920
set WIN_Y=0
set WIN_W=720
set WIN_H=2560
set MONITOR_URL=http://192.168.137.230:5000/monitor

:: Prefer Brave (Chromium-based - reliable --window-position/--window-size
:: in --app mode, no address bar/tabs). Checked both per-user and
:: machine-wide install locations.
set BRAVE_EXE=%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe
if not exist "%BRAVE_EXE%" set BRAVE_EXE=%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe
if not exist "%BRAVE_EXE%" set BRAVE_EXE=%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe

if exist "%BRAVE_EXE%" (
    start "" "%BRAVE_EXE%" --new-window --app=%MONITOR_URL% --window-position=%WIN_X%,%WIN_Y% --window-size=%WIN_W%,%WIN_H%
    exit /b 0
)

:: Fall back to Firefox - no reliable CLI window-position flag, so it
:: opens with just the right size wherever it last was; drag it onto the
:: Xeneon Edge once and Firefox will remember that position next time.
set FIREFOX_EXE=%ProgramFiles%\Mozilla Firefox\firefox.exe
if not exist "%FIREFOX_EXE%" set FIREFOX_EXE=%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe

if exist "%FIREFOX_EXE%" (
    start "" "%FIREFOX_EXE%" -new-window -width %WIN_W% -height %WIN_H% %MONITOR_URL%
    exit /b 0
)

echo ERROR: Could not find Brave or Firefox in the usual install locations.
echo Edit BRAVE_EXE/FIREFOX_EXE in this script to point at your actual install path.
pause
