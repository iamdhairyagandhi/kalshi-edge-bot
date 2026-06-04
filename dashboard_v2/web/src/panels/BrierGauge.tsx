import { useStore } from "../store";

const SCALE_MAX = 0.4; // 0 = perfect, 0.25 = coin flip, >0.25 = worse than random

export default function BrierGauge() {
  const brier = useStore((s) => s.brier);
  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; BRIER / CALIBRATION</span>
        <span className="mono dim">{brier.length} strat</span>
      </div>
      <div className="panel-body">
        {brier.length === 0 ? (
          <div className="empty">no resolved predictions yet</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {brier.map((b) => {
              const score = Math.min(SCALE_MAX, b.brier_score);
              const pct = (score / SCALE_MAX) * 100;
              const good = b.brier_score <= 0.18;
              const warn = b.brier_score > 0.18 && b.brier_score <= 0.25;
              const color = good ? "var(--green)" : warn ? "var(--amber)" : "var(--red)";
              return (
                <div key={b.strategy}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span className="mono" style={{ fontSize: 12 }}>{b.strategy}</span>
                    <span className="mono" style={{ fontSize: 12, color }}>
                      {b.brier_score.toFixed(4)} <span className="dim" style={{ fontSize: 10 }}>n={b.n_resolved}</span>
                    </span>
                  </div>
                  <div style={{ position: "relative", background: "var(--bg-3)", height: 8, borderRadius: 2, overflow: "hidden" }}>
                    <div style={{
                      position: "absolute", left: `${(0.25 / SCALE_MAX) * 100}%`,
                      top: 0, bottom: 0, width: 1, background: "var(--fg-2)", zIndex: 2,
                    }} title="coin flip = 0.25" />
                    <div style={{
                      width: `${pct}%`, height: "100%",
                      background: `linear-gradient(90deg, ${color}, ${color}cc)`,
                      transition: "width 600ms ease-out",
                    }} />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
