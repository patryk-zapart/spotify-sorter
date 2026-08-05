"""Audio feature / mood inference layer.

Spotify no longer provides `/audio-features` or `/audio-analysis` to new
apps (deprecated Nov 2024, reinforced Feb 2026), so BPM/key/energy/
danceability/valence can't be pulled directly from Spotify for this project.

This module defines a small provider interface so the *source* of that data
is swappable without touching any calling code:

  - OpenAIAudioFeaturesProvider (default): asks an LLM to estimate the
    features from track/artist/album/genre context. It's an estimate, not a
    measurement, but it's honest, cheap, and works today for any new app.
  - SpotifyAudioFeaturesProvider: calls the real endpoint. Only useful if
    your app happens to hold a pre-Nov-2024 grandfathered extension; will
    raise if Spotify 403s.

Selected via the AUDIO_FEATURES_PROVIDER env var.
"""
from abc import ABC, abstractmethod
import json

from openai import AsyncOpenAI

from app.config import get_settings
from app.spotify_client import SpotifyClient, SpotifyAPIError

settings = get_settings()


MAX_GENRES_PER_SONG = 4
MAX_VIBE_TAGS_PER_SONG = 4

# Perceived pace/feel - a human-readable descriptor shown in the UI
# alongside the exact BPM. NOT used by fit_score.py's scoring math anymore
# (that uses a Gaussian gradient on the exact BPM difference instead - see
# fit_score.py's module docstring) since a smooth, continuous BPM
# comparison was judged more useful there than a 3-bucket category lookup.
TEMPO_CHARACTER_VOCAB = ["slow/ambient", "mid-tempo/groove", "fast/driving"]

# Broader thematic/cinematic descriptors, distinct from genre (style) and
# mood (a fixed 8-value emotional category). This is what lets an "epic"
# playlist meaningfully combine symphonic hip-hop and stadium rock even
# though their genre tags don't overlap at all - free-form, not a fixed
# vocabulary, same reasoning as genres (see fit_score.py's fuzzy matching).
VIBE_TAG_EXAMPLES = [
    "epic", "cinematic", "orchestral", "anthemic", "brass-heavy", "dramatic",
    "triumphant", "nostalgic", "dreamy", "gritty", "moody", "intimate",
    "playful", "menacing", "sultry",
]


class AudioFeatures:
    def __init__(self, tempo, tempo_character, key, mode, energy, danceability, valence, mood,
                 aggression_darkness_index, genres: list[str], vibe_tags: list[str], source: str):
        self.tempo = tempo  # display/reference; ALSO the primary tempo scoring signal - see fit_score.py
        self.tempo_character = tempo_character  # display only, not used in scoring
        self.key = key
        self.mode = mode
        self.energy = energy
        self.danceability = danceability
        self.valence = valence
        self.mood = mood
        # HIDDEN (0-100) - never exposed via the API or a UI slider. See
        # fit_score.py's module docstring for why this exists: a
        # categorical mood like "energetic" doesn't say whether that
        # energy is joyful or aggressive; this disambiguates it.
        self.aggression_darkness_index = aggression_darkness_index
        self.genres = genres[:MAX_GENRES_PER_SONG]  # ordered most -> least dominant
        self.vibe_tags = vibe_tags[:MAX_VIBE_TAGS_PER_SONG]
        self.source = source


class AudioFeaturesProvider(ABC):
    @abstractmethod
    async def analyze(self, track: dict, candidate_genres: list[str]) -> AudioFeatures:
        """track: {name, artists: [str], album}. candidate_genres: from Spotify artist lookup."""
        ...


MOOD_VOCAB = ["energetic", "euphoric", "chill", "melancholic", "aggressive", "romantic", "dark", "uplifting"]

# Vibe tags that indicate cinematic/orchestral scale - used as a deterministic
# safety net (see _reconcile_mood_with_arrangement) beneath the prompt's own
# instruction, since models don't always follow instructions perfectly.
CINEMATIC_SCALE_CLUSTER = {"orchestral", "cinematic", "anthemic", "brass-heavy", "dramatic", "epic"}


