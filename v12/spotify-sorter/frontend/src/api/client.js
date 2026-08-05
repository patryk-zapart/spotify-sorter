import axios from "axios";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

export const api = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true, // send the session cookie holding Spotify tokens
});

export const authStatus = () => api.get("/api/auth/status").then((r) => r.data);
export const loginUrl = () => `${API_BASE_URL}/api/auth/login`;
export const logout = () => api.post("/api/auth/logout").then((r) => r.data);

export const importPlaylist = (playlistUrl) =>
  api.post("/api/import", { playlist_url: playlistUrl }).then((r) => r.data);

export const fetchPlaylists = () => api.get("/api/playlists").then((r) => r.data);

export const createPlaylist = (name, description = "", makePublic = false) =>
  api
    .post("/api/playlists", { name, description, make_public: makePublic })
    .then((r) => r.data);

export const adoptPlaylist = (spotifyPlaylistId) =>
  api.post("/api/playlists/adopt", { spotify_playlist_id: spotifyPlaylistId }).then((r) => r.data);

// Removes a playlist from the local Library only - never touches the real
// playlist on Spotify.
export const deletePlaylist = (playlistId) =>
  api.delete(`/api/playlists/${playlistId}`).then((r) => r.data);

export const suggestNames = (playlistId) =>
  api.post(`/api/playlists/${playlistId}/suggest-names`).then((r) => r.data);

// Powers "Check My Playlists" - re-scores every song in a playlist against
// its current profile (leave-one-out), so drift since assignment is visible.
export const checkPlaylist = (playlistId) =>
  api.get(`/api/playlists/${playlistId}/check`).then((r) => r.data);

// Edits a playlist's description on Spotify, and re-interprets it into
// cached sonic hints that feed fit-score matching going forward.
export const updatePlaylistDescription = (playlistId, description) =>
  api.put(`/api/playlists/${playlistId}/description`, { description }).then((r) => r.data);

// Edits a playlist's active scoring weights (the four sliders). Local-only
// - no Spotify or OpenAI call - takes effect on every future score for this
// playlist, everywhere it's scored.
export const updatePlaylistWeights = (playlistId, weights) =>
  api.put(`/api/playlists/${playlistId}/weights`, weights).then((r) => r.data);

// On-demand, plain-English explanation of why a song scored the way it did
// against a playlist - only fetched when explicitly requested (costs an
// OpenAI call), never automatically for a whole list.
export const explainSongFit = (playlistId, songId) =>
  api.get(`/api/playlists/${playlistId}/songs/${songId}/explain`).then((r) => r.data);

export const fetchMySpotifyPlaylists = () => api.get("/api/spotify/my-playlists").then((r) => r.data);

export const fetchNextInQueue = () => api.get("/api/queue/next").then((r) => r.data);

export const fetchQueueCount = () => api.get("/api/queue/count").then((r) => r.data);

export const requeueSkipped = () => api.post("/api/queue/requeue-skipped").then((r) => r.data);

export const skipSong = (songId) => api.post(`/api/songs/${songId}/skip`).then((r) => r.data);

// Review Queue's main action: assign one song to one or more playlists at once.
export const assignSongBatch = (songId, playlistIds) =>
  api.post(`/api/songs/${songId}/assign-batch`, { playlist_ids: playlistIds }).then((r) => r.data);

// Post-hoc membership editing from a playlist card (manual override).
export const addSongToPlaylist = (songId, playlistId) =>
  api.post(`/api/songs/${songId}/memberships`, { playlist_id: playlistId }).then((r) => r.data);

// Removes one playlist membership. Pass requeue=true to also send the song
// back to the front of the Review Queue for reconsideration, even if it's
// still a member of other playlists (otherwise it only auto-requeues when
// this was its last remaining playlist).
export const removeSongFromPlaylist = (songId, playlistId, requeue = false) =>
  api
    .delete(`/api/songs/${songId}/memberships/${playlistId}`, { params: { requeue } })
    .then((r) => r.data);

// Name suggestions for a brand-new playlist built around a single song
// (the "Create New Playlist" option in the Review Queue).
export const suggestNewPlaylistName = (songId) =>
  api.post(`/api/songs/${songId}/suggest-playlist-name`).then((r) => r.data);
