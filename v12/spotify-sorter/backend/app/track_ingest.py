"""Shared logic for turning a raw Spotify track object into a local `Song`
row. Used both when importing a playlist into the review queue
(routers/import_.py) and when syncing an adopted playlist's existing tracks
so its sonic profile isn't blank from the start (routers/playlists.py).
"""
from sqlalchemy.orm import Session

from app.models import Song


async def get_or_create_song(
    track: dict,
    client,
    provider,
    genre_cache: dict[str, list[str]],
    db: Session,
    *,
    source_playlist_id: str,
    status,
    queue_position: int = 0,
) -> tuple[Song, bool]:
    """Returns (song, created).

    If this Spotify track has already been analyzed before (from an earlier
    import or sync), the existing row is reused as-is and `status`/
    `queue_position` are ignored - created=False tells the caller not to
    re-queue or re-count it. Otherwise, artist genres are looked up (via the
    shared genre_cache) and the track is run through the audio-features
    provider before a new Song row is created.
    """
    existing = db.query(Song).filter_by(spotify_track_id=track["id"]).first()
    if existing:
        return existing, False

    artist_ids = [a["id"] for a in track.get("artists", []) if a.get("id")]
    candidate_genres: list[str] = []
    for artist_id in artist_ids[:2]:
        if artist_id not in genre_cache:
            genre_cache[artist_id] = await client.get_artist_genres(artist_id)
        for g in genre_cache[artist_id]:
            if g not in candidate_genres:  # de-dupe while preserving order across artists
                candidate_genres.append(g)

    analysis = await provider.analyze(
        {
            "id": track["id"],
            "name": track["name"],
            "artists": [a["name"] for a in track.get("artists", [])],
            "album": (track.get("album") or {}).get("name", ""),
        },
        candidate_genres,
    )

    album_images = (track.get("album") or {}).get("images") or []
    song = Song(
        spotify_track_id=track["id"],
        name=track["name"],
        artists=", ".join(a["name"] for a in track.get("artists", [])),
        album=(track.get("album") or {}).get("name", ""),
        image_url=album_images[0]["url"] if album_images else None,
        duration_ms=track.get("duration_ms"),
        genres=analysis.genres,
        vibe_tags=analysis.vibe_tags,
        tempo=analysis.tempo,
        tempo_character=analysis.tempo_character,
        key=analysis.key,
        mode=analysis.mode,
        energy=analysis.energy,
        danceability=analysis.danceability,
        valence=analysis.valence,
        mood=analysis.mood,
        aggression_darkness_index=analysis.aggression_darkness_index,
        analysis_source=analysis.source,
        status=status,
        queue_position=queue_position,
        source_playlist_id=source_playlist_id,
    )
    db.add(song)
    db.flush()
    return song, True
