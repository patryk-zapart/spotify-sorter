import datetime as dt
import enum

from sqlalchemy import (
    String, Integer, Float, DateTime, ForeignKey, Text, Enum, JSON, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.fit_score import GENRE_POSITION_WEIGHTS, DEFAULT_WEIGHTS


class SongStatus(str, enum.Enum):
    QUEUED = "queued"      # imported, waiting for review
    ASSIGNED = "assigned"  # belongs to at least one playlist
    SKIPPED = "skipped"    # set aside during review; excluded from the queue until requeued


class TagEmbedding(Base):
    """Cache of OpenAI embedding vectors, one row per unique (normalized)
    genre/vibe tag string - see app/embeddings.py. The same tag strings
    recur constantly across songs, so caching avoids re-fetching the same
    vector on every scoring pass."""

    __tablename__ = "tag_embeddings"

    tag: Mapped[str] = mapped_column(String(128), primary_key=True)  # lowercase, stripped
    embedding: Mapped[list[float]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class Playlist(Base):
    """A local playlist record, mirrored 1:1 with a real Spotify playlist."""

    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    spotify_playlist_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    # OpenAI's structured read of `description` (genres/vibe/mood/tempo
    # character/energy it implies), cached so scoring never needs a live
    # API call - see openai_client.interpret_playlist_description() and
    # routers/playlists.py's PUT /{id}/description.
    description_signal: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # OpenAI's structured read of the playlist's own NAME (vibe tags, implied
    # mood/energy/genre) - e.g. "Epic Uplifting Anthems" implies vibe_tags
    # like ["epic", "uplifting"]. Cached at create/adopt time; used directly
    # by fit_score.py's title-vibe scoring (NOT blended into the aggregate
    # profile the way description_signal is - a title is a fixed target to
    # match against, not evidence to average into the collective vibe).
    title_signal: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Active per-playlist scoring weights across the four fit-score
    # categories (theme_vibe, genre_style, emotional_tone, tempo_energy) -
    # sum to 1.0. Initialized from an AI suggestion based on the playlist's
    # title/description (see openai_client.suggest_scoring_weights) at
    # create/adopt time, then freely editable via sliders in the UI
    # (PUT /api/playlists/{id}/weights). A workout playlist's AI suggestion
    # leans toward tempo_energy; a study/ambient one leans toward
    # theme_vibe/emotional_tone.
    weights: Mapped[dict] = mapped_column(JSON, default=lambda: dict(DEFAULT_WEIGHTS))
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    memberships: Mapped[list["PlaylistSong"]] = relationship(
        back_populates="playlist", cascade="all, delete-orphan"
    )

    @property
    def songs(self) -> list["Song"]:
        return [m.song for m in self.memberships]

    def sonic_profile(self, exclude_song_id: int | None = None) -> dict:
        """Aggregate sonic profile computed live from current REAL member
        songs ONLY - a pure members-only aggregate. `title_signal` and
        `description_signal` are passed through unblended alongside it;
        fit_score.py's compute_fit_score() is what combines all three into
        a Composite Target Profile (40% description / 30% title / 30%
        members), NOT this method - see fit_score.py's module docstring for
        why the description acting as the dominant anchor, and the
        "echo chamber" drift this guards against (member songs that have
        quietly drifted from the playlist's own written premise), belong
        at the scoring layer rather than being pre-blended into the
        aggregate here.

        Used both to display playlist stats and as one input to
        fit-score calculations against songs still in the queue.

        `exclude_song_id` leaves one REAL song out of the aggregate - used
        by "Check My Playlists" so a song's fit is judged against the rest
        of the playlist, not against a profile that already includes its
        own attributes.

        `avg_tempo` feeds fit_score.py's Gaussian BPM gradient directly now
        (a smooth mathematical falloff on the exact BPM difference, not a
        discrete tempo-character lookup) - `tempo_character` per song is
        still recorded and shown in the UI as a human-readable descriptor,
        it's just not consumed by scoring math anymore.

        `avg_aggression_darkness` is the average of member songs' HIDDEN
        aggression_darkness_index (see audio_features.py) - never exposed
        via the API, used only internally by fit_score.py's
        emotional_tone scoring to catch cases a categorical mood label
        alone might miss (a high-energy "energetic" song could be joyful
        OR aggressive - this disambiguates it).

        `song_count` counts only REAL member songs (for accurate
        "12 tracks" style display) - `has_signal` separately tells callers
        whether there's ANY basis to score against (real songs, a usable
        title, a usable description, or any combination), which is what
        scoring code and the UI should actually gate on instead of
        `song_count > 0`.
        """
        songs = self.songs
        if exclude_song_id is not None:
            songs = [s for s in songs if s.id != exclude_song_id]
        real_song_count = len(songs)

        has_signal = bool(songs) or bool(self.title_signal) or bool(self.description_signal)

        if not songs:
            return {
                "avg_tempo": None, "avg_energy": None, "avg_danceability": None,
                "avg_valence": None, "avg_aggression_darkness": None,
                "genre_distribution": {}, "vibe_distribution": {},
                "mood_distribution": {}, "tempo_character_distribution": {},
                "song_count": 0, "has_signal": has_signal,
                "title_signal": self.title_signal, "description_signal": self.description_signal,
                "weights": self.weights or dict(DEFAULT_WEIGHTS),
            }

        def avg(attr):
            vals = [getattr(s, attr) for s in songs if getattr(s, attr) is not None]
            return sum(vals) / len(vals) if vals else None

        genre_counts: dict[str, float] = {}
        vibe_counts: dict[str, float] = {}
        mood_counts: dict[str, float] = {}
        tempo_character_counts: dict[str, float] = {}
        for s in songs:
            for genre, pos_weight in zip(s.genres or [], GENRE_POSITION_WEIGHTS):
                genre_counts[genre] = genre_counts.get(genre, 0) + pos_weight
            for vibe, pos_weight in zip(getattr(s, "vibe_tags", None) or [], GENRE_POSITION_WEIGHTS):
                vibe_counts[vibe] = vibe_counts.get(vibe, 0) + pos_weight
            if s.mood:
                mood_counts[s.mood] = mood_counts.get(s.mood, 0) + 1
            tc = getattr(s, "tempo_character", None)
            if tc:
                tempo_character_counts[tc] = tempo_character_counts.get(tc, 0) + 1

        return {
            "avg_tempo": avg("tempo"),
            "avg_energy": avg("energy"),
            "avg_danceability": avg("danceability"),
            "avg_valence": avg("valence"),
            "avg_aggression_darkness": avg("aggression_darkness_index"),
            "genre_distribution": genre_counts,
            "vibe_distribution": vibe_counts,
            "mood_distribution": mood_counts,
            "tempo_character_distribution": tempo_character_counts,
            "song_count": real_song_count,
            "has_signal": has_signal,
            "title_signal": self.title_signal,
            "description_signal": self.description_signal,
            "weights": self.weights or dict(DEFAULT_WEIGHTS),
        }


class Song(Base):
    __tablename__ = "songs"

    id: Mapped[int] = mapped_column(primary_key=True)
    spotify_track_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(512))
    artists: Mapped[str] = mapped_column(String(512))  # comma-separated display names
    album: Mapped[str] = mapped_column(String(512), default="")
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Analysis fields ---
    genres: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=list)  # ordered most -> least dominant
    vibe_tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=list)  # e.g. "epic", "cinematic"
    tempo: Mapped[float | None] = mapped_column(Float, nullable=True)          # BPM - display/reference ONLY, see fit_score.py
    tempo_character: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "slow/ambient" etc - see audio_features.py
    key: Mapped[str | None] = mapped_column(String(8), nullable=True)          # e.g. "C#", "Bb"
    mode: Mapped[str | None] = mapped_column(String(8), nullable=True)         # major / minor
    energy: Mapped[float | None] = mapped_column(Float, nullable=True)         # 0-1
    danceability: Mapped[float | None] = mapped_column(Float, nullable=True)   # 0-1
    valence: Mapped[float | None] = mapped_column(Float, nullable=True)        # 0-1 (used to derive mood)
    mood: Mapped[str | None] = mapped_column(String(32), nullable=True)        # e.g. energetic, chill
    # HIDDEN sub-metric (0-100) - never exposed via the API or a UI slider.
    # Disambiguates cases the categorical `mood` field alone can't: an
    # "energetic" song could be joyful OR aggressive; this says which. Used
    # only internally by fit_score.py's emotional_tone scoring and its
    # genre fusion-tolerance check. See audio_features.py.
    aggression_darkness_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    analysis_source: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "openai" | "spotify"

    # --- Workflow fields ---
    status: Mapped[SongStatus] = mapped_column(Enum(SongStatus), default=SongStatus.QUEUED)
    queue_position: Mapped[int] = mapped_column(Integer, default=0)
    source_playlist_id: Mapped[str] = mapped_column(String(64))  # spotify playlist id it was imported from
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    memberships: Mapped[list["PlaylistSong"]] = relationship(
        back_populates="song", cascade="all, delete-orphan"
    )

    def as_sonic_profile(self) -> dict:
        """Represents this single song in the same shape as
        Playlist.sonic_profile(), so it can feed the OpenAI name-suggestion
        call when creating a brand-new playlist around just this one track.
        """
        genre_dist = {
            genre: weight for genre, weight in zip(self.genres or [], GENRE_POSITION_WEIGHTS)
        }
        vibe_dist = {
            vibe: weight for vibe, weight in zip(self.vibe_tags or [], GENRE_POSITION_WEIGHTS)
        }
        mood_dist = {self.mood: 1} if self.mood else {}
        tempo_character_dist = {self.tempo_character: 1} if self.tempo_character else {}
        return {
            "avg_tempo": self.tempo,
            "avg_energy": self.energy,
            "avg_danceability": self.danceability,
            "avg_valence": self.valence,
            "avg_aggression_darkness": self.aggression_darkness_index,
            "genre_distribution": genre_dist,
            "vibe_distribution": vibe_dist,
            "mood_distribution": mood_dist,
            "tempo_character_distribution": tempo_character_dist,
            "song_count": 1,
        }


class PlaylistSong(Base):
    """Membership of a song in a playlist (many-to-many) plus the fit score
    the song had against that specific playlist at the time it was added."""

    __tablename__ = "playlist_songs"
    __table_args__ = (UniqueConstraint("playlist_id", "song_id", name="uq_playlist_song"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id"), index=True)
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id"), index=True)
    fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fit_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    assigned_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    playlist: Mapped["Playlist"] = relationship(back_populates="memberships")
    song: Mapped["Song"] = relationship(back_populates="memberships")
