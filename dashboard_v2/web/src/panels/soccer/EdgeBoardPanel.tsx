import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import {
  api,
  SoccerBetLeg,
  SoccerEdgeBoard,
  SoccerMarketEdge,
  SoccerMatchSummary,
  SoccerParlayBlueprint,
} from "../../api/client";

type Props = {
  match: SoccerMatchSummary | null;
  onUseLegs: (legs: SoccerBetLeg[]) => void;
};

function pct(x: number | null | undefined, digits = 1): string {
  if (x == null || !Number.isFinite(x)) return "--";
  return `${(x * 100).toFixed(digits)}%`;
}

function odds(x: number | null | undefined): string {
  if (x == null || !Number.isFinite(x)) return "--";
  return x >= 100 ? "100+" : x.toFixed(2);
}

function edgeColor(edge: number | null): string {
  if (edge == null) return "var(--fg-2)";
  if (edge >= 0.08) return "var(--green)";
  if (edge > 0) return "var(--amber)";
  return "var(--red)";
}

function riskColor(level: string): string {
  if (level === "safer") return "var(--green)";
  if (level === "moderate") return "var(--amber)";
  return "var(--red)";
}

function edgeToLeg(e: SoccerMarketEdge, match: SoccerMatchSummary): SoccerBetLeg | null {
  const home = (match.fixture.home_team_name || match.fixture.home_team_id).toLowerCase();
  const away = (match.fixture.away_team_name || match.fixture.away_team_id).toLowerCase();
  const sel = e.selection.toLowerCase();
  if (e.market_key === "h2h") {
    const side = sel === "draw" ? "D" : sel.includes(home) ? "H" : sel.includes(away) ? "A" : null;
    return side ? { kind: "match_result", params: { side }, label: e.label } : null;
  }
  if (e.market_key === "totals") {
    return {
      kind: "total_goals",
      params: { line: 2.5, side: sel.startsWith("over") ? "over" : "under" },
      label: e.label,
    };
  }
  if (e.market_key === "btts") {
    return { kind: "btts", params: { side: sel.startsWith("yes") ? "yes" : "no" }, label: e.label };
  }
  return null;
}

