"""Thin wrapper around the Spotify Web API.

Scopes requested cover both reading the user's playlists and writing to
them, per the project requirements:
    playlist-read-private
    playlist-read-collaborative
    playlist-modify-public
    playlist-modify-private

NOTE ON DEPRECATED / RENAMED ENDPOINTS:

1) Nov 27 2024 deprecations: `/audio-features`, `/audio-analysis`,
   `/recommendations`, and the bulk `/artists` and `/albums` endpoints return
   403 for any app not grandfathered in before that date. This client does
   NOT call `/audio-features`; tempo/energy/danceability/valence/key/mood
   come from app.audio_features.OpenAIAudioFeaturesProvider instead. Genre
   comes from the single-artist endpoint (`/artists/{id}`), which is
   unaffected and remains live.

2) Feb 11 2026 "Dev Mode" changes (see Spotify's Feb 2026 migration guide):
   - `/playlists/{id}/tracks` was renamed to `/playlists/{id}/items` for
     GET/POST/PUT/DELETE. This client already targets `/items`.
   - Critically: `GET /playlists/{id}/items` now only returns track data for
     playlists the authenticated user **owns or collaborates on**. For any
     other playlist - including ALL Spotify-owned/editorial playlists (their
     IDs start with `37i9dQZ...`, e.g. Discover Weekly, Daily Mix, genre
     radio) - the API returns only playlist metadata, and this client raises
     a 403 with a message explaining that. There is no workaround: import a
     playlist you personally created or a collaborative one you're on.
   - `POST /users/{user_id}/playlists` was removed in favor of
     `POST /me/playlists` (no user id needed) - this client already uses it.
"""
import base64
import time
import re
from urllib.parse import urlencode

import httpx

from app.config import get_settings

settings = get_settings()

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

SCOPES = (
    "playlist-read-private playlist-read-collaborative "
    "playlist-modify-public playlist-modify-private user-read-private"
)


class SpotifyAuthError(Exception):
    pass


class SpotifyAPIError(Exception):
    pass


def build_authorize_url(state: str) -> str:
    params = {
        "client_id": settings.spotify_client_id,
        "response_type": "code",
        "redirect_uri": settings.spotify_redirect_uri,
        "scope": SCOPES,
        "state": state,
    }
    return f"{SPOTIFY_AUTH_URL}?{urlencode(params)}"


def _basic_auth_header() -> dict:
    raw = f"{settings.spotify_client_id}:{settings.spotify_client_secret}".encode()
    token = base64.b64encode(raw).decode()
    return {"Authorization": f"Basic {token}"}


async def exchange_code_for_token(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.spotify_redirect_uri,
            },
            headers=_basic_auth_header(),
        )
    if resp.status_code != 200:
        raise SpotifyAuthError(f"Token exchange failed: {resp.status_code} {resp.text}")
    data = resp.json()
    data["obtained_at"] = time.time()
    return data


async def refresh_access_token(refresh_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            SPOTIFY_TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers=_basic_auth_header(),
        )
    if resp.status_code != 200:
        raise SpotifyAuthError(f"Token refresh failed: {resp.status_code} {resp.text}")
    data = resp.json()
    data["obtained_at"] = time.time()
    return data


def extract_playlist_id(playlist_url_or_id: str) -> str:
    """Accepts a full Spotify URL, a spotify: URI, or a bare ID."""
    match = re.search(r"playlist[/:]([a-zA-Z0-9]{22})", playlist_url_or_id)
    if match:
        return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9]{22}", playlist_url_or_id.strip()):
        return playlist_url_or_id.strip()
    raise ValueError("Could not parse a Spotify playlist ID from the given input.")


