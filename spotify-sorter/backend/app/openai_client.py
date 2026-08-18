"""Calls OpenAI for five things:
  1. Playlist name suggestions from a playlist's aggregate sonic/mood
     profile (suggest_playlist_names).
  2. Interpreting a playlist's free-text description into structured sonic
     hints - genres/vibe/mood/tempo character/energy it implies - cached
     ONCE per save so it can feed scoring without a live call per song
     (interpret_playlist_description).
  3. Interpreting a playlist's own NAME the same way (e.g. "Epic Uplifting
     Anthems" -> vibe_tags ["epic", "uplifting"]) - cached at create/adopt
     time, read directly as one input to fit_score.py's Composite Target
     (interpret_playlist_title).
  4. Suggesting a starting weight distribution across the four fit-score
     components based on the playlist's title/description - a workout
     playlist shifts toward tempo_energy, a study/ambient one toward
     theme_vibe/emotional_tone (suggest_scoring_weights). Cached at
     create/adopt time as Playlist.weights, then freely editable via UI
     sliders.
  5. An on-demand, plain-English explanation of why a specific song scored
     the way it did against a playlist, generated only when explicitly
     requested (explain_fit_score).
"""
import json

from openai import AsyncOpenAI

from app.config import get_settings
from app.audio_features import MOOD_VOCAB, TEMPO_CHARACTER_VOCAB

settings = get_settings()

STYLE_EXAMPLES = [
    "Downtempo Chillout",
    "High Energy Indie-Dance & Neo-Disco",
    "Alt Melancholy Dream/Psychedelic Pop",
    "Dance/Electronic",
    "Goofy Swing",
    "Electro Jazz/Pop",
]

SYSTEM_PROMPT = (
    "You name music playlists the way real curators and streaming editors do: short, "
    "descriptive, genre-and-mood-forward names, sometimes combining two genres with '&' or '/'. "
    f"Examples of the target style: {', '.join(STYLE_EXAMPLES)}. "
    "Avoid generic names like 'My Playlist' or overly cute/emoji-laden names. "
    "If the input includes 'existing_playlist_names', treat those as playlists that already "
    "exist in this user's library - do not suggest a name that is the same idea as one of them "
    "reworded (e.g. don't suggest 'Chillout Downtempo Vibes' when 'downtempo-chillout' is already "
    "listed). If every reasonable name for this sound is already taken, prefer a slightly more "
    "specific angle (a sub-mood, an instrument, an era) over a generic rewording. "
    "Respond ONLY with JSON: {\"suggestions\": [string, ...]} containing 3 to 5 names."
)


async def suggest_playlist_names(
    sonic_profile: dict, sample_songs: list[dict], existing_names: list[str] | None = None
) -> list[str]:
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    genres = sorted(sonic_profile["genre_distribution"].items(), key=lambda kv: -kv[1])
    moods = sorted(sonic_profile["mood_distribution"].items(), key=lambda kv: -kv[1])

    profile_summary = {
        "top_genres": [g for g, _ in genres[:4]],
        "top_moods": [m for m, _ in moods[:3]],
        "avg_tempo_bpm": sonic_profile["avg_tempo"],
        "avg_energy_0_1": sonic_profile["avg_energy"],
        "avg_danceability_0_1": sonic_profile["avg_danceability"],
        "sample_tracks": [f"{s['name']} - {s['artists']}" for s in sample_songs[:8]],
        "existing_playlist_names": existing_names or [],
    }

    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(profile_summary)},
        ],
        response_format={"type": "json_object"},
        temperature=0.9,
    )
    data = json.loads(resp.choices[0].message.content)
    suggestions = data.get("suggestions", [])
    return suggestions[:5] if suggestions else ["Untitled Mix"]


DESCRIPTION_SIGNAL_PROMPT = (
    "You extract structured musical signals from a playlist's own description, written by "
    "whoever curates it. Infer what genres, broader thematic/cinematic vibe tags (e.g. 'epic', "
    "'cinematic', 'anthemic', 'nostalgic', 'dreamy', 'gritty' - distinct from genre and from mood), "
    "mood, tempo character, energy, and danceability the description implies - even loosely (e.g. "
    "'gym hype only' implies high energy, tempo_character 'fast/driving', and vibe tags like "
    "'intense'/'motivational'; 'rainy day reading' implies chill, tempo_character 'slow/ambient', "
    "low energy, vibe tags like 'cozy'/'intimate'; a description with no sonic implication at all "
    "- blank, just a date, an unrelated joke - implies nothing, and every field should come back "
    "null/empty in that case; don't invent signal that isn't there). Respond ONLY with JSON "
    'matching exactly this shape: {"genres": [0 to 4 strings], "vibe_tags": [0 to 4 strings], '
    f'"moods": [0 to 2 strings, each one of {MOOD_VOCAB}], '
    f'"tempo_character": null or one of {TEMPO_CHARACTER_VOCAB}, '
    '"tempo_bpm": number|null, "energy": number 0-1|null, "danceability": number 0-1|null}'
)


