from pydantic import BaseModel


class SongOut(BaseModel):
    id: int
    spotify_track_id: str
    name: str
    artists: str
    album: str
    image_url: str | None = None
    duration_ms: int | None = None
    genres: list[str] | None = None  # ordered most -> least dominant
    vibe_tags: list[str] | None = None  # e.g. "epic", "cinematic" - broader than genre/mood
    tempo: float | None = None  # display/reference only - see fit_score.py
    tempo_character: str | None = None  # "slow/ambient" etc - what scoring actually uses
    key: str | None = None
    mode: str | None = None
    energy: float | None = None
    danceability: float | None = None
    valence: float | None = None
    mood: str | None = None
    analysis_source: str | None = None
    status: str

    # Only populated by song_out_dict() below - not real columns on Song.
    fit_score: int | None = None
    member_playlist_ids: list[int] = []

    model_config = {"from_attributes": True}


def song_out_dict(song, fit_score: int | None = None) -> dict:
    """Builds a SongOut-shaped dict from an ORM Song, filling in the
    playlist-membership fields that aren't real columns on the model.

    `song.memberships` is a local SQLAlchemy relationship, already available
    from the same query - reading it here is a local lookup, not a Spotify
    API call, so this adds no meaningful latency wherever it's used
    (playlist cards, and the review queue's "already in: ..." indicator).
    """
    data = SongOut.model_validate(song).model_dump()
    data["fit_score"] = fit_score
    data["genres"] = song.genres or []
    data["vibe_tags"] = song.vibe_tags or []
    data["member_playlist_ids"] = [m.playlist_id for m in song.memberships]
    return data


class SonicProfile(BaseModel):
    avg_tempo: float | None
    avg_energy: float | None
    avg_danceability: float | None
    avg_valence: float | None
    genre_distribution: dict
    vibe_distribution: dict = {}
    mood_distribution: dict
    tempo_character_distribution: dict = {}
    song_count: int
    has_signal: bool = True


class PlaylistWeights(BaseModel):
    """Active per-playlist scoring weights across the four fit-score
    components - see fit_score.py's module docstring. Used both as the
    PlaylistOut.weights response shape and as the PUT /{id}/weights request
    body (don't need to sum to exactly 1.0 - compute_fit_score normalizes
    at score time regardless)."""
    theme_vibe: float
    genre_style: float
    emotional_tone: float
    tempo_energy: float


class PlaylistOut(BaseModel):
    id: int
    spotify_playlist_id: str
    name: str
    description: str
    image_url: str | None = None
    songs: list[SongOut] = []
    sonic_profile: SonicProfile
    weights: PlaylistWeights
    # Playlist Drift Warning - whether the accumulated member songs have
    # drifted from the title+description premise (see
    # fit_score.detect_playlist_drift), plus a short human-readable note
    # when they have. False/None for a playlist with no real songs yet.
    is_drifting: bool = False
    drift_comment: str | None = None
    # One-time informational message from adopt (e.g. "couldn't read existing
    # tracks"). None for ordinary GET /api/playlists responses.
    sync_note: str | None = None

    model_config = {"from_attributes": True}


class PlaylistCreate(BaseModel):
    name: str
    description: str = ""
    make_public: bool = False


class AdoptRequest(BaseModel):
    spotify_playlist_id: str


class SpotifyPlaylistSummary(BaseModel):
    """One row in the 'Load My Playlists' browser - live Spotify data, not
    necessarily tracked locally yet."""
    spotify_playlist_id: str
    name: str
    description: str = ""
    image_url: str | None = None
    track_count: int | None = None
    is_owner: bool = False
    collaborative: bool = False
    already_tracked: bool = False
    local_playlist_id: int | None = None


class ImportRequest(BaseModel):
    playlist_url: str


class AssignBatchRequest(BaseModel):
    """Body for the Review Queue's main assign action - one or more playlists
    at once."""
    playlist_ids: list[int]


class MembershipRequest(BaseModel):
    """Body for adding a single song/playlist membership after the fact
    (the 'add to another playlist' control on a playlist card)."""
    playlist_id: int


class FitScoreEntry(BaseModel):
    playlist_id: int
    playlist_name: str
    score: int
    breakdown: dict
    # Fast, local, template-built explanation (see fit_score._build_explanation)
    # - always present, no extra API call. Distinct from the richer on-demand
    # prose from GET /playlists/{id}/songs/{song_id}/explain.
    explanation: str
    # How many REAL songs the playlist's profile is based on.
    song_count: int
    # Whether there's ANY basis to score against - real songs, a usable
    # title, a usable description, or any combination. Use this (not
    # song_count > 0) to decide whether a score is a real match vs. a
    # meaningless neutral 50.
    has_signal: bool = True


class QueueSongOut(BaseModel):
    song: SongOut
    fit_scores: list[FitScoreEntry]
    remaining_in_queue: int


class NameSuggestionsOut(BaseModel):
    suggestions: list[str]


class PlaylistCheckSongOut(BaseModel):
    """One row in the 'Check My Playlists' view: a song's fit score
    recomputed against the REST of the playlist (leave-one-out), alongside
    what it originally scored when it was added, so drift is visible."""
    song: SongOut
    live_score: int
    live_breakdown: dict
    live_explanation: str
    original_score: int | None


class PlaylistCheckOut(BaseModel):
    playlist_id: int
    playlist_name: str
    playlist_image_url: str | None = None
    is_drifting: bool = False
    drift_comment: str | None = None
    songs: list[PlaylistCheckSongOut]


class UpdateDescriptionRequest(BaseModel):
    description: str


class ExplainFitOut(BaseModel):
    """Plain-English, on-demand explanation of why a song scored the way
    it did against a playlist - only generated when explicitly requested,
    to keep Check My Playlists itself fast and call-free."""
    explanation: str
    score: int
    breakdown: dict
