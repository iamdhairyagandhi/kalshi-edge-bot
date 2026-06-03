import { useStore } from "../store";

const fmt = (n: number, d = 2) => n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const ago = (unix: number) => {
  const s = Math.max(0, Math.floor(Date.now() / 1000) - unix);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
};

export default function ConsensusFeed() {
  const signals = useStore((s) => s.signals);
  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" style={{ background: "var(--magenta)", boxShadow: "0 0 6px var(--magenta)" }} /> &nbsp; CONSENSUS SIGNALS</span>
        <span className="mono dim">{signals.length}</span>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        {signals.length === 0 ? (
          <div className="empty">no consensus signals yet — run <span className="mono">cli.py polymarket-scan</span></div>
        ) : (
          <table className="tight">
            <thead>
              <tr>
                <th>AGE</th><th>K/N</th><th>MARKET</th>
                <th className="right">AVG ENTRY</th>
                <th className="right">NOTIONAL</th>
                <th>DECISION</th>
              </tr>
            </thead>
            <tbody>
              {signals.map((s) => (
                <tr key={s.idempotency_key}>
                  <td className="dim">{ago(s.decision_unix)}</td>
                  <td className="mono">{s.agreeing_wallets.length}/{s.cohort_size}</td>
                  <td title={s.market_question || s.condition_id}>
                    {(s.market_question || s.condition_id).slice(0, 36)}
                    <span className="dim" style={{ marginLeft: 6 }}>#{s.outcome_index}</span>
                  </td>
                  <td className="right">{fmt(s.avg_wallet_entry_price, 3)}</td>
                  <td className="right">${fmt(s.total_wallet_notional_usd, 0)}</td>
                  <td><span className={`badge ${s.decision}`}>{s.decision.replace("rejected_", "x:")}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