export default function EdgeBoardPanel({ match, onUseLegs }: Props) {
  const [board, setBoard] = useState<SoccerEdgeBoard | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!match) {
      setBoard(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setErr(null);
    api.soccerEdgeBoard(match.fixture.fixture_id, 8000)
      .then((b) => { if (!cancelled) setBoard(b); })
      .catch((e) => { if (!cancelled) setErr(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [match?.fixture.fixture_id]);

  const sortedEdges = useMemo(() => {
    return [...(board?.market_edges || [])].sort((a, b) => {
      // Prefer edge_vs_market (the sharp-money edge) when present;
      // fall back to raw edge for markets without a de-vig vector.
      const av = a.edge_vs_market ?? a.edge ?? -99;
      const bv = b.edge_vs_market ?? b.edge ?? -99;
      return bv - av;
    });
  }, [board]);

  const bestEdge = sortedEdges.find((e) => (e.edge_vs_market ?? e.edge) != null);

  if (!match) {
    return (
      <div className="panel">
        <div className="panel-header"><span><span className="ind" /> &nbsp; MARKET EDGE</span></div>
        <div className="panel-body"><div className="empty">select a fixture</div></div>
      </div>
    );
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; MARKET EDGE WORKBENCH</span>
        <span className="mono dim">{loading ? "loading" : board?.odds_available ? "live odds" : "model only"}</span>
      </div>
      <div className="panel-body" style={{ display: "grid", gridTemplateColumns: "1.35fr 1fr", gap: 14 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
            <Metric
              label="BEST vs MKT"
              value={bestEdge == null ? "--" : pct(bestEdge.edge_vs_market ?? bestEdge.edge, 2)}
              color={edgeColor(bestEdge?.edge_vs_market ?? bestEdge?.edge ?? null)}
            />
            <Metric label="BEST PRICE" value={odds(bestEdge?.best_decimal)} sub={bestEdge?.best_book || "--"} />
            <Metric label="xG MODEL" value={`${(board?.expected_home_goals ?? 0).toFixed(2)}-${(board?.expected_away_goals ?? 0).toFixed(2)}`} />
            <Metric label="BOOKS" value={String(Math.max(0, ...sortedEdges.map((e) => e.book_count)))} />
          </div>

          {err ? <div className="mono" style={{ color: "var(--red)", fontSize: 11 }}>{err}</div> : null}
          {board?.notes.map((n) => (
            <div key={n} className="mono dim" style={{ fontSize: 11 }}>{n}</div>
          ))}

          <table className="tight" style={{ fontSize: 11 }}>
            <thead>
              <tr>
                <th>MARKET</th>
                <th>SELECTION</th>
                <th className="right">MODEL</th>
                <th className="right">FAIR</th>
                <th className="right">NO-VIG</th>
                <th className="right">BEST</th>
                <th className="right">EDGE</th>
                <th className="right">VS MKT</th>
                <th className="right">KELLY</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {sortedEdges.map((e) => {
                const leg = edgeToLeg(e, match);
                return (
                  <tr key={`${e.market_key}-${e.selection}`}>
                    <td className="mono dim">{e.market_group}</td>
                    <td className="mono">{e.label}</td>
                    <td className="right mono">{pct(e.model_probability)}</td>
                    <td className="right mono dim">{odds(e.fair_decimal_odds)}</td>
                    <td className="right mono dim">{e.no_vig_market_prob != null ? pct(e.no_vig_market_prob) : "--"}</td>
                    <td className="right mono">
                      {odds(e.best_decimal)}
                      <span className="dim"> {e.best_book ? e.best_book.slice(0, 8) : ""}</span>
                    </td>
                    <td className="right mono" style={{ color: edgeColor(e.edge) }}>{pct(e.edge, 2)}</td>
                    <td className="right mono" style={{ color: edgeColor(e.edge_vs_market) }}>
                      {e.edge_vs_market != null ? pct(e.edge_vs_market, 2) : "--"}
                    </td>
                    <td className="right mono dim">{pct(e.kelly_fraction, 2)}</td>
                    <td className="right">
                      <button
                        disabled={!leg}
                        onClick={() => leg && onUseLegs([leg])}
                        style={miniButton(Boolean(leg))}
                      >USE</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div>
            <div className="mono dim" style={{ fontSize: 10, letterSpacing: "0.15em", marginBottom: 6 }}>
              BOOK LADDER
            </div>
            {(bestEdge?.prices || []).length === 0 ? (
              <div className="empty" style={{ minHeight: 52 }}>no book prices for selected edge</div>
            ) : (
              <div style={{ display: "grid", gap: 4 }}>
                {(bestEdge?.prices || []).slice(0, 6).map((p) => (
                  <div key={`${p.book}-${p.decimal}`} style={{ display: "flex", justifyContent: "space-between" }}>
                    <span className="mono dim" style={{ fontSize: 11 }}>{p.book}</span>
                    <span className="mono" style={{ fontSize: 11 }}>{p.decimal.toFixed(2)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div>
            <div className="mono dim" style={{ fontSize: 10, letterSpacing: "0.15em", marginBottom: 6 }}>
              MODEL PARLAY BLUEPRINTS
            </div>
            {(board?.parlay_blueprints || []).length === 0 ? (
              <div className="empty" style={{ minHeight: 80 }}>no safer blueprints for this match</div>
            ) : (
              <div style={{ display: "grid", gap: 6 }}>
                {board!.parlay_blueprints.slice(0, 6).map((p) => (
                  <Blueprint key={p.label} blueprint={p} onUse={() => onUseLegs(p.legs)} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ border: "1px solid var(--border)", background: "rgba(255,255,255,0.02)", padding: "7px 8px", borderRadius: 3 }}>
      <div className="mono dim" style={{ fontSize: 9, letterSpacing: "0.14em" }}>{label}</div>
      <div className="mono" style={{ color: color || "var(--fg)", fontSize: 16 }}>{value}</div>
      {sub ? <div className="mono dim" style={{ fontSize: 9 }}>{sub}</div> : null}
    </div>
  );
}

function Blueprint({ blueprint, onUse }: { blueprint: SoccerParlayBlueprint; onUse: () => void }) {
  return (
    <div style={{ border: "1px solid var(--border)", padding: "7px 8px", borderRadius: 3, background: "rgba(255,255,255,0.02)" }}>
      <div className="mono" style={{ fontSize: 11, marginBottom: 5 }}>{blueprint.label}</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr auto", gap: 6, alignItems: "center" }}>
        <span className="mono dim" style={{ fontSize: 10 }}>P {pct(blueprint.fair_probability)}</span>
        <span className="mono dim" style={{ fontSize: 10 }}>fair {odds(blueprint.fair_decimal_odds)}</span>
        <span className="mono" style={{ fontSize: 10, color: riskColor(blueprint.risk_level) }}>
          {blueprint.safety_score.toFixed(0)} {blueprint.risk_level}
        </span>
        <button onClick={onUse} style={miniButton(true)}>LOAD</button>
      </div>
    </div>
  );
}

function miniButton(enabled: boolean): CSSProperties {
  return {
    background: enabled ? "rgba(80,180,255,0.14)" : "var(--bg-3)",
    color: enabled ? "var(--cyan)" : "var(--fg-3)",
    border: `1px solid ${enabled ? "var(--cyan-dim)" : "var(--border)"}`,
    padding: "2px 7px",
    borderRadius: 2,
    fontFamily: "var(--mono)",
    fontSize: 10,
    cursor: enabled ? "pointer" : "not-allowed",
  };
}