def _fuzzy_tag_in(tag: str, cluster: set[str]) -> bool:
    """Loose substring/case-insensitive 'is this the same idea' matching,
    used only for this module's own deterministic mood/arrangement
    contradiction check below - unrelated to (and simpler than) the
    embeddings-based semantic similarity fit_score.py now uses for
    genre/vibe scoring, which needs a continuous 0-1 measure rather than a
    plain yes/no membership test.
    """
    t = tag.lower().strip()
    return any(t == c or t in c or c in t for c in cluster)


def _reconcile_mood_with_arrangement(mood: str, vibe_tags: list[str], energy: float) -> str:
    """Deterministic safety net beneath the prompt's own instruction: if
    'chill' mood is assigned to a track ALSO tagged with cinematic/
    orchestral/brass-heavy/anthemic vibe, that's a direct contradiction -
    exactly the failure mode where a track like Little Simz's "Introvert"
    (heavy brass, marching drums, cinematic build) gets wrongly tagged
    "chill" purely because of its introspective title. Re-derive mood from
    energy instead of trusting a self-contradictory result.
    """
    has_cinematic_scale = any(_fuzzy_tag_in(tag, CINEMATIC_SCALE_CLUSTER) for tag in (vibe_tags or []))
    if mood == "chill" and has_cinematic_scale:
        return "euphoric" if energy >= 0.6 else "uplifting"
    return mood


def _mood_from_valence_energy(valence: float, energy: float) -> str:
    """Fallback categorical mood mapping if the model omits it."""
    if energy >= 0.7 and valence >= 0.6:
        return "euphoric"
    if energy >= 0.7 and valence < 0.4:
        return "aggressive"
    if energy < 0.4 and valence < 0.4:
        return "melancholic"
    if energy < 0.4 and valence >= 0.6:
        return "chill"
    return "uplifting" if valence >= 0.5 else "dark"


def _tempo_character_from_bpm(bpm: float | None) -> str:
    """Crude literal-BPM fallback for when tempo_character is missing (the
    model omitted it, or the non-LLM Spotify provider path, which has no
    way to judge PERCEIVED feel vs a raw number at all). This is exactly
    the kind of naive mapping tempo_character is meant to improve on - a
    half-time or double-time track will get miscategorized here - so it's
    only used as a last resort, never the primary source.
    """
    if bpm is None:
        return "mid-tempo/groove"
    if bpm < 90:
        return "slow/ambient"
    if bpm < 130:
        return "mid-tempo/groove"
    return "fast/driving"


def _clamp_0_100(value: float) -> float:
    return max(0.0, min(100.0, value))


def _aggression_darkness_from_energy_valence(energy: float, valence: float) -> float:
    """Fallback heuristic for aggression_darkness_index when the model
    omits it, or for the non-LLM Spotify provider path (which has no LLM
    call to ask at all): high energy + low valence reads as
    aggressive/dark, the same (arousal * (1 - valence)) logic
    fit_score.py's _aggression_proxy_from_mood uses for the fixed mood
    vocabulary - duplicated here rather than imported, since that lives in
    fit_score.py which this module doesn't otherwise depend on.
    """
    return _clamp_0_100(energy * (1 - valence) * 100)


