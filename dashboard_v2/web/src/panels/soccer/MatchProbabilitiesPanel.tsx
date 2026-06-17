import { SoccerMatchSummary } from "../../api/client";

type Props = { match: SoccerMatchSummary | null; loading: boolean };

function fmt(p: number | undefined): string {
  if (p == null) return "—";
  return `${(p * 100).toFixed(1)}%`;
}

function ProbBar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <span className="mono" style={{ fontSize: 11 }}>{label}</span>
        <span className="mono" style={{ fontSize: 11, color }}>{(value * 100).toFixed(1)}%</span>
      </div>
      <div style={{ background: "var(--bg-3)", height: 6, borderRadius: 2, overflow: "hidden" }}>
        <div style={{ width: `${Math.max(0, Math.min(100, value * 100))}%`, height: "100%", background: color }} />
      </div>
    </div>
  );
}

export default function MatchProbabilitiesPanel({ match, loading }: Props) {
  if (loading && !match) {
    return (
      <div className="panel">
        <div className="panel-header"><span><span className="ind" /> &nbsp; MATCH</span></div>
        <div className="panel-body"><div className="empty">simulating…</div></div>
      </div>
    );
  }
  if (!match) {
    return (
      <div className="panel">
        <div className="panel-header"><span><span className="ind" /> &nbsp; MATCH</span></div>
        <div className="panel-body"><div className="empty">select a fixture</div></div>
      </div>
    );
  }
  const f = match.fixture;
  const home = f.home_team_name || f.home_team_id;
  const away = f.away_team_name || f.away_team_id;
  const sm = match.score_matrix;
  let max = 0.0;
  for (const row of sm) for (const v of row) if (v > max) max = v;

  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; {home} vs {away}</span>
        <span className="mono dim">{f.competition || ""}</span>
      </div>
      <div className="panel-body" style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: 16 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <ProbBar label={`${home} win`} value={f.home_win || 0} color="var(--green)" />
          <ProbBar label="Draw" value={f.draw || 0} color="var(--amber)" />
          <ProbBar label={`${away} win`} value={f.away_win || 0} color="var(--red)" />
          <div style={{ height: 8 }} />
          <ProbBar label="Over 2.5" value={f.over_2_5 || 0} color="var(--blue)" />
          <ProbBar label="BTTS yes" value={f.btts_yes || 0} color="var(--blue)" />
          <div style={{ height: 8 }} />
          <div className="mono dim" style={{ fontSize: 11 }}>TOP SCORER PROB (anytime)</div>
          {match.top_scorer_probs.slice(0, 8).map((s) => (
            <ProbBar key={s.player_id} label={s.name} value={s.anytime} color="var(--blue)" />
          ))}
        </div>
        <div>
          <div className="mono dim" style={{ fontSize: 11, marginBottom: 4 }}>
            JOINT SCORE HEATMAP &nbsp;
            <span className="dim">(rows={home}, cols={away})</span>
          </div>
          <table className="tight" style={{ fontSize: 10 }}>
            <thead>
              <tr><th></th>{sm[0].map((_, j) => <th key={j} className="right mono">{j}</th>)}</tr>
            </thead>
            <tbody>
              {sm.slice(0, 7).map((row, i) => (
                <tr key={i}>
                  <th className="right mono">{i}</th>
                  {row.slice(0, 7).map((v, j) => {
                    const intensity = max > 0 ? Math.min(1, v / max) : 0;
                    const bg = `rgba(80,180,255,${intensity * 0.85 + (intensity > 0 ? 0.05 : 0)})`;
                    return (
                      <td key={j} className="right mono"
                          style={{ background: bg, color: intensity > 0.4 ? "#000" : "var(--fg)" }}>
                        {v < 0.001 ? "" : `${(v * 100).toFixed(1)}`}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ marginTop: 12 }}>
            <div className="mono dim" style={{ fontSize: 11 }}>CARDS (over/under)</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 6, marginTop: 4 }}>
              {Object.entries(match.cards_distribution).map(([k, v]) => {
                const line = k.replace("over_", "");
                return (
                  <div key={k} className="mono" style={{ fontSize: 11, textAlign: "center" }}>
                    <div className="dim">o {line}</div>
                    <div>{fmt(v)}</div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
