import { useStore } from "../store";

const pct = (n: number) => `${(Number(n) * 100).toFixed(1)}c`;
const price = (n: number) => Number(n) ? Number(n).toFixed(2) : "--";
const ago = (unix: number) => {
  const s = Math.max(0, Math.floor(Date.now() / 1000) - unix);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
};

export default function CrossVenueSpread() {
  const snapshot = useStore((s) => s.crossVenue);
  const run = snapshot.latest_run;
  const spreads = snapshot.spreads;
  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" style={{ background: "var(--cyan)", boxShadow: "0 0 6px var(--cyan)" }} /> &nbsp; CROSS-VENUE SPREAD</span>
        <span className="mono dim">{run ? `${run.candidates} CAND` : "NO RUN"}</span>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        {!run ? (
          <div className="empty">no cross-venue scan yet — run <span className="mono">cli.py cross-venue-scan</span></div>
        ) : spreads.length === 0 ? (
          <div className="empty">
            latest scan {ago(run.scanned_at_unix)} ago: {run.matched_markets} matches, {run.books_checked} books, no priceable spreads
          </div>
        ) : (
          <>
            <div className="scan-strip mono">
              <span>{ago(run.scanned_at_unix)} ago</span>
              <span>K {run.kalshi_eligible_markets}/{run.kalshi_markets}</span>
              <span>mve x{run.kalshi_excluded_mve}</span>
              <span>P {run.polymarket_markets}</span>
              <span>matched {run.matched_markets}</span>
              <span>books {run.books_checked}</span>
            </div>
            <table className="tight">
              <thead>
                <tr>
                  <th>MARKET</th>
                  <th className="right">MATCH</th>
                  <th className="right">K YES</th>
                  <th className="right">P YES</th>
                  <th className="right">VAL</th>
                  <th className="right">EXEC</th>
                  <th>DIR</th>
                </tr>
              </thead>
              <tbody>
                {spreads.map((s) => (
                  <tr key={s.id}>
                    <td title={`${s.kalshi_title}\n${s.polymarket_question}`}>
                      <span className="mono dim">{s.kalshi_ticker}</span>{" "}
                      {s.kalshi_title.slice(0, 34)}
                    </td>
                    <td className="right">{Number(s.match_score).toFixed(2)}</td>
                    <td className="right mono">{price(s.kalshi_yes_bid)}/{price(s.kalshi_yes_ask)}</td>
                    <td className="right mono">{price(s.polymarket_yes_bid)}/{price(s.polymarket_yes_ask)}</td>
                    <td className={`right mono ${s.valuation_spread >= 0 ? "up" : "down"}`}>{pct(s.valuation_spread)}</td>
                    <td className={`right mono ${s.best_executable_spread >= run.min_spread ? "up" : "dim"}`}>{pct(s.best_executable_spread)}</td>
                    <td><span className={`badge ${s.decision}`}>{s.direction.replace("buy_", "").replace("_sell_", "→")}</span></td>
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
