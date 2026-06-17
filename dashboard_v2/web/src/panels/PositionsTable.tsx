import { useStore } from "../store";

const fmt = (n: number, d = 2) => Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

export default function PositionsTable() {
  const positions = useStore((s) => s.positions);
  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; OPEN POSITIONS</span>
        <span className="mono dim">{positions.length}</span>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        {positions.length === 0 ? (
          <div className="empty">no open positions</div>
        ) : (
          <table className="tight">
            <thead>
              <tr>
                <th>VENUE</th><th>STATUS</th><th>MARKET</th><th>SIDE</th>
                <th className="right">QTY</th><th className="right">AVG</th><th className="right">MARK</th><th className="right">COST</th>
                <th className="right">VALUE</th><th className="right">U-PNL</th>
                <th className="right">PAYOUT</th><th className="right">MAX PROFIT</th>
                <th className="right">REALIZED</th><th>OPENED</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => {
                const title = p.market_title || p.ticker;
                const shortTitle = title.length > 48 ? title.slice(0, 48) + "…" : title;
                return (
                  <tr key={p.id}>
                    <td><span className={`badge ${p.venue}`}>{p.venue}</span></td>
                    <td>
                      <span className={`badge ${statusClass(p.market_status)}`} title={`position: ${p.position_status}`}>
                        {p.market_status || "unknown"}
                      </span>
                    </td>
                    <td title={`${title}\n${p.condition_id || ""}\n${p.ticker}`}>
                      {p.market_url ? (
                        <a href={p.market_url} target="_blank" rel="noreferrer" className="market-link">
                          {shortTitle}
                        </a>
                      ) : shortTitle}
                      {p.outcome_index != null ? <span className="dim" style={{ marginLeft: 6 }}>#{p.outcome_index}</span> : null}
                    </td>
                    <td>{p.side}</td>
                    <td className="right">{p.contracts}</td>
                    <td className="right">{fmt(p.avg_price)}</td>
                    <td className={`right ${(p.current_price ?? p.avg_price) >= p.avg_price ? "up" : "down"}`}>
                      {p.current_price == null ? "—" : fmt(p.current_price)}
                    </td>
                    <td className="right">${fmt(p.cost ?? Number(p.contracts) * Number(p.avg_price))}</td>
                    <td className="right">{p.current_value == null ? "—" : `$${fmt(p.current_value)}`}</td>
                    <td className={`right ${(p.unrealized_pnl ?? 0) >= 0 ? "up" : "down"}`}>
                      {p.unrealized_pnl == null ? "—" : `${p.unrealized_pnl >= 0 ? "+" : ""}$${fmt(p.unrealized_pnl)}`}
                    </td>
                    <td className="right">${fmt(p.potential_payout ?? Number(p.contracts))}</td>
                    <td className="right up">+${fmt(p.max_profit ?? Number(p.contracts) - Number(p.contracts) * Number(p.avg_price))}</td>
                    <td className={`right ${p.realized_pnl >= 0 ? "up" : "down"}`}>
                      {p.realized_pnl >= 0 ? "+" : ""}{fmt(p.realized_pnl)}
                    </td>
                    <td className="dim">{p.opened_at.slice(5, 16)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function statusClass(status: string) {
  if (status === "active" || status === "won") return "filled";
  if (status === "lost") return "rejected";
  return "observe";
}
