"""Computes a 0-100 fit score between a song and a playlist, by scoring the
song against a synthesized COMPOSITE TARGET PROFILE rather than any single
signal in isolation.

The Composite Target blends THREE independent sources (see
_build_composite_target), each representing a different facet of the
playlist's identity:
    description_signal   40%  - the playlist's own written description -
                                 the primary anchor, since it's the
                                 clearest statement of intent a person
                                 actually wrote
    title_signal          30%  - what the playlist's own NAME implies
    members aggregate      30%  - the actual accumulated songs

A source that's entirely absent (no description written, a brand-new
playlist with zero songs) has its share proportionally redistributed among
whichever sources ARE present.

Four scoring categories, each weighted per playlist (Playlist.weights, JSON
column - DEFAULT_WEIGHTS below is only a fallback). Internal dict keys stay
stable even though the UI displays slightly different labels:
    theme_vibe     -> "Theme & Vibe"            - vibe-tag semantic
                       similarity to the Composite Target
    genre_style     -> "Genre & Style"            - genre semantic similarity,
                       with tolerance for modern fusion sub-genres (see
                       _genre_style_score)
    emotional_tone  -> "Mood / Emotional Tone"    - mood similarity via
                       semantic clusters/coordinates PLUS a hidden
                       aggression/darkness alignment check (see below)
    tempo_energy    -> "Energy & Tempo"           - a smooth Gaussian
                       gradient on the raw BPM difference, blended with
                       energy/danceability closeness

SEMANTIC MATCHING, not string matching: genre/vibe-tag similarity uses
continuous OpenAI embeddings (app/embeddings.py); mood/emotional-word
similarity uses an explicit synonym-cluster dictionary (EMOTIONAL_CLUSTERS)
plus a fixed-vocabulary valence/arousal coordinate system
(MOOD_COORDINATES) for the 8 canonical moods. Two words in the same
cluster score 90+ against each other regardless of exact string identity.

HIDDEN AGGRESSION/DARKNESS INDEX: audio_features.py's per-song analysis
includes a hidden aggression_darkness_index (0-100) that is NEVER exposed
via the API or a UI slider - it exists purely to make emotional_tone
scoring more robust, since a song's categorical mood label alone can be
ambiguous (an "energetic" tag doesn't say whether that energy is joyful or
aggressive). It's absorbed directly into _emotional_tone_score's math so a
high-energy JOYFUL song can't score well against a high-energy AGGRESSIVE
playlist just because their raw energy numbers happen to match.

GRADIENT BPM SCORING, not flat category penalties: tempo scoring compares
the song's exact BPM against the Composite Target's blended BPM with a
smooth Gaussian falloff (_bpm_gradient_score) - no discrete tempo-category
buckets, no arbitrary flat penalty for an "adjacent" category mismatch.
Song.tempo_character is still recorded (audio_features.py) and shown in
the UI as a human-readable descriptor, but it is NOT consumed by scoring
math anymore.

A VIBE VETO (VIBE_VETO_THRESHOLD/VIBE_VETO_MULTIPLIER) crushes the final
score with a severe multiplier if theme_vibe OR emotional_tone is
critically low, regardless of how well genre/tempo happen to align.

compute_fit_score() is deliberately kept SYNCHRONOUS and dependency-free
(only stdlib `math`) - it's handed a ready-made {tag: embedding_vector}
dict rather than fetching embeddings itself, so all async/DB/API
orchestration lives in app/embeddings.py and the router layer.

detect_playlist_drift() is a separate, PLAYLIST-level (not per-song)
utility that compares the members-only aggregate against the
title+description premise alone, to flag when accumulated songs have
drifted from what the playlist was actually built around.
"""

import math

DEFAULT_WEIGHTS = {"theme_vibe": 0.30, "genre_style": 0.25, "emotional_tone": 0.25, "tempo_energy": 0.20}

# How the three signal sources blend into the Composite Target Profile -
# see _build_composite_target.
COMPOSITE_WEIGHTS = {"description": 0.40, "title": 0.30, "members": 0.30}

