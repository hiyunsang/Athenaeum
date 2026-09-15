@echo off
rem Athenaeum setup helper launcher. ASCII only (batch files are read in the system code page).
rem Runs paper-search\setup.py with the bundled python (portable release) or python on PATH.
setlocal
set "HERE=%~dp0"
if exist "%HERE%..\python\python.exe" goto bundled
where python >nul 2>nul
if errorlevel 1 goto nopy
python "%HERE%setup.py" %*
goto end
:bundled
"%HERE%..\python\python.exe" "%HERE%setup.py" %*
goto end
:nopy
echo Python 3 was not found. Use the portable release which includes a python folder, or install Python 3.8+ with Add to PATH.
pause
:end
endlocal
