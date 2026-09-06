@echo off
rem Athenaeum launcher. ASCII only inside (batch files are read in the system code page, so no Korean here).
rem Starts the local server without a console window, waits until it answers, then opens the app window.
rem Uses the bundled python folder (portable release) if present, otherwise pythonw on PATH.
rem Env for testing: ATHENAEUM_PORT (default 8770), ATHENAEUM_NOBROWSER=1 (do not open a browser).
setlocal
set "HERE=%~dp0"
if not defined ATHENAEUM_PORT set "ATHENAEUM_PORT=8770"
set "PYW="
set "PY="
if exist "%HERE%..\python\pythonw.exe" (
  set "PYW=%HERE%..\python\pythonw.exe"
  set "PY=%HERE%..\python\python.exe"
) else (
  where pythonw >nul 2>nul && set "PYW=pythonw" && set "PY=python"
)
if not defined PYW (
  echo Python 3 was not found. Use the portable release which includes a python folder, or install Python 3.8+ with Add to PATH.
  pause
  exit /b 1
)
start "" "%PYW%" "%HERE%server.py"
rem wait up to ~12 s for the server to answer on the port
set /a N=0
:waitloop
"%PY%" -c "import socket,os,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1', int(os.environ.get('ATHENAEUM_PORT','8770'))))==0 else 1)" >nul 2>nul
if %errorlevel%==0 goto up
set /a N+=1
if %N% geq 12 goto failed
timeout /t 1 >nul
goto waitloop
:failed
echo The server did not start. Running it in this window to show the error:
echo.
"%PY%" "%HERE%server.py"
pause
exit /b 1
:up
if defined ATHENAEUM_NOBROWSER goto end
where chrome >nul 2>nul
if %errorlevel%==0 (
  start "" chrome --app=http://localhost:%ATHENAEUM_PORT%
) else (
  start "" msedge --app=http://localhost:%ATHENAEUM_PORT%
)
:end
endlocal
