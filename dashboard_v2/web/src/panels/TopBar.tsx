import { useStore } from "../store";

const fmt = (n: number | null | undefined, d = 2) =>
  n == null ? "—" : n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

export default function TopBar() {
  const { venue, setVenue, wsAlive, portfolio } = useStore();
  const pnl = portfolio ? portfolio.bankroll - portfolio.starting_bankroll : 0;
  const pnlPct = portfolio && portfolio.starting_bankroll ? (pnl / portfolio.starting_bankroll) * 100 : 0;
  return (
    <div className="topbar">
      <div className="brand">EDGE</div>
      <div className="stat">
        <span>Bankroll</span>
        <span className="value mono">${fmt(portfolio?.bankroll)}</span>
      </div>
      <div className="stat">
        <span>PnL</span>
        <span className={`value mono ${pnl >= 0 ? "up" : "down"}`}>
          {pnl >= 0 ? "+" : ""}${fmt(pnl)} ({pnl >= 0 ? "+" : ""}{fmt(pnlPct)}%)
        </span>
      </div>
      <div className="stat">
        <span>Cash</span>
        <span className="value mono">${fmt(portfolio?.cash)}</span>
      </div>
      <div className="stat">
        <span>Open</span>
        <span className="value mono">
          {portfolio?.n_open_positions ?? 0}
          <span className="dim mono" style={{ fontSize: 11, marginLeft: 8 }}>
            K:{portfolio?.n_open_kalshi ?? 0} · P:{portfolio?.n_open_polymarket ?? 0}
          </span>
        </span>
      </div>
      <div className="spacer" />
      <div className="stat" style={{ alignItems: "flex-end" }}>
        <span>Venue</span>
        <select
          value={venue}
          onChange={(e) => setVenue(e.target.value as any)}
          style={{
            background: "var(--bg-2)", color: "var(--fg-0)", border: "1px solid var(--border-hot)",
            padding: "4px 8px", fontFamily: "var(--mono)", fontSize: 12, marginTop: 2,
          }}
        >
          <option value="all">All</option>
          <option value="kalshi">Kalshi</option>
          <option value="polymarket">Polymarket</option>
        </select>
      </div>
      <div className="stat" style={{ alignItems: "flex-end" }}>
        <span>Stream</span>
        <span style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4 }}>
          <span className={`pulse ${wsAlive ? "" : "dead"}`} />
          <span className="mono" style={{ fontSize: 11, color: wsAlive ? "var(--green)" : "var(--red)" }}>
            {wsAlive ? "LIVE" : "OFFLINE"}
          </span>
        </span>
      </div>
    </div>
  );
}