# A song can carry multiple genres or vibe tags (see app/audio_features.py),
# ordered most to least dominant. Earlier entries carry more weight both
# when a song contributes to a playlist's aggregate distribution
# (models.py) and when scoring a song against that distribution below.
GENRE_POSITION_WEIGHTS = [1.0, 0.6, 0.4, 0.25]

# Anchors for rescaling raw embedding cosine similarity (typically a narrow
# band for short phrases, rarely near 0 or 1) onto a full 0-100 spread.
# Approximate for text-embedding-3-small on short genre/vibe phrases - if
# real-world scores cluster too high or low once this is actually run
# against the live API, these are the two numbers to retune.
SIMILARITY_FLOOR = 0.15
SIMILARITY_CEILING = 0.70

# Gaussian tolerance (in BPM) for tempo scoring - controls how quickly the
# score falls off with distance. At 22: a 10 BPM gap barely matters (~90),
# a 30 BPM gap is a real but not crushing penalty (~40), 50+ BPM apart is
# essentially zero. Tunable if real songs show this too strict/loose.
BPM_GAUSSIAN_SIGMA = 22.0

# Approximate (valence, arousal) coordinates for each of the 8 fixed mood
# categories, based on the circumplex model of affect - used to give
# PARTIAL credit for semantically adjacent moods instead of treating every
# non-exact match as an equally total clash. Tunable if real usage shows a
# specific pair scoring oddly.
MOOD_COORDINATES = {
    "euphoric":    (0.90, 0.85),
    "uplifting":   (0.85, 0.70),
    "energetic":   (0.60, 0.90),
    "chill":       (0.75, 0.15),
    "romantic":    (0.70, 0.35),
    "melancholic": (0.20, 0.25),
    "dark":        (0.15, 0.50),
    "aggressive":  (0.15, 0.90),
}
MOOD_SIMILARITY_SIGMA = 0.35

# Explicit synonym clusters for common COLLOQUIAL emotional words - exists
# alongside MOOD_COORDINATES (not instead of it) because free-form vibe
# tags or a title/description's own wording can use words entirely outside
# the fixed 8-value mood vocabulary ("happy", "positive", "gritty") that
# MOOD_COORDINATES has no entry for. Any two words in the same cluster
# score 90 against each other. "energetic" is deliberately NOT clustered
# with "aggressive" - high energy alone doesn't imply aggression, which is
# exactly the ambiguity the hidden aggression_darkness_index (see below)
# exists to resolve more precisely than a word-cluster ever could.
EMOTIONAL_CLUSTERS = [
    {"happy", "uplifting", "euphoric", "playful", "positive", "joyful",
     "cheerful", "upbeat", "triumphant", "fun"},
    {"chill", "relaxed", "mellow", "calm", "peaceful", "romantic",
     "dreamy", "intimate", "sultry", "cozy"},
    {"dark", "gritty", "melancholic", "moody", "brooding", "menacing",
     "ominous", "somber", "wistful", "nostalgic", "bittersweet"},
    {"aggressive", "angry", "intense", "fierce", "hard-hitting"},
]

# How heavily the hidden aggression_darkness_index factors into
# emotional_tone, alongside the primary mood/cluster signal - see
# _emotional_tone_score. Meaningful (30%) but not dominant.
AGGRESSION_WEIGHT_IN_EMOTIONAL_TONE = 0.30

# Genre fusion tolerance (see _genre_style_score): modern hybrid genres
# ("trap metal", "industrial hip-hop") often don't share strong text/
# embedding similarity with a playlist's primary genre despite genuinely
# belonging there. If a song's energy AND aggression/darkness profile are
# both close to the target's, that's corroborating evidence.
GENRE_FUSION_ENERGY_TOLERANCE = 0.20
GENRE_FUSION_AGGRESSION_TOLERANCE = 20.0
GENRE_FUSION_BOOST = 20.0

# "Vibe veto" - see compute_fit_score. If the song's fit against the
# Composite Target's theme/vibe OR emotional tone is this critically low,
# no amount of genre/tempo agreement should be allowed to rescue the total
# score.
VIBE_VETO_THRESHOLD = 35.0
VIBE_VETO_MULTIPLIER = 0.45

