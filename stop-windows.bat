@echo off
REM Double-click to stop the NERVIS ecosystem on Windows.
REM
REM **It stops inside WSL 2, because that is where the services are.** See
REM start-windows.bat for why Windows' own Python cannot do this: the processes
REM this stops are Linux processes, and the PID file that names them was written
REM by the launcher inside the distribution.
REM
REM Everything else is in tools/run.py, shared with the macOS and Linux
REM launchers so the sequence cannot drift between platforms.
cd /d "%~dp0" 2>nul

where wsl >nul 2>&1 || (
  echo WSL 2 is not installed, so nothing of this ecosystem is running.
  pause
  exit /b 1
)

wsl.exe --cd "%~dp0." -- python3 tools/run.py stop
if errorlevel 1 (
  echo.
  echo The stack did not stop cleanly. Open the distribution and run:
  echo   python3 tools/run.py status
  pause
)
