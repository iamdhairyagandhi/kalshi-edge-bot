import { useState } from "react";
import type { CSSProperties } from "react";
import { api, SoccerBetLeg, SoccerBetslip, SoccerBetslipBatch } from "../../api/client";

type Props = {
  onUseLegs: (legs: SoccerBetLeg[], fixtureId?: string, bookOdds?: number | null) => void;
  onSelectFixture: (fixtureId: string) => void;
};

function pct(x: number | null | undefined, digits = 1): string {
  if (x == null || !Number.isFinite(x)) return "--";
  return `${(x * 100).toFixed(digits)}%`;
}

function money(x: number): string {
  return `$${x.toFixed(2)}`;
}

function riskColor(level: string): string {
  if (level === "safer") return "var(--green)";
  if (level === "moderate") return "var(--amber)";
  if (level === "scout") return "var(--cyan)";
  return "var(--red)";
}

function typeLabel(type: string): string {
  if (type === "cross_fixture_parlay") return "CROSS-GAME PARLAY";
  if (type === "model_parlay") return "SAME-GAME PARLAY";
  return "SINGLE";
}

function typeColor(type: string): string {
  if (type.includes("parlay")) return "var(--magenta)";
  return "var(--cyan)";
}

type Horizon = "today" | "week" | "all";

function horizonHours(h: Horizon): number | undefined {
  if (h === "all") return undefined;
  if (h === "week") return 24 * 7;
  // "today": seconds remaining until local midnight, rounded up to whole hours.
  const now = new Date();
  const end = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59, 999);
  const hours = Math.max(1, Math.ceil((end.getTime() - now.getTime()) / 3_600_000));
  return hours;
}

function horizonLabel(h: Horizon): string {
  if (h === "today") return "TODAY";
  if (h === "week") return "NEXT 7D";
  return "ALL UPCOMING";
}

function InfoTooltip({ label, desc }: { label: string; desc: string }) {
  return (
    <div title={desc} style={{ cursor: "help", borderBottom: "1px dotted var(--fg-2)" }}>
      {label} ⓘ
    </div>
  );
}