# How far a members-only aggregate's dominant mood/genre/vibe can sit from
# the title+description premise's dominant one before it's flagged as
# drift - see detect_playlist_drift.
DRIFT_MOOD_SIMILARITY_THRESHOLD = 40.0


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Duplicated from app/embeddings.py rather than imported, to avoid a
    circular import: embeddings.py -> models.py -> fit_score.py (for
    DEFAULT_WEIGHTS/GENRE_POSITION_WEIGHTS) would cycle back here if this
    module imported embeddings.py.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _rescale_similarity(cosine: float) -> float:
    return _clamp(100 * (cosine - SIMILARITY_FLOOR) / (SIMILARITY_CEILING - SIMILARITY_FLOOR))


def _dominant_tag(distribution: dict) -> str | None:
    if not distribution:
        return None
    return max(distribution.items(), key=lambda kv: kv[1])[0]


def _loose_tag_match(a: str, b: str) -> bool:
    """Cheap, LOCAL substring/case-insensitive check used only by
    detect_playlist_drift's genre/vibe dimension - not the main scoring
    path (which uses real embeddings for genuine semantic similarity).
    """
    a, b = a.lower().strip(), b.lower().strip()
    return a == b or a in b or b in a


def _emotional_cluster_score(word_a: str, word_b: str) -> float | None:
    """Explicit cluster-membership check (EMOTIONAL_CLUSTERS) - returns 90
    if both words are members of the SAME hand-curated cluster, or None if
    neither is recognized in any cluster.
    """
    a, b = word_a.lower().strip(), word_b.lower().strip()
    if a == b:
        return 100.0
    for cluster in EMOTIONAL_CLUSTERS:
        if a in cluster and b in cluster:
            return 90.0
    return None


def _mood_pair_similarity(mood_a: str, mood_b: str) -> float:
    """0-100 semantic similarity between two mood/emotional words. Checks
    the explicit EMOTIONAL_CLUSTERS dictionary first, then falls back to
    MOOD_COORDINATES' valence/arousal distance for the 8 canonical mood
    categories.
    """
    if mood_a == mood_b:
        return 100.0

    cluster_score = _emotional_cluster_score(mood_a, mood_b)
    if cluster_score is not None:
        return cluster_score

    a = MOOD_COORDINATES.get(mood_a)
    b = MOOD_COORDINATES.get(mood_b)
    if a is None or b is None:
        return 0.0
    dist = math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)
    return _clamp(100 * math.exp(-(dist ** 2) / (2 * MOOD_SIMILARITY_SIGMA ** 2)))


def _aggression_proxy_from_mood(mood: str) -> float | None:
    """Derives an approximate 0-100 aggression/darkness level from a fixed
    mood category's (valence, arousal) coordinates - used only to estimate
    an implied 'target aggression level' from a title/description's stated
    moods, which don't have a real per-track aggression_darkness_index the
    way actual songs do.
    """
    coords = MOOD_COORDINATES.get(mood)
    if coords is None:
        return None
    valence, arousal = coords
    return _clamp(arousal * (1 - valence) * 100)


def _semantic_centroid(distribution: dict, embeddings: dict) -> list[float] | None:
    """Weighted-average embedding vector representing a target
    distribution."""
    total_weight = 0.0
    centroid = None
    for tag, weight in distribution.items():
        vec = embeddings.get(tag)
        if not vec:
            continue
        if centroid is None:
            centroid = [0.0] * len(vec)
        for i, v in enumerate(vec):
            centroid[i] += v * weight
        total_weight += weight
    if centroid is None or total_weight == 0:
        return None
    return [v / total_weight for v in centroid]


def _best_pairwise_match(
    song_tags: list[str] | None, distribution: dict, embeddings: dict
) -> tuple[str, str, float] | None:
    """Finds the single (song_tag, target_tag) pair with the highest cosine
    similarity - for a concrete, human-readable explanation snippet."""
    best = None
    best_sim = -1.0
    for song_tag in song_tags or []:
        song_vec = embeddings.get(song_tag)
        if not song_vec:
            continue
        for target_tag in distribution:
            target_vec = embeddings.get(target_tag)
            if not target_vec:
                continue
            sim = _cosine_similarity(song_vec, target_vec)
            if sim > best_sim:
                best_sim = sim
                best = (song_tag, target_tag, sim)
    return best


