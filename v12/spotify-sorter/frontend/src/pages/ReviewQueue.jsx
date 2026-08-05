import { useEffect, useState } from "react";
import FitScoreMeter from "../components/FitScoreMeter.jsx";
import {
  fetchNextInQueue,
  fetchQueueCount,
  assignSongBatch,
  skipSong,
  requeueSkipped,
  createPlaylist,
  suggestNewPlaylistName,
} from "../api/client.js";

function formatMs(ms) {
  if (!ms) return "";
  const totalSec = Math.round(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export default function ReviewQueue() {
  const [current, setCurrent] = useState(null); // { song, fit_scores, remaining_in_queue }
  const [skippedCount, setSkippedCount] = useState(0);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [extraPlaylists, setExtraPlaylists] = useState([]); // playlists created for this song, not yet in fit_scores
  const [expandedBreakdowns, setExpandedBreakdowns] = useState(new Set());
  const [loading, setLoading] = useState(true);
  const [assigning, setAssigning] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [error, setError] = useState(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [suggestions, setSuggestions] = useState(null);
  const [suggesting, setSuggesting] = useState(false);
  const [creatingPlaylist, setCreatingPlaylist] = useState(false);

  async function loadNext() {
    setLoading(true);
    setError(null);
    try {
      const [data, counts] = await Promise.all([fetchNextInQueue(), fetchQueueCount()]);
      setCurrent(data);
      setSkippedCount(counts.skipped || 0);
      // Only pre-select a playlist whose score reflects real signal (real
      // songs and/or a usable description) - a truly-empty profile's "50"
      // is a neutral fallback, not a match.
      const scored = (data?.fit_scores || []).filter((f) => f.has_signal);
      const topId = scored[0]?.playlist_id;
      // Also pre-select (and later flag) any playlist this song is already
      // a member of - can happen if a playlist you adopted after this song
      // was queued turns out to already contain it.
      const alreadyIn = data?.song?.member_playlist_ids || [];
      const initialSelection = new Set(alreadyIn);
      if (topId != null) initialSelection.add(topId);
      setSelectedIds(initialSelection);
      setExtraPlaylists([]);
      setExpandedBreakdowns(new Set());
      setCreateOpen(false);
      setNewName("");
      setSuggestions(null);
    } catch (err) {
      setError("Couldn't reach the queue. Is the backend running?");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadNext();
  }, []);

  function toggleSelected(id) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleBreakdown(id) {
    setExpandedBreakdowns((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleAssign() {
    if (!current) return;
    if (selectedIds.size === 0) {
      setError("Select at least one playlist, use Skip, or create a new one below.");
      return;
    }
    setAssigning(true);
    setError(null);
    try {
      await assignSongBatch(current.song.id, Array.from(selectedIds));
      await loadNext();
    } catch (err) {
      setError(err.response?.data?.detail || "Couldn't write that track to Spotify.");
    } finally {
      setAssigning(false);
    }
  }

  async function handleSkip() {
    if (!current) return;
    setSkipping(true);
    setError(null);
    try {
      await skipSong(current.song.id);
      await loadNext();
    } catch (err) {
      setError(err.response?.data?.detail || "Couldn't skip that track.");
    } finally {
      setSkipping(false);
    }
  }

  async function handleRequeueSkipped() {
    setError(null);
    try {
      await requeueSkipped();
      await loadNext();
    } catch (err) {
      setError(err.response?.data?.detail || "Couldn't bring skipped tracks back.");
    }
  }

  async function handleSuggestNames() {
    if (!current) return;
    setSuggesting(true);
    setError(null);
    try {
      const data = await suggestNewPlaylistName(current.song.id);
      setSuggestions(data.suggestions);
    } catch (err) {
      setError(err.response?.data?.detail || "Couldn't reach OpenAI. Try again.");
    } finally {
      setSuggesting(false);
    }
  }

  async function handleCreatePlaylist() {
    if (!newName.trim()) return;
    setCreatingPlaylist(true);
    setError(null);
    try {
      const created = await createPlaylist(newName.trim());
      setExtraPlaylists((prev) => [...prev, { playlist_id: created.id, playlist_name: created.name }]);
      setSelectedIds((prev) => new Set(prev).add(created.id));
      setCreateOpen(false);
      setNewName("");
      setSuggestions(null);
    } catch (err) {
      setError(err.response?.data?.detail || "Couldn't create the playlist on Spotify.");
    } finally {
      setCreatingPlaylist(false);
    }
  }

  if (loading) return <div className="empty-state">Cueing up the next track…</div>;
  if (error && !current) return <div className="inline-error">{error}</div>;

  if (!current) {
    return (
      <div className="empty-state">
        <p>Queue's clear.</p>
        {skippedCount > 0 ? (
          <>
            <p className="page-sub">
              {skippedCount} track{skippedCount === 1 ? "" : "s"} skipped.
            </p>
            <button className="btn-primary" onClick={handleRequeueSkipped}>
              Bring skipped tracks back
            </button>
          </>
        ) : (
          <p className="page-sub">Import another playlist to keep sorting.</p>
        )}
      </div>
    );
  }

  const { song, fit_scores, remaining_in_queue } = current;
  const scoredEntries = fit_scores.filter((f) => f.has_signal);
  const topScore = scoredEntries[0] || null;
  const options = [
    ...fit_scores,
    ...extraPlaylists.map((p) => ({
      playlist_id: p.playlist_id,
      playlist_name: p.playlist_name,
      score: null,
      song_count: 0,
      has_signal: false,
      isNew: true,
    })),
  ];

  return (
    <div className="review-page">
      <div className="review-progress">
        {remaining_in_queue} left in the queue
        {skippedCount > 0 && (
          <>
            {" "}
            · {skippedCount} skipped —{" "}
            <button className="btn-text" onClick={handleRequeueSkipped}>
              bring them back
            </button>
          </>
        )}
      </div>

      <div className="channel-strip">
        <div className="channel-art-wrap">
          {song.image_url ? (
            <img className="channel-art" src={song.image_url} alt="" />
          ) : (
            <div className="channel-art channel-art-placeholder">♫</div>
          )}
        </div>

        <div className="channel-info">
          <h2>{song.name}</h2>
          <div className="channel-artist">
            {song.artists} {song.album ? `· ${song.album}` : ""}
          </div>

          <div className="readout-row">
            <Readout
              label="Genre"
              value={song.genres?.[0] || "—"}
              sub={song.genres?.length > 1 ? song.genres.slice(1).join(", ") : undefined}
            />
            <Readout label="Tempo" value={song.tempo ? `${Math.round(song.tempo)}` : "—"} sub="BPM" />
            <Readout label="Character" value={song.tempo_character || "—"} />
            <Readout label="Mood" value={song.mood || "—"} />
          </div>
          <div className="readout-row">
            <Readout label="Energy" value={song.energy != null ? Math.round(song.energy * 100) : "—"} sub="/100" />
            <Readout
              label="Danceability"
              value={song.danceability != null ? Math.round(song.danceability * 100) : "—"}
              sub="/100"
            />
            <Readout label="Length" value={formatMs(song.duration_ms)} />
            <Readout
              label="Vibe"
              value={song.vibe_tags?.[0] || "—"}
              sub={song.vibe_tags?.length > 1 ? song.vibe_tags.slice(1).join(", ") : undefined}
            />
          </div>
        </div>
      </div>

      <div className="fit-section">
        <h3>Assign to playlists</h3>
        <p className="page-sub">
          {topScore ? (
            <>
              Best match:{" "}
              <strong>
                {topScore.playlist_name} ({topScore.score})
              </strong>
              . Check as many playlists as fit, skip this track, or create a new one below.
            </>
          ) : (
            "None of your playlists have enough history yet for a confident match — check any that fit, skip this track, or create a new one below."
          )}
        </p>
        {song.member_playlist_ids?.length > 0 && (
          <p className="page-sub fit-already-note">
            Marked <span className="tag tag-genre fit-option-new-tag">already here</span> means this song is
            already in that playlist (found when it was added to the sorter). Unchecking it won't remove the
            song — do that from its playlist card in the Library instead.
          </p>
        )}

        <div className="fit-list">
          {options.map((entry) => {
            const isExpanded = expandedBreakdowns.has(entry.playlist_id);
            return (
              <div key={entry.playlist_id} className="fit-option-wrap">
                <label
                  className={`fit-option ${selectedIds.has(entry.playlist_id) ? "fit-option-selected" : ""}`}
                >
                  <input
                    type="checkbox"
                    checked={selectedIds.has(entry.playlist_id)}
                    onChange={() => toggleSelected(entry.playlist_id)}
                  />
                  <span className="fit-option-name">
                    {entry.playlist_name}
                    {entry.isNew && <span className="tag tag-mood fit-option-new-tag">new</span>}
                    {song.member_playlist_ids?.includes(entry.playlist_id) && (
                      <span className="tag tag-genre fit-option-new-tag" title="This song is already in this playlist">
                        already here
                      </span>
                    )}
                  </span>
                  {entry.has_signal ? (
                    <>
                      <FitScoreMeter score={entry.score} compact highlight={entry === topScore} />
                      <button
                        type="button"
                        className="btn-why"
                        title="Show score breakdown"
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          toggleBreakdown(entry.playlist_id);
                        }}
                      >
                        {isExpanded ? "▾" : "▸"}
                      </button>
                    </>
                  ) : (
                    <span className="fit-option-no-score">{entry.isNew ? "just created" : "no songs yet"}</span>
                  )}
                </label>
                {isExpanded && entry.breakdown && (
                  <div className="fit-breakdown-wrap">
                    <div className="fit-breakdown">
                      <span>Theme/Vibe {entry.breakdown.theme_vibe}</span>
                      <span>Genre/Style {entry.breakdown.genre_style}</span>
                      <span>Mood/Tone {entry.breakdown.emotional_tone}</span>
                      <span>Energy/Tempo {entry.breakdown.tempo_energy}</span>
                    </div>
                    {entry.explanation && <p className="fit-explanation">{entry.explanation}</p>}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="create-inline">
          {!createOpen ? (
            <button className="btn-text" onClick={() => setCreateOpen(true)}>
              + Create a new playlist for this song
            </button>
          ) : (
            <div className="create-inline-panel">
              {topScore && topScore.score >= 70 && (
                <div className="create-inline-warning">
                  Heads up: <strong>{topScore.playlist_name}</strong> already scores {topScore.score}/100
                  for this song — you may not need a new one.
                </div>
              )}
              <div className="create-inline-row">
                <input
                  placeholder="New playlist name…"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                />
                <button className="btn-secondary" onClick={handleSuggestNames} disabled={suggesting}>
                  {suggesting ? "Thinking…" : "✦ Suggest names"}
                </button>
              </div>

              {suggestions && (
                <div className="suggestion-chips">
                  {suggestions.map((s) => (
                    <button key={s} type="button" className="chip" onClick={() => setNewName(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              )}

              <div className="create-inline-row">
                <button
                  className="btn-primary"
                  onClick={handleCreatePlaylist}
                  disabled={creatingPlaylist || !newName.trim()}
                >
                  {creatingPlaylist ? "Creating…" : "Create & select"}
                </button>
                <button className="btn-ghost" onClick={() => setCreateOpen(false)}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {error && <div className="inline-error">{error}</div>}

      <div className="review-actions">
        <button className="btn-ghost btn-large" onClick={handleSkip} disabled={skipping || assigning}>
          {skipping ? "Skipping…" : "Skip this track"}
        </button>
        <button className="btn-primary btn-large" onClick={handleAssign} disabled={assigning || skipping}>
          {assigning
            ? "Sending to Spotify…"
            : `Assign to ${selectedIds.size} playlist${selectedIds.size === 1 ? "" : "s"}`}
        </button>
      </div>
    </div>
  );
}

function Readout({ label, value, sub }) {
  return (
    <div className="readout">
      <div className="readout-label">{label}</div>
      <div className="readout-value">
        {value}
        {sub ? <span className="readout-sub"> {sub}</span> : null}
      </div>
    </div>
  );
}
