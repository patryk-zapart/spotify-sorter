import { useState } from "react";
import FitScoreMeter from "./FitScoreMeter.jsx";
import WeightSliders from "./WeightSliders.jsx";
import { suggestNames, addSongToPlaylist, removeSongFromPlaylist, deletePlaylist } from "../api/client.js";

export default function PlaylistCard({ playlist, allPlaylists, onChanged }) {
  const [suggestions, setSuggestions] = useState(null);
  const [loadingNames, setLoadingNames] = useState(false);
  const [nameError, setNameError] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [showWeights, setShowWeights] = useState(false);
  const [busySongId, setBusySongId] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  const profile = playlist.sonic_profile;

  async function handleDelete() {
    const count = profile.song_count;
    const confirmed = window.confirm(
      `Remove "${playlist.name}" from your Library?\n\n` +
        `This only stops tracking it here — the real playlist and its ${count} track${count === 1 ? "" : "s"} ` +
        `stay untouched on Spotify. Any songs only assigned here will go back to the review queue.`
    );
    if (!confirmed) return;

    setDeleting(true);
    setDeleteError(null);
    try {
      await deletePlaylist(playlist.id);
      onChanged?.();
    } catch (err) {
      setDeleteError(err.response?.data?.detail || "Couldn't remove that playlist. Try again.");
      setDeleting(false);
    }
  }

  async function handleSuggestNames() {
    setLoadingNames(true);
    setNameError(null);
    try {
      const data = await suggestNames(playlist.id);
      setSuggestions(data.suggestions);
    } catch (err) {
      setNameError(err.response?.data?.detail || "Couldn't reach OpenAI. Try again.");
    } finally {
      setLoadingNames(false);
    }
  }

  async function handleRemove(songId, requeue) {
    setBusySongId(songId);
    try {
      await removeSongFromPlaylist(songId, playlist.id, requeue);
      onChanged?.();
    } finally {
      setBusySongId(null);
    }
  }

  async function handleAddTo(songId, otherPlaylistId) {
    if (!otherPlaylistId) return;
    setBusySongId(songId);
    try {
      await addSongToPlaylist(songId, Number(otherPlaylistId));
      onChanged?.();
    } finally {
      setBusySongId(null);
    }
  }

  const visibleSongs = expanded ? playlist.songs : playlist.songs.slice(0, 5);

  return (
    <div className="playlist-card">
      <div className="playlist-card-head">
        {playlist.image_url ? (
          <img className="playlist-art" src={playlist.image_url} alt="" />
        ) : (
          <div className="playlist-art playlist-art-placeholder">♫</div>
        )}
        <div className="playlist-head-text">
          <h3>{playlist.name}</h3>
          <div className="playlist-meta">
            {profile.song_count} track{profile.song_count === 1 ? "" : "s"}
            {profile.avg_tempo ? ` · ${Math.round(profile.avg_tempo)} BPM avg` : ""}
          </div>
        </div>
        <button
          className="btn-icon playlist-delete-btn"
          title="Remove from Library"
          onClick={handleDelete}
          disabled={deleting}
        >
          {deleting ? "…" : "🗑"}
        </button>
      </div>

      {deleteError && <div className="inline-error">{deleteError}</div>}

      {playlist.is_drifting && (
        <div className="drift-warning">
          <span className="drift-warning-badge">⚠ Drifting</span>
          <em>{playlist.drift_comment}</em>
        </div>
      )}

      <div className="playlist-tags">
        {Object.entries(profile.genre_distribution)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 3)
          .map(([genre]) => (
            <span key={genre} className="tag tag-genre">
              {genre}
            </span>
          ))}
        {Object.entries(profile.vibe_distribution || {})
          .sort((a, b) => b[1] - a[1])
          .slice(0, 2)
          .map(([vibe]) => (
            <span key={vibe} className="tag tag-vibe">
              {vibe}
            </span>
          ))}
        {Object.entries(profile.mood_distribution)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 2)
          .map(([mood]) => (
            <span key={mood} className="tag tag-mood">
              {mood}
            </span>
          ))}
      </div>

      <div className="playlist-actions">
        <button className="btn-secondary" onClick={handleSuggestNames} disabled={loadingNames}>
          {loadingNames ? "Naming…" : "✦ Suggest names"}
        </button>
        <button className="btn-text" onClick={() => setShowWeights((s) => !s)}>
          {showWeights ? "▾" : "▸"} Scoring weights
        </button>
      </div>

      {showWeights && (
        <WeightSliders playlistId={playlist.id} weights={playlist.weights} onSaved={onChanged} />
      )}

      {nameError && <div className="inline-error">{nameError}</div>}
      {suggestions && (
        <ul className="name-suggestions">
          {suggestions.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      )}

      <div className="playlist-songs">
        {playlist.songs.length === 0 && (
          <div className="empty-note">No tracks assigned yet — send some over from the review queue.</div>
        )}
        {visibleSongs.map((song) => {
          const otherPlaylists = allPlaylists.filter(
            (p) => p.id !== playlist.id && !song.member_playlist_ids?.includes(p.id)
          );
          const busy = busySongId === song.id;
          return (
            <div className="song-row" key={song.id}>
              {song.image_url && <img className="song-thumb" src={song.image_url} alt="" />}
              <div className="song-row-text">
                <div className="song-row-name">{song.name}</div>
                <div className="song-row-artist">{song.artists}</div>
              </div>
              {song.fit_score != null && <FitScoreMeter score={song.fit_score} compact />}
              <button
                className="btn-icon"
                title="Remove from this playlist"
                disabled={busy}
                onClick={() => handleRemove(song.id, false)}
              >
                ✕
              </button>
              <button
                className="btn-icon btn-icon-requeue"
                title="Remove and send to Review Queue for reconsideration"
                disabled={busy}
                onClick={() => handleRemove(song.id, true)}
              >
                ↺
              </button>
              {otherPlaylists.length > 0 && (
                <select
                  className="reassign-select"
                  value=""
                  disabled={busy}
                  onChange={(e) => handleAddTo(song.id, e.target.value)}
                  title="Also add to another playlist"
                >
                  <option value="">+ add to…</option>
                  {otherPlaylists.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              )}
            </div>
          );
        })}
        {playlist.songs.length > 5 && (
          <button className="btn-text" onClick={() => setExpanded((e) => !e)}>
            {expanded ? "Show less" : `Show all ${playlist.songs.length} tracks`}
          </button>
        )}
      </div>
    </div>
  );
}