def _semantic_tag_score(
    song_tags: list[str] | None, distribution: dict, embeddings: dict
) -> tuple[float, tuple[str, str, float] | None]:
    """Continuous, position-weighted semantic similarity between a song's
    tags and a target distribution. Returns (score, best matching pair)."""
    if not distribution or not song_tags:
        return 50.0, None

    centroid = _semantic_centroid(distribution, embeddings)
    if centroid is None:
        return 50.0, None

    weights_used = GENRE_POSITION_WEIGHTS[: len(song_tags)]
    weighted_sum = 0.0
    total_weight = 0.0
    for tag, weight in zip(song_tags, weights_used):
        vec = embeddings.get(tag)
        if not vec:
            continue
        sim = _cosine_similarity(vec, centroid)
        weighted_sum += weight * _rescale_similarity(sim)
        total_weight += weight

    if total_weight == 0:
        return 50.0, None

    score = _clamp(weighted_sum / total_weight)
    best_match = _best_pairwise_match(song_tags, distribution, embeddings)
    return score, best_match


def _mood_score(song_mood, mood_distribution: dict) -> float:
    """Proportional match against a mood distribution, using semantic
    similarity (_mood_pair_similarity) rather than strict string equality."""
    if not song_mood or not mood_distribution:
        return 50.0
    total = sum(mood_distribution.values()) or 1
    weighted_sum = sum(
        _mood_pair_similarity(song_mood, target_mood) * weight
        for target_mood, weight in mood_distribution.items()
    )
    return _clamp(weighted_sum / total)


def _energy_dance_score(song_energy, song_dance, avg_energy, avg_dance) -> float:
    scores = []
    if song_energy is not None and avg_energy is not None:
        scores.append(100 - abs(song_energy - avg_energy) * 100)
    if song_dance is not None and avg_dance is not None:
        scores.append(100 - abs(song_dance - avg_dance) * 100)
    if not scores:
        return 50.0
    return _clamp(sum(scores) / len(scores))


def _bpm_gradient_score(song_bpm: float | None, target_bpm: float | None) -> float:
    """Smooth Gaussian gradient on the ABSOLUTE numerical BPM difference -
    NOT a discrete category lookup. A small gap barely matters, a large
    gap falls off smoothly toward zero; no flat, arbitrary penalty applied
    for crossing some category boundary."""
    if song_bpm is None or target_bpm is None:
        return 50.0
    diff = abs(song_bpm - target_bpm)
    return _clamp(100 * math.exp(-(diff ** 2) / (2 * BPM_GAUSSIAN_SIGMA ** 2)))


def _signal_to_mini_distribution(signal: dict | None) -> dict:
    """Turns a title_signal/description_signal dict into the same
    distribution-shaped fields a members aggregate already uses, so all
    three Composite Target sources can be blended together uniformly.
    avg_aggression_darkness is ESTIMATED from the signal's stated moods.
    """
    if not signal:
        return {
            "genre_distribution": {}, "vibe_distribution": {}, "mood_distribution": {},
            "avg_tempo": None, "avg_energy": None, "avg_danceability": None,
            "avg_aggression_darkness": None,
        }
    moods = signal.get("moods") or []
    aggression_proxies = [p for p in (_aggression_proxy_from_mood(m) for m in moods) if p is not None]
    return {
        "genre_distribution": {g: 1.0 for g in (signal.get("genres") or [])},
        "vibe_distribution": {v: 1.0 for v in (signal.get("vibe_tags") or [])},
        "mood_distribution": {m: 1.0 for m in moods},
        "avg_tempo": signal.get("tempo_bpm"),
        "avg_energy": signal.get("energy"),
        "avg_danceability": signal.get("danceability"),
        "avg_aggression_darkness": (
            sum(aggression_proxies) / len(aggression_proxies) if aggression_proxies else None
        ),
    }