class OpenAIAudioFeaturesProvider(AudioFeaturesProvider):
    def __init__(self):
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def analyze(self, track: dict, candidate_genres: list[str]) -> AudioFeatures:
        artist_names = ", ".join(track["artists"])
        genre_hint = ", ".join(candidate_genres) if candidate_genres else "unknown"

        system_prompt = (
            "You are a professional music analyst with deep knowledge of genres, tempo, "
            "arrangement, and the sonic/emotional character of songs. Given a track's title, "
            "artist(s), album, and known artist genre tags, estimate its audio characteristics "
            "as best you can from what a knowledgeable listener - someone who has actually "
            "heard how this artist and this kind of track typically sound - would expect.\n\n"
            "CRITICAL: base mood, energy, and vibe on the track's actual ARRANGEMENT AND "
            "PRODUCTION SCALE - live brass sections, marching or tribal drums, cinematic "
            "strings, choir, dynamic builds, wall-of-sound orchestration - not on the "
            "connotation of the title. A title like 'Introvert', 'Quiet', or 'Silent' does "
            "NOT mean the track sounds quiet, sparse, or chill - plenty of the most sonically "
            "massive, orchestral, cinematic tracks in hip-hop and alternative music carry "
            "introspective, ironic, or understated titles. Judge the sound the artist actually "
            "made, not the word in the title.\n\n"
            "STRICT RULE: never assign the mood 'chill', or genre/vibe tags like 'lo-fi', "
            "'relaxed', or 'mellow', to a track that features heavy orchestral brass, "
            "aggressive or anthemic percussion, or a high-stakes cinematic build/progression - "
            "regardless of what the title implies. Reserve 'chill' and similar tags for tracks "
            "that are genuinely sonically sparse and low-intensity in their actual arrangement.\n\n"
            "Respond ONLY with JSON, no prose, matching exactly this shape:\n"
            f'{{"genres": [1 to {MAX_GENRES_PER_SONG} strings, ordered most to least dominant - '
            'most songs genuinely span more than one genre, so include every genre that '
            'meaningfully applies, not just a single label], '
            f'"vibe_tags": [0 to {MAX_VIBE_TAGS_PER_SONG} strings - broader thematic/cinematic/'
            'emotional descriptors distinct from genre and from mood below, e.g. '
            f'{VIBE_TAG_EXAMPLES}. These are what let genre-different songs feel like they belong '
            'together (a symphonic hip-hop track and a stadium-rock anthem can both be "epic"). '
            'If the arrangement genuinely has orchestral/brass/choir/dramatic-build scale, say '
            'so here explicitly (e.g. "orchestral", "brass-heavy", "dramatic") even if the '
            'title or lyrical theme is introspective. Only include ones that genuinely apply - '
            'empty is fine if none clearly do], '
            f'"tempo_character": one of {TEMPO_CHARACTER_VOCAB} - the track\'s PERCEIVED pace and '
            'feel, not a literal reading of the BPM number (a half-time or double-time rhythm can '
            'make a track feel much slower or faster than its raw BPM suggests - judge the actual '
            'feel a listener would perceive), '
            '"tempo_bpm": number - your best literal BPM estimate, kept only as a reference figure, '
            'not used for the character judgment above, '
            '"key": str, "mode": "major"|"minor", "energy": number (0-1), '
            '"danceability": number (0-1), "valence": number (0-1), '
            f'"mood": one of {MOOD_VOCAB} - pick the closest genuine fit, don\'t default to a '
            'mismatched category just because the exact colloquial word isn\'t in the list: a '
            'bright/cheerful/"happy"/"positive" track is usually \'euphoric\' (if intense) or '
            '\'uplifting\' (if warmer/more moderate); a calm-but-positive track is \'chill\' or '
            '\'romantic\' depending on warmth vs. relaxation; a somber/"sad" track is '
            '\'melancholic\' (quieter) or \'dark\' (heavier); an intense/"angry" track is '
            f'\'aggressive\'. The goal is the best real match, not the literal presence of the '
            f'word in {MOOD_VOCAB}, '
            '"aggression_darkness_index": number (0-100) - separate from the mood label above, '
            'how aggressive/dark/menacing the track actually SOUNDS regardless of its energy '
            'level: a joyful, bright, high-energy pop-punk track scores LOW here (maybe 10-25) '
            'even though it is energetic; a genuinely hostile, distorted, threatening, or bleak '
            'track scores HIGH (70-100) even at a similar energy level. This is what separates '
            '"energetic and happy" from "energetic and aggressive" when the two could otherwise '
            'look similar on paper}'
        )
        user_prompt = (
            f"Track: {track['name']}\nArtist(s): {artist_names}\nAlbum: {track.get('album', '')}\n"
            f"Known artist genre tags: {genre_hint}"
        )

        resp = await self._client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            response_format={"type": "json_object"},
            temperature=0.4,
        )
        raw = resp.choices[0].message.content
        data = json.loads(raw)

        energy = float(data.get("energy", 0.5))
        valence = float(data.get("valence", 0.5))
        mood = data.get("mood") or _mood_from_valence_energy(valence, energy)
        if mood not in MOOD_VOCAB:
            mood = _mood_from_valence_energy(valence, energy)

        genres = [g for g in (data.get("genres") or []) if g]
        if not genres:
            genres = candidate_genres[:MAX_GENRES_PER_SONG] or ["unknown"]
        vibe_tags = [v for v in (data.get("vibe_tags") or []) if v]
        mood = _reconcile_mood_with_arrangement(mood, vibe_tags, energy)

        tempo_bpm = float(data.get("tempo_bpm", 120))
        tempo_character = data.get("tempo_character")
        if tempo_character not in TEMPO_CHARACTER_VOCAB:
            tempo_character = _tempo_character_from_bpm(tempo_bpm)

        aggression_darkness_index = data.get("aggression_darkness_index")
        if aggression_darkness_index is None:
            aggression_darkness_index = _aggression_darkness_from_energy_valence(energy, valence)
        else:
            aggression_darkness_index = _clamp_0_100(float(aggression_darkness_index))

        return AudioFeatures(
            tempo=tempo_bpm,
            tempo_character=tempo_character,
            key=data.get("key", "C"),
            mode=data.get("mode", "major"),
            energy=energy,
            danceability=float(data.get("danceability", 0.5)),
            valence=valence,
            mood=mood,
            aggression_darkness_index=aggression_darkness_index,
            genres=genres,
            vibe_tags=vibe_tags,
            source="openai",
        )


