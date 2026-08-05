from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Song, SongStatus
from app.schemas import ImportRequest
from app import spotify_client
from app.audio_features import get_audio_features_provider
from app.track_ingest import get_or_create_song
from app.routers.auth import get_valid_access_token

router = APIRouter(prefix="/api/import", tags=["import"])


@router.post("")
async def import_playlist(payload: ImportRequest, request: Request, db: Session = Depends(get_db)):
    try:
        access_token = await get_valid_access_token(request)
    except PermissionError:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify. Log in first.")

    try:
        playlist_id = spotify_client.extract_playlist_id(payload.playlist_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    client = spotify_client.SpotifyClient(access_token)

    try:
        tracks = await client.get_playlist_tracks(playlist_id)
    except spotify_client.SpotifyAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not tracks:
        raise HTTPException(status_code=404, detail="No tracks found in that playlist.")

    provider = get_audio_features_provider(client)

    # Cache artist genre lookups within this import batch to cut down API calls.
    genre_cache: dict[str, list[str]] = {}
    existing_max = db.query(Song).count()

    created = []
    already_known = 0
    for i, track in enumerate(tracks):
        song, was_created = await get_or_create_song(
            track, client, provider, genre_cache, db,
            source_playlist_id=playlist_id,
            status=SongStatus.QUEUED,
            queue_position=existing_max + i,
        )
        if was_created:
            created.append(song)
        else:
            already_known += 1

    db.commit()
    return {"imported": len(created), "already_known": already_known, "playlist_id": playlist_id}