def _blend_distributions(sources: list[tuple[dict, float]]) -> dict:
    """Merges N {tag: weight} distributions into one. Each source is
    normalized to sum to 1.0 FIRST, then scaled by its own blend weight."""
    merged: dict[str, float] = {}
    for dist, blend_weight in sources:
        if not dist or blend_weight <= 0:
            continue
        total = sum(dist.values()) or 1
        for tag, w in dist.items():
            merged[tag] = merged.get(tag, 0) + (w / total) * blend_weight
    return merged


def _build_composite_target(
    members_profile: dict, title_signal: dict | None, description_signal: dict | None
) -> dict:
    """Blends the playlist's three signal sources into ONE Composite Target
    Profile - see the module docstring for the 40/30/30 weights. A source
    that's entirely absent has its share redistributed proportionally.
    """
    title_mini = _signal_to_mini_distribution(title_signal)
    description_mini = _signal_to_mini_distribution(description_signal)
    has_members = bool(members_profile.get("song_count", 0))

    present: list[tuple[dict, float]] = []
    if description_signal:
        present.append((description_mini, COMPOSITE_WEIGHTS["description"]))
    if title_signal:
        present.append((title_mini, COMPOSITE_WEIGHTS["title"]))
    if has_members:
        present.append((members_profile, COMPOSITE_WEIGHTS["members"]))

    if not present:
        return _signal_to_mini_distribution(None)

    total_weight = sum(w for _, w in present)
    normalized = [(dist, w / total_weight) for dist, w in present]

    def blended_avg(field: str) -> float | None:
        total_w = 0.0
        total_v = 0.0
        for dist, w in normalized:
            v = dist.get(field)
            if v is not None:
                total_v += v * w
                total_w += w
        return total_v / total_w if total_w else None

    return {
        "genre_distribution": _blend_distributions([(d.get("genre_distribution", {}), w) for d, w in normalized]),
        "vibe_distribution": _blend_distributions([(d.get("vibe_distribution", {}), w) for d, w in normalized]),
        "mood_distribution": _blend_distributions([(d.get("mood_distribution", {}), w) for d, w in normalized]),
        "avg_tempo": blended_avg("avg_tempo"),
        "avg_energy": blended_avg("avg_energy"),
        "avg_danceability": blended_avg("avg_danceability"),
        "avg_aggression_darkness": blended_avg("avg_aggression_darkness"),
    }


def detect_playlist_drift(
    members_profile: dict, title_signal: dict | None, description_signal: dict | None
) -> tuple[bool, str | None]:
    """Compares the playlist's ACTUAL accumulated songs against its
    ORIGINAL title+description premise alone, to catch "echo chamber"
    drift. PLAYLIST-level check (not per-song). Pure local computation.
    """
    if not members_profile.get("song_count", 0):
        return False, None

    premise = _build_composite_target(_signal_to_mini_distribution(None), title_signal, description_signal)
    if not premise["mood_distribution"] and not premise["genre_distribution"] and not premise["vibe_distribution"]:
        return False, None

    reasons = []

    members_mood = _dominant_tag(members_profile.get("mood_distribution", {}))
    premise_mood = _dominant_tag(premise["mood_distribution"])
    if members_mood and premise_mood and members_mood != premise_mood:
        if _mood_pair_similarity(members_mood, premise_mood) < DRIFT_MOOD_SIMILARITY_THRESHOLD:
            reasons.append(f"trending more '{members_mood}' than the '{premise_mood}' the playlist was built around")

    members_style = _dominant_tag(members_profile.get("vibe_distribution", {})) or _dominant_tag(
        members_profile.get("genre_distribution", {})
    )
    premise_style = _dominant_tag(premise["vibe_distribution"]) or _dominant_tag(premise["genre_distribution"])
    if members_style and premise_style and members_style != premise_style:
        if not _loose_tag_match(members_style, premise_style):
            reasons.append(f"leaning toward '{members_style}' rather than the '{premise_style}' it started from")

    if not reasons:
        return False, None

    comment = "The songs here are " + ", and ".join(reasons) + "."
    return True, comment[0].upper() + comment[1:]


