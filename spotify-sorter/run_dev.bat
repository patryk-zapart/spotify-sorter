@echo off
REM Runs the FastAPI backend and Vite frontend together on Windows.
REM Double-click this file, or run it from cmd.exe / PowerShell.

echo Starting backend on http://127.0.0.1:8000 ...
start "Sorted - Backend" cmd /k "%~dp0backend\start_windows.bat"

echo Starting frontend on http://127.0.0.1:5173 ...
start "Sorted - Frontend" cmd /k "%~dp0frontend\start_windows.bat"

echo.
echo Both services are starting in their own windows.
echo Close those windows (or Ctrl+C inside them) to stop the app.
