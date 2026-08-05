import json

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from openai import OpenAIError

from app.config import get_settings
from app.db import init_db
from app.routers import auth, import_, playlists, songs, spotify

settings = get_settings()

app = FastAPI(title="Spotify Playlist Sorter", version="1.0.0")

# Signed session cookie holds the Spotify OAuth tokens server-side per browser
# session. `https_only` is enabled automatically in production.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=settings.is_production,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# None of the individual OpenAI call sites (audio_features.py,
# openai_client.py) wrap their `json.loads(...)` or the API call itself in
# try/except - a missing/invalid OPENAI_API_KEY, a rate limit, or the model
# occasionally not returning clean JSON despite response_format=json_object
# would otherwise surface as a raw, unhelpful 500. These two handlers catch
# every such case globally, across every route, without needing to touch
# each call site individually.
@app.exception_handler(OpenAIError)
async def openai_error_handler(request: Request, exc: OpenAIError):
    return JSONResponse(
        status_code=502,
        content={"detail": f"OpenAI request failed ({exc}). Check OPENAI_API_KEY in backend/.env and try again."},
    )


@app.exception_handler(json.JSONDecodeError)
async def openai_json_error_handler(request: Request, exc: json.JSONDecodeError):
    return JSONResponse(
        status_code=502,
        content={"detail": "OpenAI returned something this app couldn't parse. Try again."},
    )


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/api/health")
def health():
    return {"status": "ok", "environment": settings.environment}


app.include_router(auth.router)
app.include_router(import_.router)
app.include_router(playlists.router)
app.include_router(songs.router)
app.include_router(spotify.router)