def _theme_vibe_score(song, composite: dict, embeddings: dict) -> tuple[float, tuple | None]:
    """How well the song's vibe tags match the Composite Target's broader
    thematic/cinematic character (e.g. "epic", "playful")."""
    song_vibe_tags = getattr(song, "vibe_tags", None) or []
    return _semantic_tag_score(song_vibe_tags, composite.get("vibe_distribution", {}), embeddings)


def _genre_style_score(song, composite: dict, embeddings: dict) -> tuple[float, tuple | None]:
    """Musical/instrumental style compatibility, with a genre FUSION
    TOLERANCE for modern hybrid genres ("trap metal", "industrial
    hip-hop") that don't share strong text/embedding similarity with a
    playlist's primary genre despite genuinely belonging there - if the
    song's energy AND aggression/darkness profile are both close to the
    target's, that's corroborating evidence and softens the score.
    """
    base_score, match = _semantic_tag_score(song.genres or [], composite.get("genre_distribution", {}), embeddings)

    song_energy = song.energy
    target_energy = composite.get("avg_energy")
    song_aggression = getattr(song, "aggression_darkness_index", None)
    target_aggression = composite.get("avg_aggression_darkness")

    if None not in (song_energy, target_energy, song_aggression, target_aggression):
        energy_close = abs(song_energy - target_energy) <= GENRE_FUSION_ENERGY_TOLERANCE
        aggression_close = abs(song_aggression - target_aggression) <= GENRE_FUSION_AGGRESSION_TOLERANCE
        if energy_close and aggression_close:
            base_score = _clamp(base_score + GENRE_FUSION_BOOST)

    return base_score, match


def _emotional_tone_score(song, composite: dict) -> tuple[float, float]:
    """Mood similarity to the Composite Target's emotional character,
    blended with the HIDDEN aggression_darkness_index alignment so a
    high-energy JOYFUL song can't score well against a high-energy
    AGGRESSIVE playlist just because a mood-category label or energy
    number happens to overlap. Returns (score, raw mood-only score) - the
    latter is kept for the vibe veto and explanation.
    """
    mood_distribution = composite.get("mood_distribution", {})
    mood_only_score = _mood_score(song.mood, mood_distribution)

    dominant_target_mood = _dominant_tag(mood_distribution)
    best_extra = 0.0
    if dominant_target_mood:
        for tag in getattr(song, "vibe_tags", None) or []:
            sim = _mood_pair_similarity(tag, dominant_target_mood)
            best_extra = max(best_extra, sim)
    mood_signal = max(mood_only_score, best_extra)

    song_aggression = getattr(song, "aggression_darkness_index", None)
    target_aggression = composite.get("avg_aggression_darkness")
    if song_aggression is not None and target_aggression is not None:
        aggression_alignment = _clamp(100 - abs(song_aggression - target_aggression))
        combined = (
            (1 - AGGRESSION_WEIGHT_IN_EMOTIONAL_TONE) * mood_signal
            + AGGRESSION_WEIGHT_IN_EMOTIONAL_TONE * aggression_alignment
        )
    else:
        combined = mood_signal

    return _clamp(combined), mood_only_score


def _tempo_energy_score(song, composite: dict) -> float:
    """Physical drive and pacing synergy: a smooth Gaussian gradient on
    the song's exact BPM vs. the Composite Target's blended BPM, blended
    with energy/danceability closeness."""
    bpm_score = _bpm_gradient_score(song.tempo, composite.get("avg_tempo"))
    energy_dance_score = _energy_dance_score(
        song.energy, song.danceability, composite.get("avg_energy"), composite.get("avg_danceability")
    )
    return _clamp(0.6 * bpm_score + 0.4 * energy_dance_score)


def _describe_match(match: tuple[str, str, float] | None, subject: str) -> tuple[str, float] | None:
    """Turns a (song_tag, target_tag, similarity) tuple into a phrase, or
    None if the match is too weak to be worth mentioning."""
    if not match:
        return None
    song_tag, target_tag, sim = match
    if sim < 0.45:
        return None
    verb = "echoes" if sim >= 0.6 else "loosely resembles"
    return f"'{song_tag}' {verb} {subject} ('{target_tag}')", sim


