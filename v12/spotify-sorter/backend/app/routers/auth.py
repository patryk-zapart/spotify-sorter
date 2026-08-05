import secrets
import time

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.config import get_settings
from app import spotify_client

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


@router.get("/login")
async def login(request: Request):
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    return RedirectResponse(spotify_client.build_authorize_url(state))


@router.get("/callback")
async def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return RedirectResponse(f"{settings.frontend_url}/?auth_error={error}")

    expected_state = request.session.get("oauth_state")
    if not code or not state or state != expected_state:
        return RedirectResponse(f"{settings.frontend_url}/?auth_error=state_mismatch")

    token_data = await spotify_client.exchange_code_for_token(code)
    request.session["spotify_tokens"] = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
        "expires_in": token_data["expires_in"],
        "obtained_at": token_data["obtained_at"],
    }

    client = spotify_client.SpotifyClient(token_data["access_token"])
    me = await client.get_current_user()
    request.session["spotify_user_id"] = me["id"]

    return RedirectResponse(f"{settings.frontend_url}/library")


@router.get("/status")
async def status(request: Request):
    tokens = request.session.get("spotify_tokens")
    return {"authenticated": bool(tokens), "user_id": request.session.get("spotify_user_id")}


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


async def get_valid_access_token(request: Request) -> str:
    """Returns a valid access token, refreshing it first if it has expired."""
    tokens = request.session.get("spotify_tokens")
    if not tokens:
        raise PermissionError("Not authenticated with Spotify")

    expires_at = tokens["obtained_at"] + tokens["expires_in"]
    if time.time() > expires_at - 60 and tokens.get("refresh_token"):
        refreshed = await spotify_client.refresh_access_token(tokens["refresh_token"])
        tokens["access_token"] = refreshed["access_token"]
        tokens["expires_in"] = refreshed["expires_in"]
        tokens["obtained_at"] = refreshed["obtained_at"]
        tokens["refresh_token"] = refreshed.get("refresh_token", tokens["refresh_token"])
        request.session["spotify_tokens"] = tokens

    return tokens["access_token"]
