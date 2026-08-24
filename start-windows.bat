@echo off
REM Double-click to start the NERVIS ecosystem.
cd /d "%~dp0"

REM py.exe ships with a python.org install; python.exe is what a Store or manual
REM install puts on PATH. Trying both is the difference between working and a
REM console window that closes instantly.
where py >nul 2>&1 && (
  py -3 tools\run.py start
) || (
  python tools\run.py start
)
if errorlevel 1 pause
