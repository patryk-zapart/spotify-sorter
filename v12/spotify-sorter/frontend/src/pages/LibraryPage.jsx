import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import PlaylistCard from "../components/PlaylistCard.jsx";
import {
  fetchPlaylists,
  createPlaylist,
  fetchQueueCount,
  fetchMySpotifyPlaylists,
  adoptPlaylist,
} from "../api/client.js";

export default function LibraryPage() {
  const [playlists, setPlaylists] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [queueCounts, setQueueCounts] = useState({ remaining: 0, skipped: 0 });
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);

  // "Load My Playlists" browser - live Spotify data, separate from the
  // locally-tracked playlists shown in the grid below.
  const [spotifyPlaylists, setSpotifyPlaylists] = useState(null); // null = not loaded yet
  const [loadingSpotify, setLoadingSpotify] = useState(false);
  const [spotifyError, setSpotifyError] = useState(null);
  const [adoptingId, setAdoptingId] = useState(null);
  const [adoptNote, setAdoptNote] = useState(null);
  const [selectedSpotifyIds, setSelectedSpotifyIds] = useState(new Set());
  const [bulkAdding, setBulkAdding] = useState(false);
  const [bulkProgress, setBulkProgress] = useState(null); // { current, total }
  const [showFollowed, setShowFollowed] = useState(false);

  // Followed-only playlists (not owned, not collaborative) can't be read OR
  // written to by this app - Spotify's own permission model, unrelated to
  // any API deprecation - so they're hidden by default to cut clutter.
  // Anything already tracked stays visible regardless, so your Library view
  // stays complete.
  const followedOnlyPlaylists = (spotifyPlaylists || []).filter(
    (sp) => !sp.already_tracked && !sp.is_owner && !sp.collaborative
  );
  const visibleSpotifyPlaylists = (spotifyPlaylists || []).filter(
    (sp) => showFollowed || sp.already_tracked || sp.is_owner || sp.collaborative
  );

  const untrackedSpotifyPlaylists = visibleSpotifyPlaylists.filter((sp) => !sp.already_tracked);
  const allSpotifySelected =
    untrackedSpotifyPlaylists.length > 0 &&
    untrackedSpotifyPlaylists.every((sp) => selectedSpotifyIds.has(sp.spotify_playlist_id));

  async function load() {
    setLoading(true);
    try {
      const [pl, counts] = await Promise.all([fetchPlaylists(), fetchQueueCount()]);
      setPlaylists(pl);
      setQueueCounts(counts);
      setError(null);
    } catch (err) {
      setError("Couldn't load your playlists. Is the backend running?");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleCreate(e) {
    e.preventDefault();
    if (!newName.trim()) return;
    setCreating(true);
    try {
      await createPlaylist(newName.trim());
      setNewName("");
      setShowCreate(false);
      await load();
    } catch (err) {
      setError(err.response?.data?.detail || "Couldn't create the playlist on Spotify.");
    } finally {
      setCreating(false);
    }
  }

  async function handleLoadSpotifyPlaylists() {
    setLoadingSpotify(true);
    setSpotifyError(null);
    try {
      const data = await fetchMySpotifyPlaylists();
      setSpotifyPlaylists(data);
      setSelectedSpotifyIds(new Set());
    } catch (err) {
      setSpotifyError(err.response?.data?.detail || "Couldn't fetch your Spotify playlists.");
    } finally {
      setLoadingSpotify(false);
    }
  }

  async function handleAdopt(spotifyPlaylistId) {
    setAdoptingId(spotifyPlaylistId);
    setSpotifyError(null);
    setAdoptNote(null);
    try {
      const adopted = await adoptPlaylist(spotifyPlaylistId);
      setAdoptNote(adopted.sync_note || null);
      setSelectedSpotifyIds((prev) => {
        const next = new Set(prev);
        next.delete(spotifyPlaylistId);
        return next;
      });
      await load();
      await handleLoadSpotifyPlaylists(); // refresh "already in Library" flags
    } catch (err) {
      setSpotifyError(err.response?.data?.detail || "Couldn't add that playlist to the sorter.");
    } finally {
      setAdoptingId(null);
    }
  }

  function toggleSpotifySelected(spotifyPlaylistId) {
    setSelectedSpotifyIds((prev) => {
      const next = new Set(prev);
      if (next.has(spotifyPlaylistId)) next.delete(spotifyPlaylistId);
      else next.add(spotifyPlaylistId);
      return next;
    });
  }

  function toggleSelectAllSpotify() {
    setSelectedSpotifyIds(
      allSpotifySelected ? new Set() : new Set(untrackedSpotifyPlaylists.map((sp) => sp.spotify_playlist_id))
    );
  }

  async function handleBulkAdopt() {
    const ids = Array.from(selectedSpotifyIds);
    if (ids.length === 0) return;

    setBulkAdding(true);
    setSpotifyError(null);
    setAdoptNote(null);

    let succeeded = 0;
    const failedNames = [];
    const syncNotes = [];

    for (let i = 0; i < ids.length; i++) {
      setBulkProgress({ current: i + 1, total: ids.length });
      const sp = spotifyPlaylists?.find((p) => p.spotify_playlist_id === ids[i]);
      try {
        const adopted = await adoptPlaylist(ids[i]);
        succeeded += 1;
        if (adopted.sync_note) syncNotes.push(`"${sp?.name || adopted.name}" - ${adopted.sync_note}`);
      } catch (err) {
        failedNames.push(sp?.name || ids[i]);
      }
    }

    let summary = `Added ${succeeded} playlist${succeeded === 1 ? "" : "s"} to your Library.`;
    if (failedNames.length > 0) {
      summary += ` Couldn't add: ${failedNames.join(", ")}.`;
    }
    if (syncNotes.length > 0) {
      summary += ` ${syncNotes.join(" ")}`;
    }

    setBulkAdding(false);
    setBulkProgress(null);
    setAdoptNote(summary);
    await load();
    await handleLoadSpotifyPlaylists();
  }

  return (
    <div className="dashboard">
      <div className="dashboard-header">
        <div>
          <h1>Library</h1>
          <p className="page-sub">
            {playlists.length} playlist{playlists.length === 1 ? "" : "s"} on the desk
            {queueCounts.remaining > 0 && (
              <>
                {" "}
                ·{" "}
                <Link className="inline-link" to="/review">
                  {queueCounts.remaining} track{queueCounts.remaining === 1 ? "" : "s"} waiting in the queue
                </Link>
              </>
            )}
          </p>
        </div>
        <button className="btn-primary" onClick={() => setShowCreate((s) => !s)}>
          + New playlist
        </button>
      </div>

      {showCreate && (
        <form className="create-playlist-form" onSubmit={handleCreate}>
          <input
            autoFocus
            placeholder="e.g. Downtempo Chillout"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <button className="btn-primary" type="submit" disabled={creating}>
            {creating ? "Creating…" : "Create on Spotify"}
          </button>
        </form>
      )}

      {error && <div className="inline-error">{error}</div>}

      <div className="spotify-playlists-panel">
        <div className="spotify-playlists-head">
          <div>
            <h3>Your Spotify playlists</h3>
            <p className="page-sub">Browse everything on your account and add existing playlists to the sorter.</p>
          </div>
          <button className="btn-secondary" onClick={handleLoadSpotifyPlaylists} disabled={loadingSpotify}>
            {loadingSpotify ? "Loading…" : spotifyPlaylists ? "↻ Refresh" : "Load My Playlists"}
          </button>
        </div>

        {spotifyError && <div className="inline-error">{spotifyError}</div>}
        {adoptNote && <div className="empty-note">{adoptNote}</div>}

        {spotifyPlaylists && spotifyPlaylists.length === 0 && (
          <div className="empty-note">No playlists found on your Spotify account.</div>
        )}

        {spotifyPlaylists && spotifyPlaylists.length > 0 && (
          <>
            {followedOnlyPlaylists.length > 0 && (
              <label className="followed-toggle">
                <input
                  type="checkbox"
                  checked={showFollowed}
                  onChange={() => setShowFollowed((v) => !v)}
                />
                Show {followedOnlyPlaylists.length} followed-only playlist
                {followedOnlyPlaylists.length === 1 ? "" : "s"} too - these can't be read from or
                written to since you don't own or collaborate on them
              </label>
            )}

            {visibleSpotifyPlaylists.length === 0 ? (
              <div className="empty-note">
                All of your Spotify playlists are followed-only - check the box above to see them.
              </div>
            ) : (
              <>
                {untrackedSpotifyPlaylists.length > 0 && (
                  <div className="bulk-select-bar">
                    <label className="bulk-select-all">
                      <input
                        type="checkbox"
                        checked={allSpotifySelected}
                        onChange={toggleSelectAllSpotify}
                        disabled={bulkAdding}
                      />
                      Select all
                    </label>
                    {selectedSpotifyIds.size > 0 && (
                      <button className="btn-primary" onClick={handleBulkAdopt} disabled={bulkAdding}>
                        {bulkAdding
                          ? `Adding ${bulkProgress?.current ?? 0} of ${bulkProgress?.total ?? selectedSpotifyIds.size}…`
                          : `+ Add ${selectedSpotifyIds.size} selected to sorter`}
                      </button>
                    )}
                  </div>
                )}

                <div className="spotify-playlist-list">
                  {visibleSpotifyPlaylists.map((sp) => (
                    <div className="song-row" key={sp.spotify_playlist_id}>
                      {!sp.already_tracked && (
                        <input
                          type="checkbox"
                          checked={selectedSpotifyIds.has(sp.spotify_playlist_id)}
                          onChange={() => toggleSpotifySelected(sp.spotify_playlist_id)}
                          disabled={bulkAdding || adoptingId === sp.spotify_playlist_id}
                        />
                      )}
                      {sp.image_url ? (
                        <img className="song-thumb" src={sp.image_url} alt="" />
                      ) : (
                        <div className="song-thumb-placeholder">♫</div>
                      )}
                      <div className="song-row-text">
                        <div className="song-row-name">{sp.name}</div>
                        <div className="song-row-artist">
                          {sp.track_count != null ? `${sp.track_count} tracks` : "—"}
                          {!sp.is_owner ? " · followed" : ""}
                          {sp.collaborative ? " · collaborative" : ""}
                        </div>
                      </div>
                      {sp.already_tracked ? (
                        <span className="tag tag-mood">✓ In Library</span>
                      ) : (
                        <button
                          className="btn-secondary"
                          onClick={() => handleAdopt(sp.spotify_playlist_id)}
                          disabled={adoptingId === sp.spotify_playlist_id || bulkAdding}
                        >
                          {adoptingId === sp.spotify_playlist_id ? "Adding & analyzing…" : "+ Add to sorter"}
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </div>

      {loading ? (
        <div className="empty-state">Pulling up the console…</div>
      ) : playlists.length === 0 ? (
        <div className="empty-state">
          <p>No playlists yet.</p>
          <p className="page-sub">Create one above, or load your Spotify playlists to add existing ones.</p>
        </div>
      ) : (
        <div className="playlist-grid">
          {playlists.map((p) => (
            <PlaylistCard key={p.id} playlist={p} allPlaylists={playlists} onChanged={load} />
          ))}
        </div>
      )}
    </div>
  );
}