class SpotifyAudioFeaturesProvider(AudioFeaturesProvider):
    """Only works for apps grandfathered in before Nov 27, 2024."""

    def __init__(self, spotify_client: SpotifyClient):
        self._client = spotify_client

    async def analyze(self, track: dict, candidate_genres: list[str]) -> AudioFeatures:
        try:
            data = await self._client._get(f"/audio-features/{track['id']}")
        except SpotifyAPIError as e:
            raise SpotifyAPIError(
                "Spotify /audio-features is deprecated for apps created after Nov 27, 2024. "
                "Switch AUDIO_FEATURES_PROVIDER to 'openai' in your .env."
            ) from e
        valence = data.get("valence", 0.5)
        energy = data.get("energy", 0.5)
        bpm = data.get("tempo")
        keys = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        return AudioFeatures(
            tempo=bpm,
            tempo_character=_tempo_character_from_bpm(bpm),
            key=keys[data.get("key", 0)] if data.get("key", -1) >= 0 else None,
            mode="major" if data.get("mode") == 1 else "minor",
            energy=energy,
            danceability=data.get("danceability"),
            valence=valence,
            mood=_mood_from_valence_energy(valence, energy),
            aggression_darkness_index=_aggression_darkness_from_energy_valence(energy, valence),
            genres=candidate_genres[:MAX_GENRES_PER_SONG] or ["unknown"],
            vibe_tags=[],  # no LLM call in this path, so no honest source for this signal
            source="spotify",
        )


def get_audio_features_provider(spotify_client: SpotifyClient | None = None) -> AudioFeaturesProvider:
    if settings.audio_features_provider == "spotify":
        if spotify_client is None:
            raise ValueError("spotify_client is required for the 'spotify' audio features provider")
        return SpotifyAudioFeaturesProvider(spotify_client)
    return OpenAIAudioFeaturesProvider()