def _parse_text_signal(data: dict) -> dict | None:
    """Shared parsing/validation for both description- and title-signal
    responses, which share the exact same JSON shape."""
    genres = [g for g in (data.get("genres") or []) if g][:4]
    vibe_tags = [v for v in (data.get("vibe_tags") or []) if v][:4]
    moods = [m for m in (data.get("moods") or []) if m in MOOD_VOCAB][:2]
    tempo_character = data.get("tempo_character")
    if tempo_character not in TEMPO_CHARACTER_VOCAB:
        tempo_character = None
    tempo_bpm = data.get("tempo_bpm")
    energy = data.get("energy")
    danceability = data.get("danceability")

    if (
        not genres and not vibe_tags and not moods and tempo_character is None
        and tempo_bpm is None and energy is None and danceability is None
    ):
        return None  # no usable musical signal

    return {
        "genres": genres,
        "vibe_tags": vibe_tags,
        "moods": moods,
        "tempo_character": tempo_character,
        "tempo_bpm": tempo_bpm,
        "energy": energy,
        "danceability": danceability,
    }


async def interpret_playlist_description(description: str) -> dict | None:
    """Turns a playlist's free-text description into structured sonic hints,
    cached on the Playlist row (description_signal) so it can contribute to
    scoring (via Playlist._description_phantom) without a live OpenAI call
    every time a fit score is computed. Called once, when the description is
    saved - see routers/playlists.py PUT /{id}/description.

    Returns None if the description is blank or has no usable signal, so an
    empty/unhelpful description doesn't quietly inject noise into scoring.
    """
    if not description or not description.strip():
        return None

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": DESCRIPTION_SIGNAL_PROMPT},
            {"role": "user", "content": description},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    return _parse_text_signal(json.loads(resp.choices[0].message.content))


TITLE_SIGNAL_PROMPT = (
    "You extract structured musical signals from a playlist's own TITLE (its name, not a "
    "description - titles are short, so lean on genre/mood/vibe conventions, e.g. 'Epic "
    "Uplifting Anthems' implies vibe_tags ['epic', 'uplifting'], mood 'euphoric'; 'Workout "
    "Hype' implies high energy, tempo_character 'fast/driving', vibe tags like ['intense', "
    "'motivational']; 'Rainy Day Chillout' implies tempo_character 'slow/ambient', low energy, "
    "mood 'chill', vibe tags like ['cozy']). A title with no sonic implication at all (a "
    "person's name, an unrelated in-joke, a date) implies nothing - every field should come "
    "back null/empty in that case; don't invent signal that isn't there. Genres, vibe tags "
    "(broader thematic/cinematic descriptors like 'epic', 'cinematic', 'anthemic', 'nostalgic', "
    "'dreamy', 'gritty' - distinct from genre and from mood), mood, tempo character, and implied "
    "energy all count; a title rarely implies a precise BPM or danceability, so only fill those "
    "in when it's genuinely obvious (e.g. a title that literally states a BPM). Respond ONLY "
    'with JSON matching exactly this shape: {"genres": [0 to 4 strings], "vibe_tags": [0 to 4 '
    f'strings], "moods": [0 to 2 strings, each one of {MOOD_VOCAB}], '
    f'"tempo_character": null or one of {TEMPO_CHARACTER_VOCAB}, '
    '"tempo_bpm": number|null, "energy": number 0-1|null, "danceability": number 0-1|null}'
)


async def interpret_playlist_title(name: str) -> dict | None:
    """Turns a playlist's own NAME into structured sonic/vibe hints, cached
    on the Playlist row (title_signal) and blended into fit_score.py's
    Composite Target Profile (_build_composite_target) - unlike a member
    song, a title is a fixed statement of intent, not evidence to average
    into the collective vibe the same way accumulated songs are.
    Called once, at playlist create/adopt time.

    Returns None if the name has no usable musical/vibe signal (many
    playlist names are just personal labels with no sonic implication).
    """
    if not name or not name.strip():
        return None

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": TITLE_SIGNAL_PROMPT},
            {"role": "user", "content": name},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    return _parse_text_signal(json.loads(resp.choices[0].message.content))


