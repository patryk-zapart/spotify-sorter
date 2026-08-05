const SEGMENTS = 12;

/**
 * Renders a fit score (0-100) as a segmented LED-style meter, echoing the
 * VU meters on a mixing console channel strip. Segments light amber, then
 * shift to the "hot" accent as the score climbs into a strong match.
 */
export default function FitScoreMeter({ score, label, highlight = false, compact = false }) {
  const lit = Math.round((score / 100) * SEGMENTS);

  return (
    <div className={`meter ${compact ? "meter-compact" : ""} ${highlight ? "meter-highlight" : ""}`}>
      {label && <div className="meter-label">{label}</div>}
      <div className="meter-track" role="meter" aria-valuenow={score} aria-valuemin={0} aria-valuemax={100}>
        {Array.from({ length: SEGMENTS }).map((_, i) => {
          const isLit = i < lit;
          const zone = i < SEGMENTS * 0.5 ? "low" : i < SEGMENTS * 0.8 ? "mid" : "high";
          return (
            <span
              key={i}
              className={`meter-seg ${isLit ? `meter-seg-lit meter-seg-${zone}` : ""}`}
            />
          );
        })}
      </div>
      <div className="meter-value">{score}</div>
    </div>
  );
}
