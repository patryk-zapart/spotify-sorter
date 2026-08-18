from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Song, Playlist, PlaylistSong, SongStatus
from app.schemas import AssignBatchRequest, MembershipRequest, QueueSongOut, NameSuggestionsOut, song_out_dict
from app import spotify_client
from app.fit_score import compute_fit_score
from app.embeddings import fetch_embeddings_for
from app.openai_client import suggest_playlist_names
from app.routers.auth import get_valid_access_token

router = APIRouter(tags=["songs"])


async def _fit_scores_for(song: Song, db: Session) -> list[dict]:
    playlists = db.query(Playlist).order_by(Playlist.created_at).all()
    profiles = [p.sonic_profile() for p in playlists]

    # One batched embeddings fetch covering every genre/vibe tag the song
    # and every playlist (plus each playlist's title_signal) references,
    # rather than fetching per playlist - keeps this fast even with many
    # local playlists.
    embeddings = await fetch_embeddings_for([song], profiles, db)
    db.commit()  # persist any newly-cached embedding vectors

    results = []
    for p, profile in zip(playlists, profiles):
        result = compute_fit_score(song, profile, embeddings)
        results.append({
            "playlist_id": p.id,
            "playlist_name": p.name,
            "score": result["score"],
            "breakdown": result["breakdown"],
            "explanation": result["explanation"],
            "song_count": profile["song_count"],
            "has_signal": profile["has_signal"],
        })
    results.sort(key=lambda r: -r["score"])
    return results


@router.get("/api/queue/next", response_model=QueueSongOut | None)
async def get_next_in_queue(db: Session = Depends(get_db)):
    song = (
        db.query(Song)
        .filter(Song.status == SongStatus.QUEUED)
        .order_by(Song.queue_position)
        .first()
    )
    if not song:
        return None

    remaining = db.query(Song).filter(Song.status == SongStatus.QUEUED).count()
    return {
        "song": song_out_dict(song),
        "fit_scores": await _fit_scores_for(song, db),
        "remaining_in_queue": remaining,
    }


@router.get("/api/queue/count")
def queue_count(db: Session = Depends(get_db)):
    return {
        "remaining": db.query(Song).filter(Song.status == SongStatus.QUEUED).count(),
        "skipped": db.query(Song).filter(Song.status == SongStatus.SKIPPED).count(),
    }


@router.post("/api/queue/requeue-skipped")
def requeue_skipped(db: Session = Depends(get_db)):
    """Brings every skipped song back to the front of the review queue."""
    skipped = db.query(Song).filter(Song.status == SongStatus.SKIPPED).all()
    for s in skipped:
        s.status = SongStatus.QUEUED
    db.commit()
    return {"requeued": len(skipped)}


@router.post("/api/songs/{song_id}/skip")
def skip_song(song_id: int, db: Session = Depends(get_db)):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    song.status = SongStatus.SKIPPED
    db.commit()
    return {"ok": True}


async def _ensure_membership(
    song: Song, playlist: Playlist, client: spotify_client.SpotifyClient, db: Session
) -> PlaylistSong:
    """Adds `song` to `playlist` on Spotify and records the membership
    locally, unless it's already a member (idempotent)."""
    existing = (
        db.query(PlaylistSong)
        .filter_by(playlist_id=playlist.id, song_id=song.id)
        .first()
    )
    if existing:
        return existing

    track_uri = f"spotify:track:{song.spotify_track_id}"
    try:
        await client.add_track_to_playlist(playlist.spotify_playlist_id, track_uri)
    except spotify_client.SpotifyAPIError as e:
        raise HTTPException(status_code=502, detail=f"Failed writing to Spotify: {e}")

    profile = playlist.sonic_profile()
    embeddings = await fetch_embeddings_for([song], [profile], db)
    fit = compute_fit_score(song, profile, embeddings)
    membership = PlaylistSong(
        playlist_id=playlist.id,
        song_id=song.id,
        fit_score=fit["score"],
        fit_breakdown=fit["breakdown"],
    )
    db.add(membership)
    db.flush()
    return membership


