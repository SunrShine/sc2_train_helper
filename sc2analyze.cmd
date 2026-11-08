@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Drag a .SC2Replay file onto this file, or run:
  echo    sc2analyze.cmd "path\to\game.SC2Replay"
  echo    sc2analyze.cmd batch "path\to\replay\folder" -o reports
  echo.
  python -m replay_analyzer --help
  pause
  exit /b 1
)
python -m replay_analyzer %*
if errorlevel 1 pause
