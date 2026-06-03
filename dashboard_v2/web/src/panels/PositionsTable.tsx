import { useStore } from "../store";

const fmt = (n: number, d = 2) => n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

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
                <th>VENUE</th><th>TICKER</th><th>SIDE</th>
                <th className="right">QTY</th><th className="right">AVG</th>
                <th className="right">REALIZED</th><th>OPENED</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.id}>
                  <td><span className={`badge ${p.venue}`}>{p.venue}</span></td>
                  <td>{p.ticker.length > 22 ? p.ticker.slice(0, 22) + "…" : p.ticker}</td>
                  <td>{p.side}</td>
                  <td className="right">{p.contracts}</td>
                  <td className="right">{fmt(p.avg_price)}</td>
                  <td className={`right ${p.realized_pnl >= 0 ? "up" : "down"}`}>
                    {p.realized_pnl >= 0 ? "+" : ""}{fmt(p.realized_pnl)}
                  </td>
                  <td className="dim">{p.opened_at.slice(5, 16)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
