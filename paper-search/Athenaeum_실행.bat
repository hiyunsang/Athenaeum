@echo off
rem Athenaeum - starts the local server without a console window and opens the app window.
rem Uses the bundled python\ (portable release) if present, otherwise pythonw/pyw on PATH. Works from any folder (%~dp0).
setlocal
set "HERE=%~dp0"
if exist "%HERE%..\python\pythonw.exe" (
  start "" "%HERE%..\python\pythonw.exe" "%HERE%server.py"
  goto open
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%HERE%server.py"
  goto open
)
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 "%HERE%server.py"
  goto open
)
echo Python 3 was not found. Use the portable release (python folder included) or install Python 3.8+ with "Add to PATH".
pause
exit /b 1
:open
timeout /t 2 >nul
where chrome >nul 2>nul
if %errorlevel%==0 (
  start "" chrome --app=http://localhost:8770
) else (
  start "" msedge --app=http://localhost:8770
)
endlocal