export default function BetslipCreatorPanel({ onUseLegs, onSelectFixture }: Props) {
  const [batch, setBatch] = useState<SoccerBetslipBatch | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [bankroll, setBankroll] = useState("1000");
  const [minEdge, setMinEdge] = useState("0.03");
  const [horizon, setHorizon] = useState<Horizon>("today");

  async function create(h: Horizon = horizon) {
    setLoading(true);
    setErr(null);
    try {
      const b = await api.soccerBetslips(
        14,
        parseFloat(bankroll),
        parseFloat(minEdge),
        horizonHours(h),
      );
      setBatch(b);
    } catch (e: any) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }

  function pickHorizon(h: Horizon) {
    if (h === horizon) return;
    setHorizon(h);
    // Only auto-refetch if the user has already loaded a batch — otherwise
    // first click on a pill shouldn't kick off an API call before they've
    // even set bankroll / min-edge.
    if (batch) {
      create(h);
    }
  }

  const bankrollNum = parseFloat(bankroll) || 0;

  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; BETSLIP CREATOR</span>
        <span className="mono dim">
          {batch
            ? `${batch.slips.length} SLIPS · ${money(batch.total_suggested_stake_usd)} TOTAL STAKE · ${horizonLabel(horizon)}`
            : `READY · ${horizonLabel(horizon)}`}
        </span>
      </div>
      <div className="panel-body" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {/* Input Controls */}
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <div title="Total money you're willing to use for all bets combined">
            <label className="mono dim" style={{ fontSize: 11 }}>bankroll</label>
            <Input value={bankroll} onChange={setBankroll} width={90} />
            <div className="mono dim" style={{ fontSize: 9, marginTop: 2 }}>{bankrollNum > 0 ? `${bankrollNum > 100 ? "can create ~" : ""}${Math.min(14, Math.floor(bankrollNum / 20))}-14 bets` : ""}</div>
          </div>
          <div title="Only show bets with at least this much edge. 0.08 = 8% advantage">
            <label className="mono dim" style={{ fontSize: 11 }}>min edge</label>
            <Input value={minEdge} onChange={setMinEdge} width={70} />
            <div className="mono dim" style={{ fontSize: 9, marginTop: 2 }}>({pct(parseFloat(minEdge) || 0)})</div>
          </div>
          <div style={{ display: "flex", gap: 4 }}>
            {(["today", "week", "all"] as Horizon[]).map((h) => (
              <button
                key={h}
                onClick={() => pickHorizon(h)}
                disabled={loading}
                style={pillStyle(horizon === h)}
              >{horizonLabel(h)}</button>
            ))}
          </div>
          <button
            onClick={() => create()}
            disabled={loading}
            style={{
              background: "rgba(25,195,125,0.18)", color: "var(--green)",
              border: "1px solid var(--green-dim)", padding: "4px 12px",
              borderRadius: 2, fontFamily: "var(--mono)", fontSize: 11,
              cursor: loading ? "wait" : "pointer", letterSpacing: "0.1em",
            }}
          >{loading ? "ANALYZING..." : "CREATE BETSLIPS"}</button>
        </div>

        {err ? <div className="mono" style={{ color: "var(--red)", fontSize: 11 }}>{err}</div> : null}
        {batch?.notes.map((n) => (
          <div key={n} className="mono dim" style={{ fontSize: 11 }}>{n}</div>
        ))}

        {!batch ? (
          <div className="empty" style={{ minHeight: 120, lineHeight: 1.5 }}>
            <div>⚙ Set bankroll and min edge, then click CREATE BETSLIPS</div>
            <div style={{ fontSize: 10, marginTop: 6, color: "var(--fg-2)" }}>System will find the best parlay combinations using live odds, model probabilities, Kelly sizing, correlation analysis, and safety filters.</div>
          </div>
        ) : batch.slips.length === 0 ? (
          <div className="empty" style={{ minHeight: 120 }}>⚠ No slips met your filters. Try lower min edge or check if matches are available.</div>
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {batch.slips.map((slip) => (
              <SlipCard
                key={slip.slip_id}
                slip={slip}
                onOpen={() => onSelectFixture(slip.fixture_id)}
                onLoad={() => onUseLegs(
                  slip.legs.map((l) => ({ kind: l.kind, params: l.params, label: l.label })),
                  slip.fixture_id,
                  slip.book_decimal_odds,
                )}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SlipCard({ slip, onOpen, onLoad }: { slip: SoccerBetslip; onOpen: () => void; onLoad: () => void }) {
  const scout = slip.scout_only === true;
  return (
    <div style={{
      border: scout ? "1px dashed var(--cyan-dim)" : "1px solid var(--border)",
      borderRadius: 4,
      background: scout ? "rgba(80,180,255,0.04)" : "rgba(255,255,255,0.02)",
      padding: "9px 10px",
    }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 10, alignItems: "start" }}>
        <div>
          <div className="mono" style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
            {scout && (
              <span className="mono" style={{
                fontSize: 9, letterSpacing: "0.12em", color: "var(--cyan)",
                border: "1px solid var(--cyan-dim)", padding: "1px 5px", borderRadius: 2,
                background: "rgba(80,180,255,0.10)",
              }}>SCOUT</span>
            )}
            {slip.title}
          </div>
          <div className="mono dim" style={{ fontSize: 10, marginTop: 2 }}>
            {slip.match_label} · <span style={{ color: typeColor(slip.slip_type) }}>{typeLabel(slip.slip_type)}</span>
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="mono" style={{ color: riskColor(slip.risk_level), fontSize: 13 }} title="Safety score: 0-100. Higher is safer.">
            <strong>{slip.safety_score.toFixed(0)}</strong> <span style={{ fontSize: 11 }}>{slip.risk_level.toUpperCase()}</span>
          </div>
          <div className="mono" style={{ color: slip.edge != null ? "var(--green)" : "var(--cyan)", fontSize: 13, marginTop: 2 }} title="Your predicted edge if book odds are correct">
            <strong>{slip.edge != null ? pct(slip.edge, 1) : "—"}</strong> <span style={{ fontSize: 10 }}>EDGE</span>
          </div>
        </div>
      </div>

      {/* Wager Summary - What You're Actually Betting */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, marginBottom: 10, padding: "8px", background: "rgba(25,195,125,0.08)", borderRadius: 3 }}>
        <div title="Amount to stake on this parlay">
          <div className="mono dim" style={{ fontSize: 9 }}>STAKE</div>
          <div className="mono" style={{ fontSize: 13, color: "var(--cyan)", marginTop: 2 }}>{money(slip.stake_usd)}</div>
        </div>
        <div title="If this bet wins, your return">
          <div className="mono dim" style={{ fontSize: 9 }}>IF WIN</div>
          <div className="mono" style={{ fontSize: 13, color: "var(--green)", marginTop: 2 }}>{money(willWin)}</div>
        </div>
        <div title="Your profit if this bet hits">
          <div className="mono dim" style={{ fontSize: 9 }}>PROFIT</div>
          <div className="mono" style={{ fontSize: 13, color: profit > 0 ? "var(--green)" : "var(--fg-1)", marginTop: 2 }}>{money(profit)}</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8, marginTop: 8 }}>
        <Mini label="MODEL" value={pct(slip.fair_probability)} />
        <Mini label="FAIR" value={slip.fair_decimal_odds.toFixed(2)} />
        <Mini label={slip.book_decimal_odds ? "BOOK" : "MIN BOOK"} value={(slip.book_decimal_odds ?? slip.minimum_acceptable_decimal).toFixed(2)} />
        {scout ? (
          <Mini label="STAKE" value="—" />
        ) : (
          <Mini label="STAKE" value={money(slip.stake_usd)} />
        )}
        <Mini label="CONF" value={pct(slip.confidence)} />
      </div>

      {/* Legs - What Outcomes You're Combining */}
      <div style={{ marginBottom: 8 }}>
        <div className="mono dim" style={{ fontSize: 9, marginBottom: 4 }}>LEGS (outcomes you're combining):</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
          {slip.legs.map((leg) => (
            <span key={`${leg.kind}-${leg.label}`} className="mono" style={{
              fontSize: 9, color: "var(--fg-1)", border: "1px solid var(--border)",
              padding: "3px 7px", borderRadius: 2, background: "var(--bg-3)",
            }}>{leg.label}</span>
          ))}
        </div>
      </div>

      {/* Why & Warnings */}
      {slip.reasons.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <div className="mono dim" style={{ fontSize: 9, marginBottom: 3 }}>✓ Why this bet:</div>
          {slip.reasons.slice(0, 2).map((r) => (
            <div key={r} className="mono dim" style={{ fontSize: 9, marginBottom: 2 }}>{r}</div>
          ))}
        </div>
      )}
      {slip.warnings.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <div className="mono" style={{ fontSize: 9, marginBottom: 3, color: "var(--amber)" }}>⚠ Considerations:</div>
          {slip.warnings.slice(0, 2).map((w) => (
            <div key={w} className="mono" style={{ fontSize: 9, color: "var(--amber-dim)", marginBottom: 2 }}>{w}</div>
          ))}
        </div>
      )}

      {/* Actions */}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--border-dim)" }}>
        <button onClick={onOpen} style={buttonStyle("ghost")}>OPEN MATCH</button>
        <button onClick={onLoad} style={buttonStyle("primary")}>
          {scout ? "VERIFY PRICE" : "LOAD BUILDER"}
        </button>
      </div>
    </div>
  );
}

