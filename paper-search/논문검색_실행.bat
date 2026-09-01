@echo off
start "" "C:\Users\PC1\AppData\Local\Programs\Python\Python38\pythonw.exe" "C:\Users\PC1\Documents\MAENG_paper\paper-search\server.py"
timeout /t 1 >nul
where chrome >nul 2>nul
if %errorlevel%==0 (
  start "" chrome --app=http://localhost:8770
) else (
  start "" msedge --app=http://localhost:8770
)