@router.post("/api/songs/{song_id}/assign-batch")
async def assign_batch(
    song_id: int, payload: AssignBatchRequest, request: Request, db: Session = Depends(get_db)
):
    """Main Review Queue action: assign one song to one or more playlists at once."""
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    if not payload.playlist_ids:
        raise HTTPException(status_code=400, detail="Select at least one playlist, or use Skip.")

    playlist_ids = list(dict.fromkeys(payload.playlist_ids))  # de-dupe, keep order
    playlists = db.query(Playlist).filter(Playlist.id.in_(playlist_ids)).all()
    if len(playlists) != len(playlist_ids):
        raise HTTPException(status_code=404, detail="One or more selected playlists were not found.")

    try:
        access_token = await get_valid_access_token(request)
    except PermissionError:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify. Log in first.")
    client = spotify_client.SpotifyClient(access_token)

    for playlist in playlists:
        await _ensure_membership(song, playlist, client, db)

    song.status = SongStatus.ASSIGNED
    db.commit()
    return {"ok": True, "song_id": song.id, "playlist_ids": [p.id for p in playlists]}


@router.post("/api/songs/{song_id}/memberships")
async def add_membership(
    song_id: int, payload: MembershipRequest, request: Request, db: Session = Depends(get_db)
):
    """Adds an already-processed song to one more playlist (used by the
    'add to another playlist' control on a playlist card)."""
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    playlist = db.get(Playlist, payload.playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    try:
        access_token = await get_valid_access_token(request)
    except PermissionError:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify. Log in first.")
    client = spotify_client.SpotifyClient(access_token)

    await _ensure_membership(song, playlist, client, db)
    song.status = SongStatus.ASSIGNED
    db.commit()
    return {"ok": True}


@router.delete("/api/songs/{song_id}/memberships/{playlist_id}")
async def remove_membership(
    song_id: int, playlist_id: int, request: Request, requeue: bool = False, db: Session = Depends(get_db)
):
    """Removes a song from one specific playlist (manual override).

    If that was the song's last remaining playlist, it goes back to the
    queue automatically regardless of `requeue`. Passing `requeue=true`
    additionally sends it back to the Review Queue for reconsideration even
    if it's still a member of other playlists - e.g. "this doesn't belong
    here, but I'm not sure where it does belong, so take another look." A
    requeued song jumps to the FRONT of the queue (lowest queue_position)
    rather than waiting behind whatever's already there, since asking for
    reconsideration is an explicit, immediate request.
    """
    song = db.get(Song, song_id)
    playlist = db.get(Playlist, playlist_id)
    if not song or not playlist:
        raise HTTPException(status_code=404, detail="Song or playlist not found")

    membership = db.query(PlaylistSong).filter_by(playlist_id=playlist_id, song_id=song_id).first()
    if not membership:
        return {"ok": True, "note": "Song wasn't a member of that playlist."}

    try:
        access_token = await get_valid_access_token(request)
    except PermissionError:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify. Log in first.")
    client = spotify_client.SpotifyClient(access_token)

    track_uri = f"spotify:track:{song.spotify_track_id}"
    try:
        await client.remove_track_from_playlist(playlist.spotify_playlist_id, track_uri)
    except spotify_client.SpotifyAPIError as e:
        raise HTTPException(status_code=502, detail=f"Failed updating Spotify: {e}")

    db.delete(membership)
    db.flush()

    remaining = db.query(PlaylistSong).filter_by(song_id=song_id).count()
    did_requeue = False
    if remaining == 0 or requeue:
        song.status = SongStatus.QUEUED
        min_pos = (
            db.query(func.min(Song.queue_position))
            .filter(Song.status == SongStatus.QUEUED, Song.id != song.id)
            .scalar()
        )
        song.queue_position = (min_pos - 1) if min_pos is not None else 0
        did_requeue = True

    db.commit()
    return {"ok": True, "requeued": did_requeue}


@router.post("/api/songs/{song_id}/suggest-playlist-name", response_model=NameSuggestionsOut)
async def suggest_name_for_song(song_id: int, db: Session = Depends(get_db)):
    """Powers the 'Create New Playlist' option in the Review Queue - suggests
    names for a brand-new playlist built around just this one song."""
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")

    existing_names = [p.name for p in db.query(Playlist).all()]
    profile = song.as_sonic_profile()
    suggestions = await suggest_playlist_names(
        profile, [{"name": song.name, "artists": song.artists}], existing_names=existing_names
    )
    return {"suggestions": suggestions}
