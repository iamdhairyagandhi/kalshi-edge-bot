import { useStore } from "../store";

const price = (n: number | null) => n == null ? "--" : Number(n).toFixed(2);
const pct = (n: number | null) => n == null ? "--" : `${(Number(n) * 100).toFixed(1)}%`;
const edge = (n: number | null) => n == null ? "--" : `${(Number(n) * 100).toFixed(1)}c`;
const value = (n: number | null) => n == null ? "--" : Number(n).toFixed(1);
const titleCase = (s: string | null) => (s || "--").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

const ago = (unix: number | null) => {
  if (!unix) return "NO RUN";
  const s = Math.max(0, Math.floor(Date.now() / 1000) - unix);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};

export default function WeatherSpecialist() {
  const weather = useStore((s) => s.weather);
  const rows = weather.rows;
  const candidates = rows.filter((r) => r.recommendation !== "observe").length;
  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" style={{ background: "var(--green)", boxShadow: "0 0 6px var(--green)" }} /> &nbsp; WEATHER SPECIALIST</span>
        <span className="mono dim">{candidates} CAND</span>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        {!weather.latest_run_id ? (
          <div className="empty">no weather scan yet — run <span className="mono">cli.py weather-scan</span></div>
        ) : rows.length === 0 ? (
          <div className="empty">latest scan {ago(weather.latest_recorded_unix)} found no parseable weather estimates</div>
        ) : (
          <>
            <div className="scan-strip mono">
              <span>{ago(weather.latest_recorded_unix)}</span>
              <span>run {weather.latest_run_id.slice(0, 8)}</span>
              {weather.summary.map((s) => (
                <span key={s.recommendation}>{s.recommendation} {s.count}</span>
              ))}
            </div>
            <table className="tight">
              <thead>
                <tr>
                  <th>MARKET</th>
                  <th>CITY</th>
                  <th className="right">FCST</th>
                  <th className="right">P YES</th>
                  <th className="right">BID/ASK</th>
                  <th className="right">EDGE</th>
                  <th className="right">CONF</th>
                  <th>REC</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 18).map((r) => {
                  const bestEdge = Math.max(r.edge_yes ?? -99, r.edge_no ?? -99);
                  return (
                    <tr key={r.id}>
                      <td title={`${r.title || r.market_id}\n${r.notes || ""}`}>
                        <span className="mono dim">{r.market_id}</span>{" "}
                        {(r.title || "").replace(/\*/g, "").slice(0, 42)}
                      </td>
                      <td>{titleCase(r.city)}</td>
                      <td className="right mono">{value(r.forecast_value)}</td>
                      <td className="right mono">{pct(r.p_yes)}</td>
                      <td className="right mono">{price(r.yes_bid)}/{price(r.yes_ask)}</td>
                      <td className={`right mono ${bestEdge > 0 ? "up" : "dim"}`}>{edge(bestEdge)}</td>
                      <td className="right mono">{pct(r.confidence)}</td>
                      <td>
                        <span className={`badge ${r.recommendation}`}>{r.recommendation.replace("buy_", "buy ")}</span>
                        {r.ai_used ? <span className="badge observe" title={r.notes || "AI used"}>AI</span> : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}
