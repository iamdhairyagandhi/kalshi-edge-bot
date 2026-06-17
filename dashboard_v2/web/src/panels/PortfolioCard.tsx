import { useStore } from "../store";

const fmt = (n: number | null | undefined, d = 2) =>
  n == null ? "—" : Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

export default function PortfolioCard() {
  const p = useStore((s) => s.portfolio);
  const realized = p?.realized_pnl ?? 0;
  const unrealized = p?.unrealized_pnl ?? 0;
  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; PORTFOLIO</span>
      </div>
      <div className="panel-body">
        <div className="kpi-row">
          <div className="kpi">
            <span className="label">Starting</span>
            <span className="value">${fmt(p?.starting_bankroll)}</span>
          </div>
          <div className="kpi">
            <span className="label">Cash</span>
            <span className="value">${fmt(p?.cash)}</span>
          </div>
          <div className="kpi">
            <span className="label">Open cost</span>
            <span className="value">${fmt(p?.open_position_cost)}</span>
          </div>
          <div className="kpi">
            <span className="label">Bankroll</span>
            <span className="value">${fmt(p?.bankroll)}</span>
          </div>
          <div className="kpi">
            <span className="label">Fees paid</span>
            <span className="value down">-${fmt(p?.fees_paid)}</span>
          </div>
          <div className="kpi">
            <span className="label">Realized PnL</span>
            <span className={`value ${realized >= 0 ? "up" : "down"}`}>
              {realized >= 0 ? "+" : ""}${fmt(realized)}
            </span>
          </div>
          <div className="kpi">
            <span className="label">Unrealized PnL</span>
            <span className={`value ${unrealized >= 0 ? "up" : "down"}`}>
              {unrealized >= 0 ? "+" : ""}${fmt(unrealized)}
            </span>
          </div>
          <div className="kpi">
            <span className="label">Open</span>
            <span className="value">{p?.n_open_positions ?? 0}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
