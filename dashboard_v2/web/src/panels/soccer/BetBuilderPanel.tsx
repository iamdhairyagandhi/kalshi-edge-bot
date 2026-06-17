/**
 * Interactive bet builder.
 *
 * Users add legs from a small library of leg kinds, optionally paste a
 * book combined price, and the panel posts to /api/soccer/bet-builder.
 * The response shows fair price, leg marginals, the joint correlation
 * factor, edge vs book, and Kelly stake.
 */

import { useEffect, useMemo, useState } from "react";
import {
  SoccerBetLeg,
  SoccerBetBuilderQuote,
  SoccerMatchSummary,
  api,
} from "../../api/client";

type Props = {
  match: SoccerMatchSummary | null;
  presetLegs?: SoccerBetLeg[];
  presetKey?: number;
  presetBookOdds?: number | null;
};

type LegDraft = {
  id: number;
  kind: string;
  params: Record<string, string | number>;
  label: string;
};

const LEG_KINDS = [
  { kind: "match_result",    desc: "1X2 result" },
  { kind: "total_goals",     desc: "Total goals over/under" },
  { kind: "btts",            desc: "Both teams to score" },
  { kind: "team_total",      desc: "Team total over/under" },
  { kind: "correct_score",   desc: "Correct score" },
  { kind: "anytime_scorer",  desc: "Anytime scorer" },
  { kind: "first_scorer",    desc: "First scorer" },
  { kind: "last_scorer",     desc: "Last scorer" },
  { kind: "player_yellow",   desc: "Player yellow card" },
  { kind: "player_red",      desc: "Player red card" },
  { kind: "total_cards",     desc: "Total cards over/under" },
] as const;

function defaultLeg(match: SoccerMatchSummary | null, kind: string): LegDraft {
  const home = match?.fixture.home_team_id || "HOME";
  const homeName = match?.fixture.home_team_name || home;
  const player = match?.top_scorer_probs[0]?.player_id || "";
  const playerName = match?.top_scorer_probs[0]?.name || "";
  const id = Math.floor(Math.random() * 1e9);
  switch (kind) {
    case "match_result":   return { id, kind, params: { side: "H" }, label: `${homeName} win` };
    case "total_goals":    return { id, kind, params: { line: 2.5, side: "over" }, label: "Over 2.5 goals" };
    case "btts":           return { id, kind, params: { side: "yes" }, label: "BTTS Yes" };
    case "team_total":     return { id, kind, params: { team: "home", line: 1.5, side: "over" }, label: `${homeName} over 1.5` };
    case "correct_score":  return { id, kind, params: { home: 2, away: 1 }, label: "Correct score 2-1" };
    case "anytime_scorer": return { id, kind, params: { player_id: player }, label: `${playerName} anytime` };
    case "first_scorer":   return { id, kind, params: { player_id: player }, label: `${playerName} first` };
    case "last_scorer":    return { id, kind, params: { player_id: player }, label: `${playerName} last` };
    case "player_yellow":  return { id, kind, params: { player_id: player }, label: `${playerName} yellow` };
    case "player_red":     return { id, kind, params: { player_id: player }, label: `${playerName} red` };
    case "total_cards":    return { id, kind, params: { line: 4.5, side: "over" }, label: "Over 4.5 cards" };
    default:               return { id, kind, params: {}, label: kind };
  }
}

