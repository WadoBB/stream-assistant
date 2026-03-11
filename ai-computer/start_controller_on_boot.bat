@echo off
:: =============================================================
:: Stream Assistant Controller - Auto Start
:: Place this file in your Windows Startup folder:
::   Press Win+R → type shell:startup → Enter → paste here
::
:: Starts controller.py minimized at Windows login so the
:: Stream Deck button works without any manual steps.
:: =============================================================
cd C:\StreamAssistant\ai-computer
start "Stream Assistant Controller" /min python controller.py
