@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py runtime_deck.py
) else (
    python runtime_deck.py
)
