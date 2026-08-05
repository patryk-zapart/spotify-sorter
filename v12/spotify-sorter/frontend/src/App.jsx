import { useEffect, useState } from "react";
import { Routes, Route, Link, useLocation, Navigate } from "react-router-dom";
import LibraryPage from "./pages/LibraryPage.jsx";
import ImportPage from "./pages/ImportPage.jsx";
import ReviewQueue from "./pages/ReviewQueue.jsx";
import CheckPlaylists from "./pages/CheckPlaylists.jsx";
import { authStatus, loginUrl, logout } from "./api/client.js";

export default function App() {
  const [auth, setAuth] = useState({ checked: false, authenticated: false });
  const location = useLocation();

  useEffect(() => {
    authStatus()
      .then((data) => setAuth({ checked: true, ...data }))
      .catch(() => setAuth({ checked: true, authenticated: false }));
  }, [location.pathname]);

  if (!auth.checked) {
    return <div className="app-loading">Warming up the console…</div>;
  }

  if (!auth.authenticated) {
    return <LandingScreen />;
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">◈</span>
          <span className="brand-name">Sorted</span>
        </div>
        <nav className="topnav">
          <Link className={navClass(location, "/library")} to="/library">
            Library
          </Link>
          <Link className={navClass(location, "/import")} to="/import">
            Import New Songs
          </Link>
          <Link className={navClass(location, "/review")} to="/review">
            Review Queue
          </Link>
          <Link className={navClass(location, "/check")} to="/check">
            Check My Playlists
          </Link>
        </nav>
        <button
          className="btn-ghost"
          onClick={async () => {
            await logout();
            window.location.href = "/";
          }}
        >
          Disconnect
        </button>
      </header>

      <main className="app-main">
        <Routes>
          <Route path="/" element={<Navigate to="/library" replace />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="/review" element={<ReviewQueue />} />
          <Route path="/check" element={<CheckPlaylists />} />
        </Routes>
      </main>
    </div>
  );
}

function navClass(location, path) {
  return location.pathname === path ? "topnav-link active" : "topnav-link";
}

function LandingScreen() {
  return (
    <div className="landing">
      <div className="landing-panel">
        <div className="landing-mark">◈</div>
        <h1>Sorted</h1>
        <p className="landing-sub">
          Pull a Spotify playlist in, run every track past your library's sonic
          profile, and drop each one into the playlist it actually belongs on.
        </p>
        <a className="btn-primary" href={loginUrl()}>
          Connect Spotify
        </a>
        <p className="landing-fine">
          Read + write access to your playlists only. Nothing is posted or shared.
        </p>
      </div>
    </div>
  );
}
