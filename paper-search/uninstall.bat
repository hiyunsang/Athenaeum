@echo off
rem Athenaeum uninstall helper. ASCII only (batch files are read in the system code page).
rem   1) stops the background server (pythonw running this folder's server.py, and whatever listens on the port)
rem   2) removes autostart / desktop shortcuts whose target or arguments point into this Athenaeum folder
rem   3) tells you the folder can now be deleted by hand
rem Your data (papers, summaries, translations, manuscripts) is NOT deleted by this script.
rem   uninstall.bat /dry   -> only shows what would be done
setlocal
set "HERE=%~dp0"
set "DRY="
if /i "%~1"=="/dry" set "DRY=1"
if not defined ATHENAEUM_PORT set "ATHENAEUM_PORT=8770"
echo.
echo Athenaeum uninstall helper
echo   program folder : %HERE%
if defined DRY echo   DRY RUN - nothing will be changed
echo.
echo [1/3] Stopping the background server ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$h=$env:HERE.ToLower(); Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains('server.py') -and $_.CommandLine.ToLower().Contains($h) } | ForEach-Object { if ($env:DRY) { 'would stop pid ' + $_.ProcessId } else { Stop-Process -Id $_.ProcessId -Force; 'stopped pid ' + $_.ProcessId } }"
if defined DRY goto shortcuts
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%ATHENAEUM_PORT% .*LISTENING"') do taskkill /PID %%p /F >nul 2>nul
:shortcuts
echo [2/3] Removing shortcuts that point into this folder ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=(Get-Item -LiteralPath $env:HERE).Parent.FullName.ToLower(); $sh=New-Object -ComObject WScript.Shell; $dirs=@([Environment]::GetFolderPath('Startup'),[Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('CommonDesktopDirectory')); foreach ($d in $dirs) { if ($d) { Get-ChildItem -LiteralPath $d -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object { $lnk=$sh.CreateShortcut($_.FullName); $t=($lnk.TargetPath + ' ' + $lnk.Arguments).ToLower(); if ($t.Contains($root)) { if ($env:DRY) { 'would remove ' + $_.FullName } else { Remove-Item -LiteralPath $_.FullName -Force; 'removed ' + $_.FullName } } } } }"
echo [3/3] Done.
echo.
echo The server is stopped and autostart is removed.
echo You can now delete the Athenaeum folder by hand. It still contains your papers,
echo summaries, translations and manuscripts - move them out first if you want to keep them.
echo Claude Code itself is a separate program and is not touched.
echo.
pause
endlocal
