import { useEffect } from "react";
import { api } from "../api/client";
import { useStore } from "../store";

export default function KillSwitchPanel() {
  const { strategies, upsertStrategy, setSnapshot } = useStore();

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const s = await api.killswitch();
        if (!cancelled) setSnapshot({ strategies: s });
      } catch (e) { /* table missing on fresh installs */ }
    }
    refresh();
    const id = setInterval(refresh, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [setSnapshot]);

  async function toggle(s: { strategy: string; enabled: boolean }) {
    const reason = s.enabled ? "manual halt from dashboard" : undefined;
    const updated = await api.toggleKill(s.strategy, !s.enabled, reason);
    upsertStrategy(updated);
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" style={{ background: "var(--red)", boxShadow: "0 0 6px var(--red)" }} /> &nbsp; KILL SWITCH</span>
        <span className="mono dim">{strategies.length}</span>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        {strategies.length === 0 ? (
          <div className="empty">no strategies registered yet</div>
        ) : (
          <table className="tight">
            <thead>
              <tr>
                <th>STRATEGY</th>
                <th className="right">BRIER</th>
                <th className="right">N</th>
                <th>STATE</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((s) => (
                <tr key={s.strategy}>
                  <td className="mono" title={s.disabled_reason || ""}>{s.strategy}</td>
                  <td className="right mono">{s.last_brier != null ? s.last_brier.toFixed(3) : "—"}</td>
                  <td className="right dim mono">{s.last_n_samples ?? "—"}</td>
                  <td>
                    <span className={`badge ${s.enabled ? "filled" : "rejected"}`}>
                      {s.enabled ? "LIVE" : "HALTED"}
                    </span>
                  </td>
                  <td>
                    <button
                      onClick={() => toggle(s)}
                      style={{
                        background: s.enabled ? "rgba(255,77,79,0.18)" : "rgba(25,195,125,0.18)",
                        color: s.enabled ? "var(--red)" : "var(--green)",
                        border: `1px solid ${s.enabled ? "var(--red-dim)" : "var(--green-dim)"}`,
                        padding: "2px 8px", borderRadius: 2, fontFamily: "var(--mono)",
                        fontSize: 10, cursor: "pointer", letterSpacing: "0.1em",
                      }}
                    >
                      {s.enabled ? "HALT" : "RESUME"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
