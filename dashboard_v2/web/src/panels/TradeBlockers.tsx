import { useStore } from "../store";

const fmt = (n: number | null, d = 3) => n == null ? "--" : Number(n).toFixed(d);
const ago = (unix: number) => {
  const s = Math.max(0, Math.floor(Date.now() / 1000) - unix);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
};

export default function TradeBlockers() {
  const diagnostics = useStore((s) => s.diagnostics);
  const rows = diagnostics.rows;
  const top = diagnostics.summary.slice(0, 4);
  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" style={{ background: "var(--amber)", boxShadow: "0 0 6px var(--amber)" }} /> &nbsp; TRADE BLOCKERS</span>
        <span className="mono dim">{rows.length}</span>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        {rows.length === 0 ? (
          <div className="empty">no blocker diagnostics yet</div>
        ) : (
          <>
            <div className="scan-strip mono">
              {top.map((s) => (
                <span key={`${s.strategy}-${s.venue}-${s.reason}`}>
                  {s.venue}:{s.reason} {s.count}
                </span>
              ))}
            </div>
            <table className="tight">
              <thead>
                <tr>
                  <th>AGE</th>
                  <th>VENUE</th>
                  <th>REASON</th>
                  <th>MARKET</th>
                  <th className="right">METRIC</th>
                  <th className="right">THRESH</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 12).map((r) => (
                  <tr key={r.id}>
                    <td className="dim">{ago(r.recorded_unix)}</td>
                    <td><span className={`badge ${r.venue}`}>{r.venue}</span></td>
                    <td title={r.details || r.reason}>{r.reason}</td>
                    <td title={r.market_title || r.market_id}>
                      {(r.market_title || r.market_id).slice(0, 38)}
                    </td>
                    <td className="right mono">{fmt(r.metric_value)}</td>
                    <td className="right mono dim">{fmt(r.threshold_value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}