def _build_explanation(
    vibe_match, genre_match, song_mood: str | None, mood_score: float,
    tempo_energy_score: float, vetoed: bool, veto_reasons: list[str],
) -> str:
    """Fast, template-built, plain-English summary of why a song scored the
    way it did - no LLM call. For a richer, on-demand prose explanation,
    see openai_client.explain_fit_score."""
    parts = []

    if vetoed:
        parts.append(
            f"⚠ vibe veto: {' and '.join(veto_reasons)} critically low, "
            "so the total score was heavily penalized regardless of any genre/tempo match"
        )

    candidates = []
    for match, subject in [
        (vibe_match, "the playlist's composite vibe"),
        (genre_match, "the playlist's composite genre"),
    ]:
        described = _describe_match(match, subject)
        if described:
            candidates.append(described)
    candidates.sort(key=lambda c: -c[1])
    parts += [phrase for phrase, _ in candidates]

    if song_mood:
        if mood_score >= 70:
            parts.append(f"'{song_mood}' mood fits well")
        elif mood_score <= 30:
            parts.append(f"'{song_mood}' mood clashes with the playlist")

    if tempo_energy_score >= 80:
        parts.append("tempo/energy aligns closely")
    elif tempo_energy_score <= 30:
        parts.append("tempo/energy is a mismatch")

    if not parts:
        return "Limited overlap with this playlist's composite theme, genre, mood, and pacing."

    sentence = "; ".join(parts)
    return sentence[0].upper() + sentence[1:] + "."


def compute_fit_score(song, playlist_profile: dict, embeddings: dict, weights: dict | None = None) -> dict:
    """Returns {"score": int, "breakdown": {...}, "explanation": str} for
    one song vs one playlist.

    `playlist_profile` is expected to be a Playlist.sonic_profile() dict -
    a PURE members-only aggregate plus `title_signal` and
    `description_signal` passed through unblended. This builds the
    Composite Target Profile internally before scoring the four
    categories against it.

    `embeddings` must be a pre-fetched {tag: vector} dict - see
    app/embeddings.py::get_embeddings_for_tags. `weights` overrides
    playlist_profile.get("weights"); falls back to DEFAULT_WEIGHTS.
    """
    active_weights = weights or playlist_profile.get("weights") or DEFAULT_WEIGHTS

    composite = _build_composite_target(
        playlist_profile, playlist_profile.get("title_signal"), playlist_profile.get("description_signal")
    )

    theme_vibe, vibe_match = _theme_vibe_score(song, composite, embeddings)
    genre_style, genre_match = _genre_style_score(song, composite, embeddings)
    emotional_tone, mood_only_score = _emotional_tone_score(song, composite)
    tempo_energy = _tempo_energy_score(song, composite)

    weight_sum = sum(active_weights.get(k, 0) for k in DEFAULT_WEIGHTS) or 1.0
    total = (
        theme_vibe * active_weights.get("theme_vibe", DEFAULT_WEIGHTS["theme_vibe"])
        + genre_style * active_weights.get("genre_style", DEFAULT_WEIGHTS["genre_style"])
        + emotional_tone * active_weights.get("emotional_tone", DEFAULT_WEIGHTS["emotional_tone"])
        + tempo_energy * active_weights.get("tempo_energy", DEFAULT_WEIGHTS["tempo_energy"])
    ) / weight_sum

    veto_reasons = []
    if theme_vibe < VIBE_VETO_THRESHOLD:
        veto_reasons.append("theme/vibe fit")
    if emotional_tone < VIBE_VETO_THRESHOLD:
        veto_reasons.append("emotional tone")
    elif mood_only_score < VIBE_VETO_THRESHOLD:
        veto_reasons.append("mood")
    vetoed = bool(veto_reasons)
    if vetoed:
        total *= VIBE_VETO_MULTIPLIER

    explanation = _build_explanation(
        vibe_match, genre_match, song.mood, mood_only_score, tempo_energy, vetoed, veto_reasons
    )

    return {
        "score": round(_clamp(total)),
        "breakdown": {
            "theme_vibe": round(theme_vibe),
            "genre_style": round(genre_style),
            "emotional_tone": round(emotional_tone),
            "tempo_energy": round(tempo_energy),
        },
        "explanation": explanation,
    }
