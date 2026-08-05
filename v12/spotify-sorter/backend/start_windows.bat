@echo off
REM Sets up (once) and starts the FastAPI backend on Windows.
cd /d "%~dp0"

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate.bat
pip install -q -r requirements.txt

if not exist .env (
    echo No .env found - copying .env.example. Edit backend\.env with your real keys before importing playlists.
    copy .env.example .env >nul
)

uvicorn app.main:app --reload --port 8000
