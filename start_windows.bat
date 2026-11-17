@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 open_atlas.py
) else (
  python open_atlas.py
)
pause
