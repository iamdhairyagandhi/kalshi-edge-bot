import { useStore } from "../store";

export default function CohortTable() {
  const cohort = useStore((s) => s.cohort);
  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; SMART-MONEY COHORT</span>
        <span className="mono dim">{cohort.length}</span>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        {cohort.length === 0 ? (
          <div className="empty">no cohort yet</div>
        ) : (
          <table className="tight">
            <thead>
              <tr>
                <th>#</th><th>WALLET</th>
                <th className="right">SCORE</th>
                <th className="right">SIGNALS</th>
              </tr>
            </thead>
            <tbody>
              {cohort.map((w) => (
                <tr key={w.wallet}>
                  <td className="dim">{w.rank}</td>
                  <td className="mono" title={w.wallet}>
                    {w.wallet.slice(0, 6)}…{w.wallet.slice(-4)}
                  </td>
                  <td className="right neu">{w.score.toFixed(0)}</td>
                  <td className="right dim">{w.n_trades || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
