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

export default function BetslipCreatorPanel({ onUseLegs, onSelectFixture }: Props) {
  const [batch, setBatch] = useState<SoccerBetslipBatch | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [bankroll, setBankroll] = useState("1000");
  const [minEdge, setMinEdge] = useState("0.03");
  const [horizon, setHorizon] = useState<Horizon>("today");

  async function create() {
    setLoading(true);
    setErr(null);
    try {
      const b = await api.soccerBetslips(
        14,
        parseFloat(bankroll),
        parseFloat(minEdge),
        horizonHours(horizon),
      );
      setBatch(b);
    } catch (e: any) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; BETSLIP CREATOR</span>
        <span className="mono dim">
          {batch
            ? `${batch.slips.length} slips · ${money(batch.total_suggested_stake_usd)} · ${horizonLabel(horizon)}`
            : `scanner · ${horizonLabel(horizon)}`}
        </span>
      </div>
      <div className="panel-body" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <label className="mono dim" style={{ fontSize: 11 }}>bankroll</label>
          <Input value={bankroll} onChange={setBankroll} width={90} />
          <label className="mono dim" style={{ fontSize: 11 }}>min edge</label>
          <Input value={minEdge} onChange={setMinEdge} width={70} />
          <div style={{ display: "flex", gap: 4 }}>
            {(["today", "week", "all"] as Horizon[]).map((h) => (
              <button
                key={h}
                onClick={() => setHorizon(h)}
                disabled={loading}
                style={pillStyle(horizon === h)}
              >{horizonLabel(h)}</button>
            ))}
          </div>
          <button
            onClick={create}
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
          <div className="empty" style={{ minHeight: 120 }}>
            creates ranked slips from live odds, model edge, Kelly sizing, parlay correlation, and risk filters
          </div>
        ) : batch.slips.length === 0 ? (
          <div className="empty" style={{ minHeight: 120 }}>no slips passed filters</div>
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
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 4, background: "rgba(255,255,255,0.02)", padding: "9px 10px" }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 10, alignItems: "start" }}>
        <div>
          <div className="mono" style={{ fontSize: 12 }}>{slip.title}</div>
          <div className="mono dim" style={{ fontSize: 10, marginTop: 2 }}>
            {slip.match_label} · <span style={{ color: typeColor(slip.slip_type) }}>{typeLabel(slip.slip_type)}</span>
          </div>
        </div>
        <div className="mono" style={{ color: riskColor(slip.risk_level), fontSize: 12, textAlign: "right" }}>
          {slip.safety_score.toFixed(0)} {slip.risk_level}
        </div>
        <div className="mono" style={{ color: slip.edge != null ? "var(--green)" : "var(--cyan)", fontSize: 12, textAlign: "right" }}>
          {slip.edge != null ? pct(slip.edge, 2) : `min ${slip.minimum_acceptable_decimal.toFixed(2)}`}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8, marginTop: 8 }}>
        <Mini label="MODEL" value={pct(slip.fair_probability)} />
        <Mini label="FAIR" value={slip.fair_decimal_odds.toFixed(2)} />
        <Mini label={slip.book_decimal_odds ? "BOOK" : "MIN BOOK"} value={(slip.book_decimal_odds ?? slip.minimum_acceptable_decimal).toFixed(2)} />
        <Mini label="STAKE" value={money(slip.stake_usd)} />
        <Mini label="CONF" value={pct(slip.confidence)} />
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 8 }}>
        {slip.legs.map((leg) => (
          <span key={`${leg.kind}-${leg.label}`} className="mono" style={{
            fontSize: 10, color: "var(--fg-1)", border: "1px solid var(--border)",
            padding: "2px 6px", borderRadius: 2, background: "var(--bg-3)",
          }}>{leg.label}</span>
        ))}
      </div>

      <div style={{ display: "grid", gap: 3, marginTop: 8 }}>
        {slip.reasons.slice(0, 3).map((r) => (
          <div key={r} className="mono dim" style={{ fontSize: 10 }}>{r}</div>
        ))}
        {slip.warnings.slice(0, 3).map((w) => (
          <div key={w} className="mono" style={{ fontSize: 10, color: "var(--amber)" }}>{w}</div>
        ))}
      </div>

      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 8 }}>
        <button onClick={onOpen} style={buttonStyle("ghost")}>OPEN MATCH</button>
        <button onClick={onLoad} style={buttonStyle("primary")}>LOAD BUILDER</button>
      </div>
    </div>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="mono dim" style={{ fontSize: 9, letterSpacing: "0.12em" }}>{label}</div>
      <div className="mono" style={{ fontSize: 12 }}>{value}</div>
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
