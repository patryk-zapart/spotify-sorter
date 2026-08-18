from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Playlist, PlaylistSong, Song, SongStatus
from app.schemas import (
    PlaylistOut, PlaylistCreate, AdoptRequest, NameSuggestionsOut, song_out_dict,
    PlaylistCheckOut, UpdateDescriptionRequest, ExplainFitOut, PlaylistWeights,
)
from app import spotify_client
from app.audio_features import get_audio_features_provider
from app.track_ingest import get_or_create_song
from app.embeddings import fetch_embeddings_for
from app.openai_client import (
    suggest_playlist_names, interpret_playlist_description, interpret_playlist_title,
    suggest_scoring_weights, explain_fit_score,
)
from app.fit_score import compute_fit_score, detect_playlist_drift
from app.routers.auth import get_valid_access_token

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


def _serialize(playlist: Playlist, sync_note: str | None = None) -> dict:
    songs = [
        song_out_dict(m.song, fit_score=m.fit_score)
        for m in sorted(playlist.memberships, key=lambda m: m.assigned_at)
    ]
    profile = playlist.sonic_profile()
    is_drifting, drift_comment = detect_playlist_drift(profile, playlist.title_signal, playlist.description_signal)

    return {
        "id": playlist.id,
        "spotify_playlist_id": playlist.spotify_playlist_id,
        "name": playlist.name,
        "description": playlist.description,
        "image_url": playlist.image_url,
        "songs": songs,
        "sonic_profile": profile,
        "weights": playlist.weights,
        "is_drifting": is_drifting,
        "drift_comment": drift_comment,
        "sync_note": sync_note,
    }


async def _sync_existing_tracks(playlist: Playlist, client: spotify_client.SpotifyClient, db: Session) -> int:
    """Pulls in a playlist's EXISTING tracks on adoption, so its sonic
    profile reflects real content immediately instead of starting blank
    (which otherwise makes every fit score default to a flat, meaningless
    50 - see fit_score.py's neutral fallbacks). Only works for playlists the
    user owns or collaborates on (Spotify's Feb 2026 restriction); raises
    SpotifyAPIError otherwise, which the caller handles gracefully."""
    tracks = await client.get_playlist_tracks(playlist.spotify_playlist_id)
    if not tracks:
        return 0

    provider = get_audio_features_provider(client)
    genre_cache: dict[str, list[str]] = {}
    added = 0
    for track in tracks:
        song, _ = await get_or_create_song(
            track, client, provider, genre_cache, db,
            source_playlist_id=playlist.spotify_playlist_id,
            status=SongStatus.ASSIGNED,
        )
        existing_membership = (
            db.query(PlaylistSong).filter_by(playlist_id=playlist.id, song_id=song.id).first()
        )
        if not existing_membership:
            # No fit_score here on purpose: this song was already in the
            # playlist, not matched there by our algorithm.
            db.add(PlaylistSong(playlist_id=playlist.id, song_id=song.id))
            added += 1
    return added


@router.get("", response_model=list[PlaylistOut])
def list_playlists(db: Session = Depends(get_db)):
    playlists = db.query(Playlist).order_by(Playlist.created_at).all()
    return [_serialize(p) for p in playlists]


@router.post("", response_model=PlaylistOut)
async def create_playlist(payload: PlaylistCreate, request: Request, db: Session = Depends(get_db)):
    try:
        access_token = await get_valid_access_token(request)
    except PermissionError:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify. Log in first.")

    client = spotify_client.SpotifyClient(access_token)

    try:
        sp_playlist = await client.create_playlist(payload.name, payload.description, payload.make_public)
    except spotify_client.SpotifyAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    images = sp_playlist.get("images") or []
    playlist = Playlist(
        spotify_playlist_id=sp_playlist["id"],
        name=payload.name,
        description=payload.description,
        image_url=images[0]["url"] if images else None,
        title_signal=await interpret_playlist_title(payload.name),
        weights=await suggest_scoring_weights(payload.name, payload.description),
    )
    db.add(playlist)
    db.commit()
    db.refresh(playlist)
    return _serialize(playlist)