WEIGHT_SUGGESTION_PROMPT = (
    "You suggest a scoring weight distribution for a music playlist matching algorithm, based "
    "on the playlist's name and description. There are four components: "
    "'theme_vibe' (how well a candidate song's broader thematic/cinematic character embodies the "
    "playlist's core concept), 'genre_style' (musical/instrumental genre compatibility), "
    "'emotional_tone' (how well the song's emotional character matches the playlist's), and "
    "'tempo_energy' (physical drive and pacing - tempo character plus energy/danceability). "
    "Weight the playlist's own evident priorities: a workout/gym/running playlist should weight "
    "'tempo_energy' heavily since pace and intensity matter most there; a study/focus/ambient/"
    "sleep playlist should weight 'theme_vibe' and 'emotional_tone' more heavily since consistent "
    "mood/concept matters more than tempo precision; a playlist named after a specific aesthetic "
    "or scene (genre-named, era-named) should weight 'genre_style' heavily; a playlist with a "
    "strong thematic/emotional name (e.g. 'Epic Uplifting Anthems') should weight 'theme_vibe' "
    "and 'emotional_tone' heavily. If the name/description gives no strong signal either way, "
    "return the balanced default (0.30, 0.25, 0.25, 0.20). Respond ONLY with JSON matching "
    'exactly this shape: {"theme_vibe": number, "genre_style": number, "emotional_tone": number, '
    '"tempo_energy": number} - the four numbers should sum to 1.0.'
)


async def suggest_scoring_weights(name: str, description: str) -> dict:
    """Suggests a starting weight distribution across the four fit-score
    components, based on the playlist's own name and description - e.g. a
    workout playlist should weight tempo_energy heavily, a study playlist
    should weight theme_vibe/emotional_tone. Called once at playlist
    create/adopt time to initialize Playlist.weights; from there, the
    person can freely re-adjust via UI sliders
    (PUT /api/playlists/{id}/weights).

    Falls back to the balanced default if the name/description gives no
    strong signal, or if anything about the response is malformed - a bad
    weight suggestion should never block creating a playlist.
    """
    from app.fit_score import DEFAULT_WEIGHTS  # local import: fit_score.py has zero app-internal
    # imports by design (see its module docstring) - importing it only here, only for the
    # fallback default, keeps that property intact without a module-level circular risk.

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    user_prompt = f"Playlist name: {name}\nDescription: {description or '(none given)'}"

    try:
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": WEIGHT_SUGGESTION_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        data = json.loads(resp.choices[0].message.content)
        weights = {k: float(data[k]) for k in DEFAULT_WEIGHTS if k in data}
    except (KeyError, TypeError, ValueError):
        return dict(DEFAULT_WEIGHTS)

    if len(weights) != len(DEFAULT_WEIGHTS) or any(w < 0 for w in weights.values()):
        return dict(DEFAULT_WEIGHTS)

    total = sum(weights.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: v / total for k, v in weights.items()}  # normalize to sum exactly 1.0


EXPLAIN_FIT_PROMPT = (
    "You explain, in 1-2 short sentences, why a song scored the way it did against a "
    "playlist's sonic and semantic profile. Be specific and concrete - reference the actual "
    "genre, vibe tags, tempo character, and mood values involved, and the playlist's own "
    "title/vibe intent if given, not vague praise or criticism. tempo_bpm_reference_only is a "
    "raw BPM figure kept only for display - the actual tempo scoring is based on "
    "tempo_character (a categorical pace/feel judgment), so reference that, not the raw number. "
    "Plain conversational tone, no jargon, no bullet points, no JSON - just the 1-2 sentences "
    "of prose, nothing else."
)


async def explain_fit_score(song, profile: dict, fit: dict) -> str:
    """Generates a short, on-demand explanation of a song's fit score -
    powers the 'why?' button in Check My Playlists. Only called when a
    person explicitly asks about one specific song, never automatically for
    a whole playlist, so it doesn't turn Check My Playlists into something
    that waits on an API call per row.
    """
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    top_genres = sorted(profile["genre_distribution"].items(), key=lambda kv: -kv[1])[:4]
    top_vibes = sorted(profile.get("vibe_distribution", {}).items(), key=lambda kv: -kv[1])[:4]
    top_moods = sorted(profile["mood_distribution"].items(), key=lambda kv: -kv[1])[:3]
    top_tempo_characters = sorted(
        profile.get("tempo_character_distribution", {}).items(), key=lambda kv: -kv[1]
    )[:2]

    payload = {
        "song": {
            "name": song.name,
            "artists": song.artists,
            "genres": song.genres or [],
            "vibe_tags": getattr(song, "vibe_tags", None) or [],
            "tempo_character": getattr(song, "tempo_character", None),
            "tempo_bpm_reference_only": song.tempo,
            "mood": song.mood,
            "energy_0_1": song.energy,
            "danceability_0_1": song.danceability,
        },
        "playlist_profile": {
            "top_genres": [g for g, _ in top_genres],
            "top_vibe_tags": [v for v, _ in top_vibes],
            "top_moods": [m for m, _ in top_moods],
            "top_tempo_characters": [t for t, _ in top_tempo_characters],
            "avg_energy_0_1": profile["avg_energy"],
            "avg_danceability_0_1": profile["avg_danceability"],
            "title_implied_vibe": profile.get("title_signal") or "none given",
        },
        "computed_score": fit["score"],
        "score_breakdown": fit["breakdown"],
    }

    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": EXPLAIN_FIT_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ],
        temperature=0.5,
    )
    return resp.choices[0].message.content.strip()