function Input({ value, onChange, width }: { value: string; onChange: (v: string) => void; width: number }) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{
        background: "var(--bg-3)", color: "var(--fg)", border: "1px solid var(--border)",
        padding: "3px 6px", borderRadius: 2, fontFamily: "var(--mono)", fontSize: 11, width,
      }}
    />
  );
}

function buttonStyle(kind: "ghost" | "primary"): CSSProperties {
  const primary = kind === "primary";
  return {
    background: primary ? "rgba(80,180,255,0.16)" : "var(--bg-3)",
    color: primary ? "var(--cyan)" : "var(--fg-1)",
    border: `1px solid ${primary ? "var(--cyan-dim)" : "var(--border)"}`,
    padding: "3px 8px",
    borderRadius: 2,
    fontFamily: "var(--mono)",
    fontSize: 10,
    cursor: "pointer",
  };
}

function pillStyle(active: boolean): CSSProperties {
  return {
    background: active ? "rgba(80,180,255,0.18)" : "var(--bg-3)",
    color: active ? "var(--cyan)" : "var(--fg-2)",
    border: `1px solid ${active ? "var(--cyan-dim)" : "var(--border)"}`,
    padding: "3px 9px",
    borderRadius: 2,
    fontFamily: "var(--mono)",
    fontSize: 10,
    letterSpacing: "0.08em",
    cursor: "pointer",
  };
}
