@echo off
REM Sets up (once) and starts the Vite dev server on Windows.
cd /d "%~dp0"

if not exist node_modules (
    echo Installing frontend dependencies...
    call npm install
)

if not exist .env (
    echo No .env found - copying .env.example.
    copy .env.example .env >nul
)

call npm run dev