class SpotifyClient:
    """Per-request client bound to a user's access token."""

    def __init__(self, access_token: str):
        self._headers = {"Authorization": f"Bearer {access_token}"}

    async def _get(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{SPOTIFY_API_BASE}{path}", headers=self._headers, params=params)
        if resp.status_code == 403:
            raise SpotifyAPIError(
                f"Spotify returned 403 for {path}. As of Spotify's Feb 2026 API changes, "
                "this most likely means the playlist isn't one you own or collaborate on "
                "(this includes ALL Spotify-owned/editorial playlists, e.g. IDs starting "
                "with '37i9dQZ...', like Discover Weekly or genre radio - Spotify no longer "
                "exposes their track lists via the API at all). Try importing a playlist you "
                "personally created instead. If this really is your own playlist, double-check "
                "your OAuth scopes include playlist-read-private / playlist-read-collaborative."
            )
        if resp.status_code >= 400:
            raise SpotifyAPIError(f"Spotify GET {path} failed: {resp.status_code} {resp.text}")
        return resp.json()

    async def _post(self, path: str, json: dict | None = None) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{SPOTIFY_API_BASE}{path}", headers=self._headers, json=json)
        if resp.status_code >= 400:
            raise SpotifyAPIError(f"Spotify POST {path} failed: {resp.status_code} {resp.text}")
        return resp.json() if resp.content else {}

    async def _put(self, path: str, json: dict | None = None) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.put(f"{SPOTIFY_API_BASE}{path}", headers=self._headers, json=json)
        if resp.status_code >= 400:
            raise SpotifyAPIError(f"Spotify PUT {path} failed: {resp.status_code} {resp.text}")
        return resp.json() if resp.content else {}

    async def get_current_user(self) -> dict:
        return await self._get("/me")

    async def get_current_user_playlists(self) -> list[dict]:
        """Paginates through GET /me/playlists (owned + followed playlists).
        Unaffected by the Feb 2026 changes - only /users/{id}/playlists (for
        OTHER users) was removed; the current-user version remains."""
        playlists: list[dict] = []
        url_path = "/me/playlists"
        params = {"limit": 50}
        while True:
            data = await self._get(url_path, params=params)
            playlists.extend(p for p in data.get("items", []) if p)
            next_url = data.get("next")
            if not next_url:
                break
            url_path = next_url.replace(SPOTIFY_API_BASE, "")
            params = None
        return playlists

    async def get_playlist(self, playlist_id: str) -> dict:
        return await self._get(f"/playlists/{playlist_id}", params={"fields": "id,name,description,images"})

    async def update_playlist_details(self, playlist_id: str, description: str) -> None:
        """PUT /playlists/{playlist_id} - Change Playlist Details. Untouched
        by the Feb 2026 changes (confirmed against Spotify's own reference
        docs); requires playlist-modify-public/private, which this app
        already requests. Only the description is touched here; name/
        public/collaborative are left as-is by omitting them from the body."""
        await self._put(f"/playlists/{playlist_id}", json={"description": description})

    async def get_playlist_tracks(self, playlist_id: str) -> list[dict]:
        """Paginates through all tracks in a playlist.

        Uses `/items` (renamed from `/tracks` in Feb 2026). Only returns data
        for playlists the user owns or collaborates on - see the module
        docstring. The per-row field is named `item` in the new response
        shape, but we also check the old `track` key defensively in case
        Spotify's rollout is inconsistent across accounts.
        """
        tracks: list[dict] = []
        url_path = f"/playlists/{playlist_id}/items"
        params = {
            "limit": 100,
            "fields": "items(item(id,name,duration_ms,album(name,images),artists(id,name)),track(id,name,duration_ms,album(name,images),artists(id,name))),next",
        }
        while True:
            data = await self._get(url_path, params=params)
            for row in data.get("items", []):
                track = row.get("item") or row.get("track")
                if track and track.get("id"):
                    tracks.append(track)
            next_url = data.get("next")
            if not next_url:
                break
            # subsequent pages come as absolute URLs; strip the API base
            url_path = next_url.replace(SPOTIFY_API_BASE, "")
            params = None
        return tracks

    async def get_artist_genres(self, artist_id: str) -> list[str]:
        """Single-artist endpoint remains available post-deprecation."""
        try:
            data = await self._get(f"/artists/{artist_id}")
            return data.get("genres", []) or []
        except SpotifyAPIError:
            return []

    async def create_playlist(self, name: str, description: str = "", public: bool = False) -> dict:
        """POST /me/playlists (replaces the removed POST /users/{id}/playlists)."""
        return await self._post(
            "/me/playlists",
            json={"name": name, "description": description, "public": public},
        )

    async def add_track_to_playlist(self, playlist_id: str, track_uri: str) -> dict:
        return await self._post(f"/playlists/{playlist_id}/items", json={"uris": [track_uri]})

    async def remove_track_from_playlist(self, playlist_id: str, track_uri: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.request(
                "DELETE",
                f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/items",
                headers=self._headers,
                json={"items": [{"uri": track_uri}]},
            )
        if resp.status_code >= 400:
            raise SpotifyAPIError(f"Spotify DELETE item failed: {resp.status_code} {resp.text}")
        return resp.json() if resp.content else {}
