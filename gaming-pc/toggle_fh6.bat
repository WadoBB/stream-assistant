@echo off
:: =============================================================
:: Stream Assistant Toggle - Forza Horizon 6
:: Assigned to Stream Deck button on Gaming PC.
:: Press once to start logging FH6 races, press again to stop.
:: controller.py auto-starts with Windows on AI computer.
:: =============================================================

:: Kill any leftover Capture Agent first regardless of state
taskkill /f /im python.exe > nul 2>&1

:: Check current state from controller
set RESPONSE=
for /f "tokens=*" %%i in ('curl -s --max-time 5 http://192.168.137.230:5000/status') do set RESPONSE=%%i

:: Check if stopped or running
echo %RESPONSE% | find "stopped" > nul
if not errorlevel 1 (
    :: CURRENTLY STOPPED - START EVERYTHING
    echo Starting Stream Assistant (FH6)...
    start "Capture Agent" /min cmd /c "cd C:\StreamAssistant\gaming-pc && python capture_agent.py --game FH6"
    curl -s "http://192.168.137.230:5000/toggle?game=FH6" > nul
    echo Stream Assistant STARTED (FH6)
    exit /b 0
)

echo %RESPONSE% | find "running" > nul
if not errorlevel 1 (
    :: CURRENTLY RUNNING - STOP EVERYTHING
    echo Stopping Stream Assistant...
    curl -s "http://192.168.137.230:5000/toggle?game=FH6" > nul
    taskkill /f /im python.exe > nul 2>&1
    echo Stream Assistant STOPPED
    exit /b 0
)

:: Could not reach controller
echo ERROR: Could not reach Stream Assistant Controller
echo Make sure AI computer is on and controller.py is running
pause
