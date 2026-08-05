from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Playlist
from app.schemas import SpotifyPlaylistSummary
from app import spotify_client
from app.routers.auth import get_valid_access_token

router = APIRouter(prefix="/api/spotify", tags=["spotify"])


@router.get("/my-playlists", response_model=list[SpotifyPlaylistSummary])
async def list_my_spotify_playlists(request: Request, db: Session = Depends(get_db)):
    """Powers the Library's 'Load My Playlists' button - fetches every
    playlist on the user's Spotify account (owned or followed) so they can
    pick which ones to bring into the sorter via POST /api/playlists/adopt."""
    try:
        access_token = await get_valid_access_token(request)
    except PermissionError:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify. Log in first.")

    client = spotify_client.SpotifyClient(access_token)
    try:
        raw_playlists = await client.get_current_user_playlists()
    except spotify_client.SpotifyAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    my_user_id = request.session.get("spotify_user_id")
    tracked = {p.spotify_playlist_id: p.id for p in db.query(Playlist).all()}

    results = []
    for pl in raw_playlists:
        pl_id = pl.get("id")
        if not pl_id:
            continue  # Spotify can return null entries for unavailable playlists
        images = pl.get("images") or []
        # Field renamed `tracks` -> `items` in Feb 2026; accept either shape.
        track_field = pl.get("tracks") or pl.get("items") or {}
        owner = pl.get("owner") or {}

        results.append({
            "spotify_playlist_id": pl_id,
            "name": pl.get("name") or "Untitled",
            "description": pl.get("description") or "",
            "image_url": images[0]["url"] if images else None,
            "track_count": track_field.get("total"),
            "is_owner": bool(my_user_id) and owner.get("id") == my_user_id,
            "collaborative": bool(pl.get("collaborative")),
            "already_tracked": pl_id in tracked,
            "local_playlist_id": tracked.get(pl_id),
        })
    return results
