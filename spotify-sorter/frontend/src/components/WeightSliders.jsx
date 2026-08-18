import { useState, useEffect } from "react";
import { updatePlaylistWeights } from "../api/client.js";

const FIELDS = [
  { key: "theme_vibe", label: "Theme & Vibe" },
  { key: "genre_style", label: "Genre & Style" },
  { key: "emotional_tone", label: "Mood / Emotional Tone" },
  { key: "tempo_energy", label: "Energy & Tempo" },
];

// Sliders work in convenient 0-100 units; the backend normalizes whatever
// it receives to sum to 1.0 regardless of scale, so this is just a display
// convenience, not a hard requirement.
function toSliderUnits(weights) {
  return Object.fromEntries(FIELDS.map(({ key }) => [key, Math.round((weights?.[key] ?? 0.25) * 100)]));
}

export default function WeightSliders({ playlistId, weights, onSaved }) {
  const [draft, setDraft] = useState(() => toSliderUnits(weights));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    setDraft(toSliderUnits(weights));
  }, [playlistId, weights]);

  const savedUnits = toSliderUnits(weights);
  const changed = FIELDS.some(({ key }) => draft[key] !== savedUnits[key]);
  const total = FIELDS.reduce((sum, { key }) => sum + draft[key], 0) || 1;

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const updated = await updatePlaylistWeights(playlistId, draft);
      onSaved?.(updated);
    } catch (err) {
      setError(err.response?.data?.detail || "Couldn't save weights.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="weight-sliders">
      {FIELDS.map(({ key, label }) => (
        <div className="weight-slider-row" key={key}>
          <span className="weight-slider-label">{label}</span>
          <input
            type="range"
            min="0"
            max="100"
            value={draft[key]}
            onChange={(e) => setDraft((prev) => ({ ...prev, [key]: Number(e.target.value) }))}
            className="weight-slider-input"
          />
          <span className="weight-slider-pct">{Math.round((draft[key] / total) * 100)}%</span>
        </div>
      ))}
      {changed && (
        <div className="weight-slider-actions">
          <button className="btn-secondary" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Apply weights"}
          </button>
          <button className="btn-text" onClick={() => setDraft(savedUnits)} disabled={saving}>
            Reset
          </button>
        </div>
      )}
      {error && <div className="inline-error">{error}</div>}
    </div>
  );
}
