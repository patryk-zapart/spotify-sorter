# Sorted — Spotify Playlist Sorter

Pulls songs in from an existing Spotify playlist, analyzes each one (genre,
tempo, mood/climate, key, energy, danceability), and walks you through
assigning every track to the local playlist it fits best — writing each
assignment straight back to the real playlist on Spotify. Each playlist card
can also generate name suggestions from its aggregate sonic profile via
OpenAI.

## ⚠️ Read this first: Spotify API constraints

Two separate rounds of Spotify Web API restrictions shape how this app has
to work. Both are handled in code (`app/spotify_client.py`), but you need to
know about them as a user of the app, not just a maintainer of it.

**1. Only your own (or collaborative) playlists can be imported.** Spotify's
[February 2026 "Dev Mode" changes](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
renamed `/playlists/{id}/tracks` to `/playlists/{id}/items` **and** restricted
it to return track data only for playlists the authenticated user owns or
collaborates on. Every other playlist — including *all* Spotify-owned/
editorial ones (Discover Weekly, Daily Mix, genre radio, any ID starting with
`37i9dQZ...`) — now returns just metadata, no track list, and this app
surfaces that as a 403 with an explanation. There's no workaround: pick a
playlist you made yourself.

**2. BPM/key/energy/danceability/mood can't come from Spotify at all.**
Separately, Spotify deprecated `/audio-features`, `/audio-analysis`,
`/recommendations`, and the bulk `/artists` and `/albums` endpoints for any
app not already granted an extended-quota exception before **November 27,
2024**. There is no official replacement, and Spotify has stated this won't
change for new apps.

This project works around point 2 by:

- **Genre** — fetched from the single-artist endpoint (`GET /artists/{id}`),
  which remains live.
- **Tempo / key / mode / energy / danceability / valence / mood** — estimated
  by an LLM (`app/audio_features.py::OpenAIAudioFeaturesProvider`) from the
  track's title, artist(s), album, and known genre tags. This is a
  well-informed *estimate*, not a measurement — it's labeled as `"AI
  estimate"` in the UI (`song.analysis_source`).
- If you happen to hold a pre-Nov-2024 grandfathered token with access to the
  real endpoint, set `AUDIO_FEATURES_PROVIDER=spotify` in the backend `.env`
  and `SpotifyAudioFeaturesProvider` will be used instead — no other code
  changes needed, since both providers implement the same interface.

## Architecture

```
backend/               FastAPI app
  app/
    config.py           env-based settings
    db.py                SQLAlchemy engine/session (SQLite)
    models.py            Playlist, Song, PlaylistSong (many-to-many membership),
                          TagEmbedding (embedding cache) ORM models
    schemas.py            Pydantic request/response models
    spotify_client.py    Spotify OAuth + REST wrapper
    audio_features.py    pluggable tempo-character/mood/vibe inference (OpenAI default)
    embeddings.py          cached OpenAI embeddings + cosine similarity for genre/vibe scoring
    fit_score.py          dynamic-weight, embeddings-based fit-score algorithm
    openai_client.py      naming, description/title/weight interpretation, on-demand explanations
    track_ingest.py        shared Spotify-track -> Song ingestion (import + adopt-sync)
    routers/
      auth.py             Spotify OAuth login/callback, token refresh
      import_.py          import a Spotify playlist into the review queue
      playlists.py        list/create/adopt playlists, weights, suggest names
      songs.py             queue, skip, multi-playlist assign, memberships
      spotify.py           browse the user's live Spotify playlists
  requirements.txt
  Dockerfile             for Cloud Run / GCP deployment
  start_windows.bat      Windows dev launcher (used by run_dev.bat)

frontend/               React + Vite
  src/
    api/client.js         axios wrapper around the backend API
    pages/
      LibraryPage.jsx      playlist cards + "Load My Playlists" browser
      ImportPage.jsx        paste a playlist URL
      ReviewQueue.jsx        one-by-one review: multi-select, skip, create-new
      CheckPlaylists.jsx      re-score existing playlist songs, remove poor fits
    components/
      PlaylistCard.jsx
      FitScoreMeter.jsx      segmented LED-style fit score meter
      WeightSliders.jsx        adjust a playlist's 4 scoring weights
    styles.css
  start_windows.bat      Windows dev launcher (used by run_dev.bat)

run_dev.sh               macOS/Linux: run backend + frontend together
run_dev.bat               Windows: run backend + frontend together
```

## How it works

The app has four tabs: **Library**, **Import New Songs**, **Review Queue**,
and **Check My Playlists**.

1. **Import New Songs** — paste a Spotify playlist URL/ID. The backend
   fetches every track, looks up each artist's genres, runs the
   audio-features provider on each track, and stores them as `queued` songs
   (`POST /api/import`).
2. **Review Queue** — `GET /api/queue/next` returns the oldest queued song
   plus its fit score (0–100) against every local playlist. The song's
   readouts show genre, tempo (BPM, reference only), tempo **character**,
   mood, energy, danceability, length, and vibe tags — musical key and the
   AI-vs-Spotify analysis source were dropped from this view since neither
   affects a matching decision. For each song you can:
   - **Check any number of playlists** (multi-select) and hit "Assign to N
     playlists" — `POST /api/songs/{id}/assign-batch` writes the track to
     every chosen Spotify playlist in one go.
   - **Skip** the track — `POST /api/songs/{id}/skip` sets it aside
     (`SongStatus.SKIPPED`); a "bring them back" control requeues all
     skipped tracks (`POST /api/queue/requeue-skipped`).
   - **Create a new playlist** for just this song, with OpenAI suggesting
     names based on its genre/mood/tempo (`POST
     /api/songs/{id}/suggest-playlist-name`) — the new playlist is created
     on Spotify and auto-selected alongside anything else you checked.
   - If the song is **already a member of a playlist** (e.g. you adopted
     that playlist and synced its tracks *after* this song was queued from
     somewhere else), that row is pre-checked and tagged "already here" -
     this is a pure local-database lookup (`song.memberships`, already
     loaded with the song), so it adds no Spotify calls and no meaningful
     latency to loading each song.
3. **Library** — `GET /api/playlists` returns every local playlist with its
   member songs and a live-computed aggregate sonic profile (avg tempo, avg
   energy/danceability, genre and mood distribution). Fit scores shown next
   to each song reflect what they scored against that playlist when added.
   A **"Load My Playlists"** button (`GET /api/spotify/my-playlists`) fetches
   every playlist on your Spotify account — owned or followed — so you can
   **"+ Add to sorter"** (`POST /api/playlists/adopt`) any existing one as a
   destination, instead of only being able to create brand-new playlists.
   Followed-only playlists (not owned, not collaborative) are hidden by
   default, since Spotify won't let this app read *or* write to them
   regardless of any adoption attempt - a checkbox reveals them if you want
   to see the full list anyway. Each row also has a checkbox - check several (or "Select all") and hit
   "Add N selected to sorter" to adopt a batch in one go, with a progress
   counter since each one syncs and analyzes its existing tracks in turn.
   Adopting also pulls in that playlist's *existing* tracks and analyzes
   them, so its sonic profile is real from the start rather than blank
   (a blank profile makes every fit-score category fall back to a neutral
   50 - see the note below). This only works for playlists you own or
   collaborate on (same Feb 2026 restriction as importing); if Spotify won't
   allow it, the playlist is still adopted, just starts with an empty
   profile, and the UI says so. Every playlist card also has a
   **"▸ Scoring weights"** panel — four sliders (Theme & Vibe, Genre &
   Style, Mood / Emotional Tone, Energy & Tempo) initialized from an AI
   suggestion based on the playlist's name/description, freely adjustable
   from there (see the fit-score section below). If a playlist's
   accumulated songs have drifted from its stated title/description, the
   card also shows a small **"⚠ Drifting"** badge with a short explanation.
4. **Manual override** — because a song can now belong to more than one
   playlist at once (see [Data model](#data-model-note) below), each song
   row on a playlist card (and in Check My Playlists) has two remove
   options: ✕ just removes it from that one playlist, and ↺ removes it AND
   sends it back to the front of the Review Queue for reconsideration
   (`DELETE /api/songs/{id}/memberships/{playlist_id}?requeue=true`) - even
   if it's still a member of other playlists. A song automatically returns
   to the queue anyway if a removal leaves it with zero playlists; ↺ is for
   when you want that even though it isn't down to zero yet. Either path
   jumps the song to the very front (lowest `queue_position`) rather than
   the back of whatever's already queued.
5. **Removing a playlist** — the 🗑 button on a playlist card
   (`DELETE /api/playlists/{id}`) removes it from the local Library only.
   It does **not** delete or unfollow the real playlist on Spotify, and
   doesn't touch its tracks there - it just stops this app from tracking
   it. Songs that were only assigned to that playlist (nowhere else) go
   back to the review queue instead of disappearing. You can always bring
   a removed playlist back later via "Load My Playlists."
6. **Name suggestions** — the "Suggest names" button on a playlist card calls
   `POST /api/playlists/{id}/suggest-names`, which sends the playlist's
   genre/mood/tempo profile to OpenAI and returns 3–5 names in the style of
   real streaming-editor playlist titles. Both this and the single-song
   naming call in the Review Queue also pass along every other playlist's
   name, so suggestions won't just reword a playlist you already have — if a
   strong match already exists, the "Create new playlist" panel says so
   before you commit to a new one.
7. **Check My Playlists** — pick a playlist from the dropdown; its cover
   image displays prominently in the header alongside the page title. If
   its accumulated songs have drifted from its stated title/description, a
   **"⚠ Drifting"** badge appears with a short explanation (see the
   fit-score section below). `GET /api/playlists/{id}/check` re-scores
   every song already in the playlist against its *current* profile (not
   the stale score from when it was added). Each song is scored
   **leave-one-out** — against the rest of the playlist, excluding itself —
   so a song's own attributes don't inflate the very average it's being
   judged against. Songs are sorted worst-fit-first, with a "was N" note
   if the score has drifted 15+ points since assignment. Each row has a ▸
   toggle showing the four categories plus a free, instant explanation, and
   a "✦ Explain further" button for a richer on-demand OpenAI explanation.
   A ✕ removes poor fits (reusing the same
   `DELETE /api/songs/{id}/memberships/{playlist_id}` endpoint as playlist
   cards) and a ↺ removes-and-requeues for reconsideration. The page also
   has an editable **description** field
   (`PUT /api/playlists/{id}/description`) and the same **scoring-weight
   sliders** as playlist cards (`PUT /api/playlists/{id}/weights`) — both
   feed scoring (see the fit-score section below). The check itself makes
   an OpenAI call only for a genre/vibe tag it hasn't embedded before (see
   `app/embeddings.py`'s cache) — everything else, including every song's
   numeric score, is cached/local, so this stays fast after the first pass
   regardless of playlist size; only the description save and the
   on-demand explain button always make a live call.

### Data model note

A song can belong to multiple playlists, so `Song` and `Playlist` are joined
through a `PlaylistSong` association table (`app/models.py`) rather than a
single foreign key — it also stores the fit score the song had against that
specific playlist at the moment it was added, which is what's shown on each
song row in the Library.

### Fit score algorithm (`app/fit_score.py`)

Every candidate song is scored against a synthesized **Composite Target
Profile**, not any single signal in isolation. The Composite Target blends
three independent sources - each a different facet of the playlist's
identity:

| Source | Weight | What it captures |
|---|---|---|
| Description | 40% | The playlist's own written description - the primary anchor, since it's the clearest statement of intent someone actually wrote |
| Title | 30% | What the playlist's own **name** implies (e.g. "Epic Uplifting Anthems" → vibe tags `["epic", "uplifting"]`) |
| Member songs | 30% | The actual accumulated tracks |

A source that's entirely absent (no description written, a brand-new
playlist with zero songs) has its share proportionally redistributed among
whichever sources ARE present. Each source is normalized to sum to 1.0
internally before blending, so a source with many accumulated tags (50
songs) can't dominate one with a single tag (a title) purely by tag-count -
verified directly: with 10 conflicting real songs, description+title held
exactly their intended 70% combined share of the blend; with 50 conflicting
songs, still exactly 70%. It does not fade as the playlist grows.

Four scoring categories, each weighted per playlist (`Playlist.weights`,
JSON column, AI-suggested at create/adopt time via
`openai_client.suggest_scoring_weights` and freely editable via sliders -
`DEFAULT_WEIGHTS` in `fit_score.py` is only a fallback):

| Category | Default weight | How it's scored |
|---|---|---|
| **Theme & Vibe** | 30% | Vibe-tag semantic similarity (OpenAI embeddings) to the Composite Target's broader thematic/cinematic character |
| **Genre & Style** | 25% | Genre semantic similarity, with a fusion-tolerance boost for modern hybrid genres (see below) |
| **Mood / Emotional Tone** | 25% | Mood similarity via semantic clusters/coordinates, blended with a hidden aggression/darkness signal (see below) |
| **Energy & Tempo** | 20% | A smooth Gaussian gradient on the exact BPM difference, blended with energy/danceability closeness |

**Semantic matching, not string matching.** Genre and vibe-tag similarity
uses continuous OpenAI embeddings (`app/embeddings.py`, cached per unique
tag so scoring stays fast after a warm-up) rather than substring matching -
concepts that are related but share no substring ("anthemic" and "epic")
still score highly. Mood/emotional-word similarity uses two complementary
mechanisms: an explicit synonym-cluster dictionary
(`EMOTIONAL_CLUSTERS` - e.g. `{"happy", "uplifting", "euphoric", "playful",
"positive", ...}` and `{"dark", "gritty", "melancholic", "moody", ...}`)
that catches colloquial words outside the fixed mood vocabulary, plus a
valence/arousal coordinate system (`MOOD_COORDINATES`, based on the
circumplex model of affect) for the 8 canonical mood categories - two words
in the same cluster score 90+ regardless of exact string identity, while
genuinely opposite moods still score near 0.

**Hidden aggression/darkness index.** Each song's OpenAI analysis includes
a hidden `aggression_darkness_index` (0-100, `audio_features.py`) that is
**never exposed via the API or a UI slider** - it exists purely to make
Mood/Emotional Tone scoring more robust, since a categorical mood label
alone can be ambiguous (an "energetic" tag doesn't say whether that energy
is joyful or aggressive). It's absorbed directly into the scoring math (30%
of the Mood/Emotional Tone blend) so a high-energy JOYFUL song can't score
well against a high-energy AGGRESSIVE playlist just because their mood
labels or raw energy happen to overlap - verified directly: two songs with
*identical* mood label ("energetic") and *identical* energy/tempo scored
70 vs 75 against an aggressive-dominant playlist purely from this hidden
signal, and a genuinely joyful/euphoric song scored 29 (vibe-vetoed) against
an aggressive metal playlist despite a perfect genre and tempo match.

**Gradient BPM scoring, not flat category penalties.** Tempo scoring
compares the song's exact BPM against the Composite Target's blended BPM
with a smooth Gaussian falloff (`_bpm_gradient_score`, `BPM_GAUSSIAN_SIGMA
= 22`) - no discrete tempo-category buckets, no flat penalty for crossing
a category boundary. A 10 BPM gap barely matters (~90), a 30 BPM gap is a
real but not crushing penalty (~40), 50+ BPM apart is near zero.
`Song.tempo_character` (a human-readable "slow/ambient" / "mid-tempo/groove"
/ "fast/driving" descriptor) is still recorded and shown in the UI, it's
just not consumed by scoring math anymore.

**Genre fusion tolerance.** Modern hybrid genres ("trap metal", "industrial
hip-hop") often don't share strong text/embedding similarity with a
playlist's primary genre despite genuinely belonging there. If a song's
energy AND aggression/darkness profile are both close to the target's,
that's treated as corroborating evidence and a `GENRE_FUSION_BOOST` (20
points) softens what a pure lexical/embedding comparison alone would score
- verified directly: the same moderately-related genre tag scored 70 with
matching energy/aggression vs. 50 without.

**Vibe veto.** If Theme & Vibe OR Mood/Emotional Tone falls below
`VIBE_VETO_THRESHOLD` (35), the FINAL blended score gets multiplied by
`VIBE_VETO_MULTIPLIER` (0.45) regardless of how well genre/tempo happen to
align - a strong genre+tempo match can never rescue a song whose thematic
or emotional core is a complete mismatch. Checked on the raw mood-only
score too (not just the aggression-blended Mood/Emotional Tone total), so
a coincidental energy match can't mask a genuine mood clash. The
explanation string leads with a `⚠ vibe veto` phrase when this fires.

**Playlist Drift Warning** (`detect_playlist_drift`, surfaced as
`is_drifting`/`drift_comment` on both playlist cards and Check My
Playlists). Compares the playlist's ACTUAL accumulated songs against its
ORIGINAL title+description premise alone (no member songs in that side of
the comparison) to catch "echo chamber" drift - e.g. a playlist described
as upbeat/happy that has quietly accumulated mostly dark/melancholic songs.
Pure local computation (mood coordinates/clusters plus cheap substring
matching for genre/vibe), so it's free to run on every playlist load. A
drifting playlist still scores new candidate songs against its ORIGINAL
stated intent (the 70% description+title share), not the drifted content -
verified directly: a song matching the stated premise scored 78 against a
"drifted" playlist whose actual songs had moved to a completely different
genre and mood.

**Explanations:** every score comes with a fast, **local**, template-built
explanation string (`_build_explanation`) built from which specific tags
actually resembled each other and how closely. No LLM call, so it's free
and instant everywhere a score appears. Click the ▸ next to any score (in
the Review Queue or Check My Playlists) to see this plus the four
sub-scores that produced it. For an even richer, on-demand explanation,
Check My Playlists also has a "✦ Explain further" button
(`GET /playlists/{id}/songs/{song_id}/explain`) that makes a live OpenAI
call - kept deliberately separate so that cost only applies to a song you
actually click on, never automatically for a whole list.

An empty playlist (no songs, no usable title or description signal) scores
every category at a neutral 50 so it isn't unfairly penalized or inflated.
The Review Queue and Check My Playlists key off `has_signal` (not
`song_count > 0`) to decide whether a score is a real match - a playlist
with only a description/title signal and zero songs still counts as real,
since it has genuine (if provisional) evidence to score against.

## Prerequisites

- Python 3.11+ (Windows: install from [python.org](https://www.python.org/downloads/) and check **"Add python.exe to PATH"** during setup)
- Node.js 18+
- A [Spotify Developer](https://developer.spotify.com/dashboard) app
- An [OpenAI API key](https://platform.openai.com/api-keys)

This runs identically on **Windows, macOS, and Linux** — see the OS-specific
commands in [Local setup](#local-setup) below.

## Spotify app setup

1. Create an app at the Spotify Developer Dashboard.
2. Add this exact Redirect URI: `http://127.0.0.1:8000/api/auth/callback`
3. Copy the Client ID and Client Secret into `backend/.env` (see below).

## Local setup

> **Upgrading an existing checkout?** The database schema has changed
> repeatedly (songs can belong to multiple playlists via a `PlaylistSong`
> table, there's a `skipped` status, `primary_genre`/`secondary_genre` were
> replaced with a `genres` list, a `TagEmbedding` cache table was added
> along with `Song.tempo_character`, `Playlist.weights`/`title_signal`/
> `description_signal`, and most recently `Song.aggression_darkness_index`
> - a hidden field, never exposed via the API). This project has no
> migration tooling, so delete `backend/data/app.db` before starting the
> backend — it's recreated automatically on first run. You'll need to
> re-import and re-sort, but your real Spotify playlists are untouched
> either way.

### macOS / Linux

```bash
# 1. Backend env
cp backend/.env.example backend/.env
# edit backend/.env with your Spotify + OpenAI credentials

# 2. Frontend env
cp frontend/.env.example frontend/.env
# defaults are fine for local dev

# 3. Run everything
./run_dev.sh
```

`run_dev.sh` creates the backend virtualenv, installs both sets of
dependencies, and starts the API (`:8000`) and the frontend (`:5173`)
together in one terminal.

If you'd rather run them in two terminals:

```bash
# terminal 1
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# terminal 2
cd frontend
npm install
npm run dev
```

### Windows

```bat
:: 1. Backend env
copy backend\.env.example backend\.env
:: edit backend\.env with your Spotify + OpenAI credentials

:: 2. Frontend env
copy frontend\.env.example frontend\.env
:: defaults are fine for local dev

:: 3. Run everything
run_dev.bat
```

Double-click `run_dev.bat` in File Explorer, or run it from `cmd.exe` or
PowerShell (`.\run_dev.bat`). It opens two windows — one running the backend
(creating a `.venv`, installing dependencies, then `uvicorn`), one running
the frontend (`npm install`, then `npm run dev`). Close either window (or
Ctrl+C inside it) to stop that half. First run takes a bit longer while
dependencies install; the same window keeps reusing them after that.

If you'd rather run them in two terminals yourself:

```bat
:: terminal 1 (cmd.exe)
cd backend
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

```powershell
# terminal 1 (PowerShell) - if activation is blocked, see note below
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

```bat
:: terminal 2 (either shell)
cd frontend
npm install
npm run dev
```

> **PowerShell execution policy:** if `Activate.ps1` or `run_dev.bat` refuses
> to run with a "scripts disabled" error, either use `cmd.exe` instead, or run
> once as yourself: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
> `run_dev.bat` is a plain batch file and is unaffected by this setting even
> when launched from PowerShell.

Both platforms create the SQLite database automatically at
`backend/data/app.db` on first run. Once both services are up, open
**http://127.0.0.1:5173** and connect your Spotify account.

## Deployment

**Backend (GCP / Cloud Run)**

```bash
cd backend
gcloud run deploy sorted-backend --source . --region us-central1 --allow-unauthenticated --set-env-vars SPOTIFY_CLIENT_ID=...,SPOTIFY_CLIENT_SECRET=...,SPOTIFY_REDIRECT_URI=https://YOUR_BACKEND_URL/api/auth/callback,OPENAI_API_KEY=...,FRONTEND_URL=https://YOUR_FRONTEND.vercel.app,SECRET_KEY=...,ENVIRONMENT=production
```

(Written as one line on purpose — line-continuation characters differ between
bash, PowerShell, and cmd, so this form runs unmodified in any of them.)

Cloud Run's filesystem is ephemeral, so for anything beyond a demo, point
`DATABASE_URL` at a persistent store (e.g. Cloud SQL) instead of the default
SQLite file — swap the connection string in `backend/.env`; no code changes
needed since SQLAlchemy handles the rest.

Update the Redirect URI in the Spotify Developer Dashboard to match your
deployed backend URL once you have it.

**Frontend (Vercel)**

```bash
cd frontend
vercel --prod
```

Set `VITE_API_BASE_URL` in the Vercel project's environment variables to
your deployed backend URL.

## Notes & limitations

- No automated tests, per project scope.
- No migration tooling — schema changes require deleting
  `backend/data/app.db` and starting fresh (see the callout in
  [Local setup](#local-setup)).
- Genre/tempo/mood values are inferred (see the callout at the top) — treat
  them as a strong starting point for sorting, not ground truth.
- Spotify tokens live in a signed, httpOnly server-side session cookie
  (`SessionMiddleware`); nothing is persisted to disk beyond the SQLite
  playlist/song records.