@router.post("/adopt", response_model=PlaylistOut)
async def adopt_playlist(payload: AdoptRequest, request: Request, db: Session = Depends(get_db)):
    """Registers an EXISTING Spotify playlist (picked from 'Load My
    Playlists') as a local sorting destination, without creating a new
    playlist on Spotify. Idempotent - adopting an already-tracked playlist
    just returns it. Also attempts to sync in the playlist's existing
    tracks so fit scores against it are meaningful right away."""
    existing = db.query(Playlist).filter_by(spotify_playlist_id=payload.spotify_playlist_id).first()
    if existing:
        return _serialize(existing)

    try:
        access_token = await get_valid_access_token(request)
    except PermissionError:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify. Log in first.")

    client = spotify_client.SpotifyClient(access_token)
    try:
        sp_playlist = await client.get_playlist(payload.spotify_playlist_id)
    except spotify_client.SpotifyAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    images = sp_playlist.get("images") or []
    playlist_name = sp_playlist.get("name") or "Untitled"
    playlist_description = sp_playlist.get("description") or ""
    playlist = Playlist(
        spotify_playlist_id=sp_playlist["id"],
        name=playlist_name,
        description=playlist_description,
        image_url=images[0]["url"] if images else None,
        title_signal=await interpret_playlist_title(playlist_name),
        weights=await suggest_scoring_weights(playlist_name, playlist_description),
    )
    db.add(playlist)
    db.flush()

    sync_note = None
    try:
        added = await _sync_existing_tracks(playlist, client, db)
        if added == 0:
            sync_note = "No existing tracks found to bring in - its match score will start neutral."
    except spotify_client.SpotifyAPIError:
        sync_note = (
            "Added, but Spotify won't let this app read its existing tracks (only allowed for "
            "playlists you own or collaborate on) - its match score will start neutral until you "
            "assign new songs to it."
        )

    db.commit()
    db.refresh(playlist)
    return _serialize(playlist, sync_note=sync_note)


