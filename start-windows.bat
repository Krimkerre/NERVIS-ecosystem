@echo off
REM Double-click to start the NERVIS ecosystem on Windows.
REM
REM **It starts inside WSL 2, because that is where the ecosystem lives.** On
REM Windows this stack is a Linux stack: the virtual environment, the services,
REM Ollama and code-server are all installed inside the distribution, and their
REM programs sit in ravis/.venv/bin, which Windows' own Python cannot run. This
REM file used to call py.exe and python.exe directly; that could only ever have
REM produced a console window full of "no such file", so what it does now is
REM hand the same tools/run.py to the distribution that owns it.
REM
REM Everything else is in tools/run.py, shared with the macOS and Linux
REM launchers so the sequence cannot drift between platforms.
REM
REM Nothing here needs the repository to be on the C: drive or inside the
REM distribution: "wsl --cd" takes this file's own folder, in Windows' spelling,
REM and translates it — including the \\wsl.localhost\... spelling Explorer uses
REM for a folder that lives inside WSL. The trailing dot keeps the folder's final
REM backslash from escaping the closing quote.
cd /d "%~dp0" 2>nul

where wsl >nul 2>&1 || (
  echo WSL 2 is not installed, and on Windows this ecosystem runs inside it.
  echo Install it with:  wsl --install
  echo Then open the distribution and run: ./install.sh
  pause
  exit /b 1
)

wsl.exe --cd "%~dp0." -- python3 tools/run.py start
if errorlevel 1 (
  echo.
  echo The stack did not start. The usual reasons, in order:
  echo   * the distribution has no python3         - open it and run: ./install.sh
  echo   * NERVIS is installed in another distro   - add: -d ^<name^> after wsl.exe above
  echo   * this Windows build's wsl.exe has no --cd - update WSL: wsl --update
  pause
)
