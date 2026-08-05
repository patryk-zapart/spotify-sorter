import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { importPlaylist } from "../api/client.js";

export default function ImportPage() {
  const [url, setUrl] = useState("");
  const [status, setStatus] = useState("idle"); // idle | loading | done | error
  const [message, setMessage] = useState("");
  const navigate = useNavigate();

  async function handleSubmit(e) {
    e.preventDefault();
    if (!url.trim()) return;
    setStatus("loading");
    setMessage("");
    try {
      const data = await importPlaylist(url.trim());
      setStatus("done");
      setMessage(`Imported ${data.imported} track${data.imported === 1 ? "" : "s"} into the review queue.`);
    } catch (err) {
      setStatus("error");
      setMessage(err.response?.data?.detail || "Couldn't import that playlist.");
    }
  }

  return (
    <div className="import-page">
      <div className="import-panel">
        <h1>Import New Songs</h1>
        <p className="page-sub">
          Paste a Spotify playlist link, URI, or bare ID. Every track gets pulled in, analyzed, and
          queued for one-by-one review.
        </p>
        <p className="import-hint">
          Must be a playlist you created yourself (or collaborate on) — as of Spotify's Feb 2026
          API changes, playlists owned by Spotify itself (Discover Weekly, Daily Mix, genre radio,
          IDs starting with <code>37i9dQZ...</code>) no longer expose their tracks via the API at
          all.
        </p>

        <form onSubmit={handleSubmit} className="import-form">
          <input
            placeholder="https://open.spotify.com/playlist/..."
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={status === "loading"}
          />
          <button className="btn-primary" type="submit" disabled={status === "loading"}>
            {status === "loading" ? "Analyzing tracks…" : "Import"}
          </button>
        </form>

        {status === "loading" && (
          <p className="page-sub">
            Fetching tracks, looking up artist genres, and estimating tempo/mood for each song — this
            can take a little while for a long playlist.
          </p>
        )}
        {status === "done" && (
          <div className="import-success">
            <p>{message}</p>
            <button className="btn-primary" onClick={() => navigate("/review")}>
              Start reviewing →
            </button>
          </div>
        )}
        {status === "error" && <div className="inline-error">{message}</div>}
      </div>
    </div>
  );
}