@router.delete("/{playlist_id}")
def delete_playlist(playlist_id: int, db: Session = Depends(get_db)):
    """Removes a playlist from the local Library/sorter only. This does NOT
    delete or unfollow the real playlist on Spotify, and does not remove any
    tracks from it - it just stops this app from tracking it. Songs that
    were only assigned here (nowhere else) go back to the review queue
    instead of disappearing."""
    playlist = db.get(Playlist, playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    # Grab affected song ids before the cascade delete removes the
    # membership rows.
    affected_song_ids = [m.song_id for m in playlist.memberships]

    db.delete(playlist)  # cascades to PlaylistSong rows (see models.py)
    db.flush()

    requeued = 0
    for song_id in affected_song_ids:
        remaining = db.query(PlaylistSong).filter_by(song_id=song_id).count()
        if remaining == 0:
            song = db.get(Song, song_id)
            if song and song.status == SongStatus.ASSIGNED:
                song.status = SongStatus.QUEUED
                min_pos = (
                    db.query(func.min(Song.queue_position))
                    .filter(Song.status == SongStatus.QUEUED, Song.id != song.id)
                    .scalar()
                )
                song.queue_position = (min_pos - 1) if min_pos is not None else 0
                requeued += 1

    db.commit()
    return {"ok": True, "requeued_songs": requeued}


@router.get("/{playlist_id}/check", response_model=PlaylistCheckOut)
async def check_playlist(playlist_id: int, db: Session = Depends(get_db)):
    """Powers 'Check My Playlists': re-scores every song currently in a
    playlist against the playlist's CURRENT profile, so drift becomes
    visible (a song added early on may no longer fit as well once the
    playlist's average tempo/genre/mood has shifted from later additions).

    Each song is scored leave-one-out - against the rest of the playlist,
    excluding itself - so its own attributes don't inflate the very average
    it's being judged against. Genre/vibe embeddings for every song in the
    playlist are fetched in ONE batched call up front (falling back to
    cached vectors wherever possible - see app/embeddings.py), so this
    stays fast even for larger playlists despite now doing semantic
    similarity instead of pure local string matching.
    """
    playlist = db.get(Playlist, playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    memberships = sorted(playlist.memberships, key=lambda m: m.assigned_at)
    full_profile = playlist.sonic_profile()  # NOT leave-one-out - used only for the drift check below
    profiles = [playlist.sonic_profile(exclude_song_id=m.song_id) for m in memberships]
    embeddings = await fetch_embeddings_for([m.song for m in memberships], profiles, db)
    db.commit()  # persist any newly-cached embedding vectors

    is_drifting, drift_comment = detect_playlist_drift(
        full_profile, playlist.title_signal, playlist.description_signal
    )

    rows = []
    for m, profile_without_song in zip(memberships, profiles):
        live = compute_fit_score(m.song, profile_without_song, embeddings)
        rows.append({
            "song": song_out_dict(m.song, fit_score=live["score"]),
            "live_score": live["score"],
            "live_breakdown": live["breakdown"],
            "live_explanation": live["explanation"],
            "original_score": m.fit_score,
        })

    return {
        "playlist_id": playlist.id,
        "playlist_name": playlist.name,
        "playlist_image_url": playlist.image_url,
        "is_drifting": is_drifting,
        "drift_comment": drift_comment,
        "songs": rows,
    }


@router.put("/{playlist_id}/description", response_model=PlaylistOut)
async def update_description(
    playlist_id: int, payload: UpdateDescriptionRequest, request: Request, db: Session = Depends(get_db)
):
    """Edits a playlist's description - writes it to the real Spotify
    playlist, then (re)interprets it into cached sonic hints
    (description_signal) that feed fit-score matching going forward. Only
    works if you own or collaborate on the playlist (Spotify's normal
    write-permission model - same rule that governs adding tracks)."""
    playlist = db.get(Playlist, playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    try:
        access_token = await get_valid_access_token(request)
    except PermissionError:
        raise HTTPException(status_code=401, detail="Not authenticated with Spotify. Log in first.")
    client = spotify_client.SpotifyClient(access_token)

    try:
        await client.update_playlist_details(playlist.spotify_playlist_id, payload.description)
    except spotify_client.SpotifyAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    playlist.description = payload.description
    playlist.description_signal = await interpret_playlist_description(payload.description)
    db.commit()
    db.refresh(playlist)
    return _serialize(playlist)


@router.put("/{playlist_id}/weights", response_model=PlaylistOut)
def update_weights(playlist_id: int, payload: PlaylistWeights, db: Session = Depends(get_db)):
    """Edits a playlist's active scoring weights - powers the sliders on
    playlist cards and Check My Playlists. Initialized from an AI
    suggestion at create/adopt time (see suggest_scoring_weights), this
    lets a person freely override that starting point. Weights don't need
    to arrive summing to exactly 1.0 - normalized here so what's stored is
    always clean, and compute_fit_score normalizes again defensively at
    score time regardless. A local-only change (no Spotify call, no OpenAI
    call) - it takes effect on every future score for this playlist,
    everywhere it's scored (Review Queue, Check My Playlists, etc.)."""
    playlist = db.get(Playlist, playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    raw = payload.model_dump()
    if any(v < 0 for v in raw.values()):
        raise HTTPException(status_code=400, detail="Weights can't be negative.")
    total = sum(raw.values())
    if total <= 0:
        raise HTTPException(status_code=400, detail="At least one weight must be greater than zero.")

    playlist.weights = {k: v / total for k, v in raw.items()}
    db.commit()
    db.refresh(playlist)
    return _serialize(playlist)


@router.get("/{playlist_id}/songs/{song_id}/explain", response_model=ExplainFitOut)
async def explain_song_fit(playlist_id: int, song_id: int, db: Session = Depends(get_db)):
    """On-demand, plain-English 'why?' for one song's fit against one
    playlist - powers the 'Explain further' button in Check My Playlists.
    Deliberately separate from GET /{id}/check (which only ever needs an
    OpenAI call for a genre/vibe tag it hasn't embedded before - everything
    else, including this endpoint's own numeric score, is cached/local) so
    the cost of generating actual PROSE only applies to a song someone
    explicitly asks about, never automatically for a whole playlist."""
    playlist = db.get(Playlist, playlist_id)
    song = db.get(Song, song_id)
    if not playlist or not song:
        raise HTTPException(status_code=404, detail="Playlist or song not found")

    profile = playlist.sonic_profile(exclude_song_id=song_id)
    embeddings = await fetch_embeddings_for([song], [profile], db)
    db.commit()  # persist any newly-cached embedding vectors

    fit = compute_fit_score(song, profile, embeddings)
    explanation = await explain_fit_score(song, profile, fit)
    return {"explanation": explanation, "score": fit["score"], "breakdown": fit["breakdown"]}


@router.post("/{playlist_id}/suggest-names", response_model=NameSuggestionsOut)
async def suggest_names(playlist_id: int, db: Session = Depends(get_db)):
    playlist = db.get(Playlist, playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    profile = playlist.sonic_profile()
    if not profile["has_signal"]:
        raise HTTPException(
            status_code=400,
            detail="Add at least one song, or write a description, before requesting name suggestions.",
        )

    sample_songs = [{"name": m.song.name, "artists": m.song.artists} for m in playlist.memberships]
    other_names = [p.name for p in db.query(Playlist).filter(Playlist.id != playlist_id).all()]
    suggestions = await suggest_playlist_names(profile, sample_songs, existing_names=other_names)
    return {"suggestions": suggestions}
