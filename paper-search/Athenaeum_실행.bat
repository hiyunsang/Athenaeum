@echo off
rem Athenaeum - starts the local server without a console window and opens the app window.
rem Works from any folder: uses this .bat's own folder (%~dp0). If the server is already running, only the window opens.
setlocal
set "HERE=%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%HERE%server.py"
) else (
  where pyw >nul 2>nul
  if %errorlevel%==0 (
    start "" pyw -3 "%HERE%server.py"
  ) else (
    echo Python 3 (pythonw) was not found on PATH. Install Python 3.8+ from python.org and check "Add to PATH".
    pause
    exit /b 1
  )
)
timeout /t 2 >nul
where chrome >nul 2>nul
if %errorlevel%==0 (
  start "" chrome --app=http://localhost:8770
) else (
  start "" msedge --app=http://localhost:8770
)
endlocal
