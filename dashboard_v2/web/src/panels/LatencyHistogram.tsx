import { useStore } from "../store";

const labelFor = (sec: number) => {
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${sec / 60}m`;
  if (sec < 86400) return `${sec / 3600}h`;
  return `${sec / 86400}d`;
};

export default function LatencyHistogram() {
  const latency = useStore((s) => s.latency);
  const max = Math.max(1, ...latency.map((b) => b.count));
  const total = latency.reduce((a, b) => a + b.count, 0);
  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; SIGNAL → DECISION LATENCY</span>
        <span className="mono dim">{total}</span>
      </div>
      <div className="panel-body">
        {total === 0 ? (
          <div className="empty">no filled signals yet</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {latency.map((b) => (
              <div key={b.upper_seconds} style={{ display: "grid", gridTemplateColumns: "56px 1fr 40px", alignItems: "center", gap: 8 }}>
                <span className="mono dim" style={{ fontSize: 11, textAlign: "right" }}>≤ {labelFor(b.upper_seconds)}</span>
                <div style={{ background: "var(--bg-3)", height: 14, borderRadius: 2, overflow: "hidden" }}>
                  <div
                    style={{
                      width: `${(b.count / max) * 100}%`, height: "100%",
                      background: "linear-gradient(90deg, var(--cyan-dim), var(--cyan))",
                      transition: "width 400ms cubic-bezier(.2,.7,.2,1)",
                    }}
                  />
                </div>
                <span className="mono" style={{ fontSize: 11, textAlign: "right" }}>{b.count}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
