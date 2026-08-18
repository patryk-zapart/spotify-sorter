import { useEffect, useState } from "react";
import FitScoreMeter from "../components/FitScoreMeter.jsx";
import WeightSliders from "../components/WeightSliders.jsx";
import {
  fetchPlaylists,
  checkPlaylist,
  removeSongFromPlaylist,
  updatePlaylistDescription,
  explainSongFit,
} from "../api/client.js";

export default function CheckPlaylists() {
  const [playlists, setPlaylists] = useState([]);
  const [loadingPlaylists, setLoadingPlaylists] = useState(true);
  const [selectedPlaylistId, setSelectedPlaylistId] = useState(null);

  const [checkData, setCheckData] = useState(null); // { playlist_id, playlist_name, songs }
  const [loadingCheck, setLoadingCheck] = useState(false);
  const [error, setError] = useState(null);
  const [removingSongId, setRemovingSongId] = useState(null);

  const [expandedSongIds, setExpandedSongIds] = useState(new Set());
  const [explanations, setExplanations] = useState({}); // { [songId]: string }
  const [explainingSongId, setExplainingSongId] = useState(null);

  const [descriptionDraft, setDescriptionDraft] = useState("");
  const [savingDescription, setSavingDescription] = useState(false);
  const [descriptionSavedNote, setDescriptionSavedNote] = useState(null);
  const [descriptionError, setDescriptionError] = useState(null);

  useEffect(() => {
    fetchPlaylists()
      .then((data) => {
        setPlaylists(data);
        if (data.length > 0) setSelectedPlaylistId(data[0].id);
      })
      .catch(() => setError("Couldn't load your playlists. Is the backend running?"))
      .finally(() => setLoadingPlaylists(false));
  }, []);

  async function loadCheck(playlistId) {
    if (playlistId == null) return;
    setLoadingCheck(true);
    setError(null);
    try {
      const data = await checkPlaylist(playlistId);
      setCheckData(data);
      // The profile may have just changed (save, removal) - cached
      // explanations reference the OLD profile, so they're no longer
      // trustworthy. Re-fetch on next click rather than show stale text.
      setExplanations({});
    } catch (err) {
      setError(err.response?.data?.detail || "Couldn't check that playlist.");
      setCheckData(null);
    } finally {
      setLoadingCheck(false);
    }
  }

  useEffect(() => {
    if (selectedPlaylistId == null) return;
    setExpandedSongIds(new Set());
    setExplanations({});
    setDescriptionSavedNote(null);
    setDescriptionError(null);
    const current = playlists.find((p) => p.id === selectedPlaylistId);
    setDescriptionDraft(current?.description || "");
    loadCheck(selectedPlaylistId);
    // playlists intentionally omitted - only react to switching playlists,
    // not to playlists refreshing after a description save (handled below).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPlaylistId]);

  async function handleRemove(songId, requeue) {
    setRemovingSongId(songId);
    try {
      await removeSongFromPlaylist(songId, selectedPlaylistId, requeue);
      // Removing one song shifts the whole playlist's profile, so every
      // remaining song's live score needs recomputing - refetch rather
      // than just filtering the removed row out locally.
      await loadCheck(selectedPlaylistId);
    } catch (err) {
      setError(err.response?.data?.detail || "Couldn't remove that song.");
    } finally {
      setRemovingSongId(null);
    }
  }

  function toggleDetails(songId) {
    setExpandedSongIds((prev) => {
      const next = new Set(prev);
      if (next.has(songId)) next.delete(songId);
      else next.add(songId);
      return next;
    });
  }

  async function handleExplain(songId) {
    setExplainingSongId(songId);
    setError(null);
    try {
      const data = await explainSongFit(selectedPlaylistId, songId);
      setExplanations((prev) => ({ ...prev, [songId]: data.explanation }));
    } catch (err) {
      setError(err.response?.data?.detail || "Couldn't generate an explanation.");
    } finally {
      setExplainingSongId(null);
    }
  }

  async function handleSaveDescription() {
    setSavingDescription(true);
    setDescriptionError(null);
    setDescriptionSavedNote(null);
    try {
      await updatePlaylistDescription(selectedPlaylistId, descriptionDraft);
      const refreshed = await fetchPlaylists();
      setPlaylists(refreshed);
      setDescriptionSavedNote("Saved — every score below has been re-calculated using it.");
      await loadCheck(selectedPlaylistId);
    } catch (err) {
      setDescriptionError(err.response?.data?.detail || "Couldn't save that description.");
    } finally {
      setSavingDescription(false);
    }
  }

  async function handleWeightsSaved() {
    const refreshed = await fetchPlaylists();
    setPlaylists(refreshed);
    await loadCheck(selectedPlaylistId);
  }

  if (loadingPlaylists) {
    return <div className="empty-state">Loading your playlists…</div>;
  }

  if (playlists.length === 0) {
    return (
      <div className="empty-state">
        <p>No playlists in your Library yet.</p>
        <p className="page-sub">Create or adopt one first, then come back here to check its fit.</p>
      </div>
    );
  }

  const selectedPlaylist = playlists.find((p) => p.id === selectedPlaylistId);
  const savedDescription = selectedPlaylist?.description || "";
  const descriptionUnchanged = descriptionDraft === savedDescription;
  const sortedSongs = checkData ? [...checkData.songs].sort((a, b) => a.live_score - b.live_score) : [];

  return (
    <div className="check-page">
      <div className="dashboard-header">
        <div className="check-header-main">
          {selectedPlaylist?.image_url ? (
            <img className="check-playlist-cover" src={selectedPlaylist.image_url} alt="" />
          ) : (
            <div className="check-playlist-cover check-playlist-cover-placeholder">♫</div>
          )}
          <div>
            <h1>Check My Playlists</h1>
            <p className="page-sub">
              Re-scores every song already in a playlist against the rest of its current lineup -
              useful after a playlist has grown or shifted since a song was first added.
            </p>
          </div>
        </div>
        <select
          className="check-playlist-select"
          value={selectedPlaylistId ?? ""}
          onChange={(e) => setSelectedPlaylistId(Number(e.target.value))}
        >
          {playlists.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} ({p.sonic_profile.song_count})
            </option>
          ))}
        </select>
      </div>

      {checkData?.is_drifting && (
        <div className="drift-warning">
          <span className="drift-warning-badge">⚠ Drifting</span>
          <em>{checkData.drift_comment}</em>
        </div>
      )}

      {error && <div className="inline-error">{error}</div>}

      <div className="check-description-panel">
        <h3>Playlist description</h3>
        <p className="page-sub">
          Feeds into match scoring — genres, vibe, mood, and tempo it implies count toward every
          song's score here, which especially helps a brand-new or sparse playlist that doesn't
          have enough songs yet to score against on its own.
        </p>
        <textarea
          className="check-description-input"
          value={descriptionDraft}
          onChange={(e) => setDescriptionDraft(e.target.value)}
          placeholder="e.g. Downtempo chillout for rainy mornings, nothing above 100 BPM"
          rows={2}
        />
        <div className="check-description-actions">
          <button
            className="btn-primary"
            onClick={handleSaveDescription}
            disabled={savingDescription || descriptionUnchanged}
          >
            {savingDescription ? "Saving & re-analyzing…" : "Save & re-analyze"}
          </button>
          {descriptionSavedNote && <span className="check-description-note">{descriptionSavedNote}</span>}
        </div>
        {descriptionError && <div className="inline-error">{descriptionError}</div>}
      </div>

      <div className="check-description-panel">
        <h3>Scoring weights</h3>
        <p className="page-sub">
          How much each factor counts toward this playlist's match scores. Started from an AI
          suggestion based on the playlist's name/description (a workout playlist leans on Energy
          &amp; Tempo; a study playlist leans on Theme &amp; Vibe) - adjust freely from here.
        </p>
        {selectedPlaylist && (
          <WeightSliders
            playlistId={selectedPlaylist.id}
            weights={selectedPlaylist.weights}
            onSaved={handleWeightsSaved}
          />
        )}
      </div>

      {loadingCheck ? (
        <div className="empty-state">Re-scoring tracks…</div>
      ) : !checkData || checkData.songs.length === 0 ? (
        <div className="empty-state">
          <p>No songs in this playlist yet.</p>
          <p className="page-sub">Assign some from the Review Queue first.</p>
        </div>
      ) : (
        <div className="check-song-list">
          {sortedSongs.map((row) => {
            const { song } = row;
            const drifted = row.original_score != null && Math.abs(row.original_score - row.live_score) >= 15;
            const isExpanded = expandedSongIds.has(song.id);
            const explanation = explanations[song.id];
            const isExplaining = explainingSongId === song.id;
            return (
              <div className="check-song-wrap" key={song.id}>
                <div className="check-song-row">
                  {song.image_url ? (
                    <img className="song-thumb" src={song.image_url} alt="" />
                  ) : (
                    <div className="song-thumb-placeholder">♫</div>
                  )}
                  <div className="song-row-text">
                    <div className="song-row-name">{song.name}</div>
                    <div className="song-row-artist">
                      {song.artists}
                      {song.mood ? ` · ${song.mood}` : ""}
                      {song.tempo ? ` · ${Math.round(song.tempo)} BPM` : ""}
                    </div>
                  </div>
                  <FitScoreMeter score={row.live_score} compact />
                  {drifted && <span className="check-drift-note">was {row.original_score}</span>}
                  <button
                    type="button"
                    className="btn-why"
                    title="Show score details"
                    onClick={() => toggleDetails(song.id)}
                  >
                    {isExpanded ? "▾" : "▸"}
                  </button>
                  <button
                    className="btn-icon"
                    title="Remove from this playlist"
                    disabled={removingSongId === song.id}
                    onClick={() => handleRemove(song.id, false)}
                  >
                    {removingSongId === song.id ? "…" : "✕"}
                  </button>
                  <button
                    className="btn-icon btn-icon-requeue"
                    title="Remove and send to Review Queue for reconsideration"
                    disabled={removingSongId === song.id}
                    onClick={() => handleRemove(song.id, true)}
                  >
                    ↺
                  </button>
                </div>

                {isExpanded && (
                  <div className="check-song-details">
                    <div className="fit-breakdown">
                      <span>Theme/Vibe {row.live_breakdown.theme_vibe}</span>
                      <span>Genre/Style {row.live_breakdown.genre_style}</span>
                      <span>Mood/Tone {row.live_breakdown.emotional_tone}</span>
                      <span>Energy/Tempo {row.live_breakdown.tempo_energy}</span>
                    </div>
                    {row.live_explanation && <p className="check-explanation">{row.live_explanation}</p>}
                    {explanation ? (
                      <p className="check-explanation">{explanation}</p>
                    ) : (
                      <button className="btn-text" onClick={() => handleExplain(song.id)} disabled={isExplaining}>
                        {isExplaining ? "Thinking…" : "✦ Explain further"}
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
