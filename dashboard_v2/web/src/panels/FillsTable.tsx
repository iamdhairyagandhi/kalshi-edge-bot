import { useStore } from "../store";

const fmt = (n: number, d = 2) => n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

export default function FillsTable() {
  const fills = useStore((s) => s.fills);
  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; FILL TAPE</span>
        <span className="mono dim">{fills.length}</span>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        {fills.length === 0 ? (
          <div className="empty">no fills yet</div>
        ) : (
          <table className="tight">
            <thead>
              <tr>
                <th>TIME</th><th>VENUE</th><th>STRAT</th>
                <th>TICKER</th><th>SIDE</th><th>ACT</th>
                <th className="right">QTY</th><th className="right">PX</th><th className="right">FEE</th>
              </tr>
            </thead>
            <tbody>
              {fills.map((f) => (
                <tr key={f.id}>
                  <td className="dim">{f.placed_at.slice(11, 19)}</td>
                  <td><span className={`badge ${f.venue}`}>{f.venue.slice(0, 4)}</span></td>
                  <td className="dim">{f.strategy}</td>
                  <td title={f.ticker}>{f.ticker.length > 18 ? f.ticker.slice(0, 18) + "…" : f.ticker}</td>
                  <td>{f.side}</td>
                  <td className={f.action === "buy" ? "up" : "down"}>{f.action.toUpperCase()}</td>
                  <td className="right">{f.contracts}</td>
                  <td className="right">{fmt(f.price)}</td>
                  <td className="right dim">{fmt(f.fees)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