function legParamEditor(
  leg: LegDraft,
  match: SoccerMatchSummary | null,
  onChange: (l: LegDraft) => void,
) {
  const set = (key: string, val: string | number) =>
    onChange({ ...leg, params: { ...leg.params, [key]: val } });

  const playerOpts = match?.top_scorer_probs ?? [];
  const homeId = match?.fixture.home_team_id || "HOME";
  const awayId = match?.fixture.away_team_id || "AWAY";

  const baseStyle: React.CSSProperties = {
    background: "var(--bg-3)", color: "var(--fg)", border: "1px solid var(--bg-4)",
    fontFamily: "var(--mono)", fontSize: 11, padding: "2px 6px", borderRadius: 2,
  };

  switch (leg.kind) {
    case "match_result":
      return (
        <select style={baseStyle} value={String(leg.params.side ?? "H")}
                onChange={(e) => set("side", e.target.value)}>
          <option value="H">Home win</option>
          <option value="D">Draw</option>
          <option value="A">Away win</option>
        </select>
      );
    case "total_goals":
    case "total_cards":
      return (
        <span style={{ display: "flex", gap: 6 }}>
          <input type="number" step={0.5} style={{ ...baseStyle, width: 60 }}
                 value={Number(leg.params.line ?? 2.5)}
                 onChange={(e) => set("line", parseFloat(e.target.value))} />
          <select style={baseStyle} value={String(leg.params.side ?? "over")}
                  onChange={(e) => set("side", e.target.value)}>
            <option value="over">Over</option>
            <option value="under">Under</option>
          </select>
        </span>
      );
    case "btts":
      return (
        <select style={baseStyle} value={String(leg.params.side ?? "yes")}
                onChange={(e) => set("side", e.target.value)}>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
      );
    case "team_total":
      return (
        <span style={{ display: "flex", gap: 6 }}>
          <select style={baseStyle} value={String(leg.params.team ?? "home")}
                  onChange={(e) => set("team", e.target.value)}>
            <option value="home">{homeId}</option>
            <option value="away">{awayId}</option>
          </select>
          <input type="number" step={0.5} style={{ ...baseStyle, width: 60 }}
                 value={Number(leg.params.line ?? 1.5)}
                 onChange={(e) => set("line", parseFloat(e.target.value))} />
          <select style={baseStyle} value={String(leg.params.side ?? "over")}
                  onChange={(e) => set("side", e.target.value)}>
            <option value="over">Over</option>
            <option value="under">Under</option>
          </select>
        </span>
      );
    case "correct_score":
      return (
        <span style={{ display: "flex", gap: 6 }}>
          <input type="number" min={0} max={9} style={{ ...baseStyle, width: 50 }}
                 value={Number(leg.params.home ?? 2)}
                 onChange={(e) => set("home", parseInt(e.target.value, 10))} />
          <span className="dim">-</span>
          <input type="number" min={0} max={9} style={{ ...baseStyle, width: 50 }}
                 value={Number(leg.params.away ?? 1)}
                 onChange={(e) => set("away", parseInt(e.target.value, 10))} />
        </span>
      );
    case "anytime_scorer":
    case "first_scorer":
    case "last_scorer":
    case "player_yellow":
    case "player_red":
      return (
        <select style={baseStyle} value={String(leg.params.player_id ?? "")}
                onChange={(e) => set("player_id", e.target.value)}>
          {playerOpts.length === 0 ? <option value="">—</option> : null}
          {playerOpts.map((p) => (
            <option key={p.player_id} value={p.player_id}>{p.name}</option>
          ))}
        </select>
      );
    default:
      return null;
  }
}

export default function BetBuilderPanel({ match, presetLegs, presetKey, presetBookOdds }: Props) {
  const [legs, setLegs] = useState<LegDraft[]>([]);
  const [bookOdds, setBookOdds] = useState<string>("");
  const [nSims, setNSims] = useState<number>(10000);
  const [quote, setQuote] = useState<SoccerBetBuilderQuote | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const canPrice = !!match && legs.length > 0;

  useEffect(() => {
    if (!presetLegs || presetLegs.length === 0) return;
    setLegs(presetLegs.map((l, i) => ({
      id: Date.now() + i,
      kind: l.kind,
      params: l.params as Record<string, string | number>,
      label: l.label || l.kind,
    })));
    setQuote(null);
    setErr(null);
    setBookOdds(presetBookOdds ? String(presetBookOdds) : "");
  }, [presetKey, presetLegs]);

  function addLeg(kind: string) {
    if (!match) return;
    const leg = defaultLeg(match, kind);
    setLegs((ls) => [...ls, leg]);
  }
  function removeLeg(id: number) { setLegs((ls) => ls.filter((l) => l.id !== id)); }
  function updateLeg(updated: LegDraft) {
    setLegs((ls) => ls.map((l) => (l.id === updated.id ? updated : l)));
  }

  async function priceParlay() {
    if (!match || legs.length === 0) return;
    setLoading(true);
    setErr(null);
    try {
      const payload: { fixture_id: string; legs: SoccerBetLeg[]; book_decimal_odds?: number; n_sims: number } = {
        fixture_id: match.fixture.fixture_id,
        legs: legs.map((l) => ({ kind: l.kind, params: l.params, label: l.label })),
        n_sims: nSims,
      };
      const bd = parseFloat(bookOdds);
      if (!isNaN(bd) && bd > 1.0) payload.book_decimal_odds = bd;
      const q = await api.soccerBetBuilder(payload);
      setQuote(q);
    } catch (e: any) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }

  const recColor = useMemo(() => {
    if (!quote) return "var(--fg)";
    if (quote.recommendation === "bet") return "var(--green)";
    if (quote.recommendation === "thin_edge") return "var(--amber)";
    if (quote.recommendation === "risky_edge") return "var(--amber)";
    return "var(--red)";
  }, [quote]);

  const riskColor = useMemo(() => {
    if (!quote) return "var(--fg)";
    if (quote.risk_level === "safer") return "var(--green)";
    if (quote.risk_level === "moderate") return "var(--amber)";
    return "var(--red)";
  }, [quote]);

  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; BET BUILDER</span>
        <span className="mono dim">{legs.length} legs</span>
      </div>
      <div className="panel-body" style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 16 }}>
        <div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
            {LEG_KINDS.map((k) => (
              <button
                key={k.kind}
                disabled={!match}
                onClick={() => addLeg(k.kind)}
                style={{
                  background: "var(--bg-3)", color: "var(--fg-2)",
                  border: "1px solid var(--bg-4)", padding: "2px 8px",
                  fontFamily: "var(--mono)", fontSize: 10, borderRadius: 2,
                  cursor: match ? "pointer" : "not-allowed",
                }}
                title={k.desc}
              >+ {k.kind}</button>
            ))}
          </div>
          {legs.length === 0 ? (
            <div className="empty">add legs above to build a parlay</div>
          ) : (
            <table className="tight" style={{ fontSize: 11 }}>
              <thead>
                <tr><th>KIND</th><th>SELECTION</th><th className="right">P(leg)</th><th></th></tr>
              </thead>
              <tbody>
                {legs.map((leg, i) => {
                  const p = quote?.leg_probabilities?.[i];
                  return (
                    <tr key={leg.id}>
                      <td className="mono dim">{leg.kind}</td>
                      <td>{legParamEditor(leg, match, updateLeg)}</td>
                      <td className="right mono">{p == null ? "—" : `${(p * 100).toFixed(1)}%`}</td>
                      <td>
                        <button
                          onClick={() => removeLeg(leg.id)}
                          style={{
                            background: "rgba(255,77,79,0.15)", color: "var(--red)",
                            border: "1px solid var(--red-dim)", padding: "1px 6px",
                            fontFamily: "var(--mono)", fontSize: 10, borderRadius: 2,
                            cursor: "pointer",
                          }}
                        >x</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 12, flexWrap: "wrap" }}>
            <label className="mono dim" style={{ fontSize: 11 }}>book combined</label>
            <input type="number" step={0.01} value={bookOdds}
                   onChange={(e) => setBookOdds(e.target.value)}
                   placeholder="e.g. 8.50"
                   style={{
                     background: "var(--bg-3)", color: "var(--fg)",
                     border: "1px solid var(--bg-4)", padding: "2px 6px",
                     fontFamily: "var(--mono)", fontSize: 11, width: 90, borderRadius: 2,
                   }} />
            <label className="mono dim" style={{ fontSize: 11 }}>sims</label>
            <input type="number" min={500} max={50000} step={1000}
                   value={nSims} onChange={(e) => setNSims(parseInt(e.target.value, 10))}
                   style={{
                     background: "var(--bg-3)", color: "var(--fg)",
                     border: "1px solid var(--bg-4)", padding: "2px 6px",
                     fontFamily: "var(--mono)", fontSize: 11, width: 80, borderRadius: 2,
                   }} />
            <button
              disabled={!canPrice || loading}
              onClick={priceParlay}
              style={{
                background: canPrice ? "rgba(25,195,125,0.18)" : "var(--bg-3)",
                color: canPrice ? "var(--green)" : "var(--fg-2)",
                border: `1px solid ${canPrice ? "var(--green-dim)" : "var(--bg-4)"}`,
                padding: "3px 12px", borderRadius: 2, fontFamily: "var(--mono)",
                fontSize: 11, cursor: canPrice ? "pointer" : "not-allowed",
                letterSpacing: "0.1em",
              }}
            >{loading ? "PRICING…" : "PRICE PARLAY"}</button>
          </div>
          {err ? <div className="mono" style={{ color: "var(--red)", fontSize: 11, marginTop: 8 }}>{err}</div> : null}
        </div>
        <div>
          {quote ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <Stat label="FAIR PROB"
                    value={`${(quote.fair_probability * 100).toFixed(2)}%`}
                    sub={`fair odds ${quote.fair_decimal_odds.toFixed(2)}`} />
              <Stat label="CORRELATION FACTOR"
                    value={isFinite(quote.correlation_factor) ? quote.correlation_factor.toFixed(3) : "—"}
                    sub={`indep prod ${quote.independent_product.toExponential(2)}`} />
              <Stat label="SAFETY SCORE"
                    value={`${quote.safety_score.toFixed(0)}/100`}
                    sub={quote.risk_level.toUpperCase()}
                    color={riskColor} />
              {quote.book_decimal_odds != null && (
                <>
                  <Stat label="BOOK ODDS"
                        value={quote.book_decimal_odds.toFixed(2)}
                        sub={`implied ${(100 / quote.book_decimal_odds).toFixed(2)}%`} />
                  <Stat label="EDGE"
                        value={quote.edge != null ? `${(quote.edge * 100).toFixed(2)}%` : "—"}
                        sub={`kelly ${quote.kelly_fraction != null ? (quote.kelly_fraction * 100).toFixed(2) : "0.00"}%`}
                        color={(quote.edge ?? 0) > 0 ? "var(--green)" : "var(--red)"} />
                </>
              )}
              <div style={{
                padding: "6px 10px", border: `1px solid ${recColor}`, borderRadius: 2,
                fontFamily: "var(--mono)", fontSize: 12, color: recColor,
                letterSpacing: "0.15em", textAlign: "center", marginTop: 6,
              }}>{quote.recommendation.toUpperCase()}</div>
              {quote.risk_flags.length > 0 ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {quote.risk_flags.map((flag) => (
                    <div key={flag} className="mono" style={{ color: "var(--amber)", fontSize: 10 }}>
                      {flag}
                    </div>
                  ))}
                </div>
              ) : null}
              {quote.ai_review ? (
                <div style={{
                  border: "1px solid var(--bg-4)", borderRadius: 2, padding: "7px 9px",
                  background: "rgba(80,180,255,0.08)",
                }}>
                  <div className="mono dim" style={{ fontSize: 10, letterSpacing: "0.15em", marginBottom: 4 }}>
                    AI RISK REVIEW
                  </div>
                  <div className="mono" style={{ fontSize: 11, lineHeight: 1.45 }}>{quote.ai_review}</div>
                </div>
              ) : null}
              {quote.notes ? <div className="dim mono" style={{ fontSize: 11 }}>{quote.notes}</div> : null}
              <div className="dim mono" style={{ fontSize: 10 }}>{quote.n_sims.toLocaleString()} sims</div>
            </div>
          ) : (
            <div className="empty">price a parlay to see results</div>
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div>
      <div className="mono dim" style={{ fontSize: 10, letterSpacing: "0.15em" }}>{label}</div>
      <div className="mono" style={{ fontSize: 18, color: color || "var(--fg)" }}>{value}</div>
      {sub ? <div className="mono dim" style={{ fontSize: 10 }}>{sub}</div> : null}
    </div>
  );
}
