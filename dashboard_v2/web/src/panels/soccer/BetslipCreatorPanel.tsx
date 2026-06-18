import { useState } from "react";
import type { CSSProperties } from "react";
import {
  api,
  RecordSoccerBetPayload,
  SoccerBetLegRecord,
  SoccerBetLeg,
  SoccerBetQualificationCheck,
  SoccerBetSource,
  SoccerBetslip,
  SoccerBetslipBatch,
  SoccerGuardrailReport,
  SoccerGuardrailsBlockedError,
} from "../../api/client";

type Props = {
  onUseLegs: (legs: SoccerBetLeg[], fixtureId?: string, bookOdds?: number | null) => void;
  onSelectFixture: (fixtureId: string) => void;
  onBetRecorded?: () => void;
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

type GateCheck = { label: string; pass: boolean; detail: string; severity?: "warn" | "hard" };
type GateResult = {
  status: "BETTABLE" | "NEEDS PRICE" | "NO BET" | "REVIEW";
  color: string;
  checks: GateCheck[];
  availableOdds: number | null;
  edge: number | null;
  stake: number;
  source: "live" | "pasted" | "none";
};

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

export default function BetslipCreatorPanel({ onUseLegs, onSelectFixture, onBetRecorded }: Props) {
  const [batch, setBatch] = useState<SoccerBetslipBatch | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [bankroll, setBankroll] = useState("1000");
  const [minEdge, setMinEdge] = useState("0.03");
  const [horizon, setHorizon] = useState<Horizon>("today");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [manualOdds, setManualOdds] = useState<Record<string, string>>({});

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
      setSelectedIds(new Set());
      setManualOdds({});
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
  const minEdgeNum = parseFloat(minEdge) || 0;
  const slips = batch?.slips ?? [];
  const selectedSlips = slips.filter((s) => selectedIds.has(s.slip_id));
  const targetSlips = selectedSlips.length > 0 ? selectedSlips : slips;
  const exposure = summarizeExposure(slips, manualOdds, bankrollNum, minEdgeNum);

  function toggleSlip(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectAll() {
    setSelectedIds(new Set(slips.map((s) => s.slip_id)));
  }

  function clearSelection() {
    setSelectedIds(new Set());
  }

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
          <div>
            <label className="mono dim" style={{ fontSize: 11 }}>games</label>
            <div style={{ display: "flex", gap: 4, marginTop: 2 }}>
            {(["today", "week", "all"] as Horizon[]).map((h) => (
              <button
                key={h}
                onClick={() => pickHorizon(h)}
                disabled={loading}
                style={pillStyle(horizon === h)}
              >{horizonLabel(h)}</button>
            ))}
            </div>
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
        <MarketCoveragePanel />
        {slips.length > 0 ? <ExposureSummary summary={exposure} bankroll={bankrollNum} /> : null}
        {slips.length > 0 ? (
          <div style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 10,
            flexWrap: "wrap",
            border: "1px solid var(--border)",
            borderRadius: 4,
            padding: "7px 8px",
            background: "rgba(255,255,255,0.018)",
          }}>
            <div className="mono dim" style={{ fontSize: 10 }}>
              {selectedSlips.length > 0
                ? `${selectedSlips.length}/${slips.length} SELECTED`
                : `${slips.length} AVAILABLE · EXPORT/PRINT USES ALL UNLESS YOU SELECT SLIPS`}
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <button onClick={selectAll} style={buttonStyle("ghost")}>SELECT ALL</button>
              <button onClick={clearSelection} disabled={selectedIds.size === 0} style={buttonStyle("ghost")}>CLEAR</button>
              <button onClick={() => exportBetslips(targetSlips, horizonLabel(horizon))} style={buttonStyle("primary")}>
                EXPORT {selectedSlips.length > 0 ? "SELECTED" : "ALL"}
              </button>
              <button onClick={() => printBetslips(targetSlips, horizonLabel(horizon))} style={buttonStyle("primary")}>
                PRINT {selectedSlips.length > 0 ? "SELECTED" : "ALL"}
              </button>
            </div>
          </div>
        ) : null}

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
                selected={selectedIds.has(slip.slip_id)}
                onToggle={() => toggleSlip(slip.slip_id)}
                manualOdds={manualOdds[slip.slip_id] ?? ""}
                bankroll={bankrollNum}
                minEdge={minEdgeNum}
                onManualOdds={(value) => setManualOdds((prev) => ({ ...prev, [slip.slip_id]: value }))}
                onExport={() => exportBetslips([slip], horizonLabel(horizon))}
                onPrint={() => printBetslips([slip], horizonLabel(horizon))}
                onOpen={() => onSelectFixture(slip.fixture_id)}
                onLoad={() => onUseLegs(
                  slip.legs.map((l) => ({ kind: l.kind, params: l.params, label: l.label })),
                  slip.fixture_id,
                  slip.book_decimal_odds,
                )}
                onBetRecorded={onBetRecorded}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SlipCard({
  slip,
  selected,
  onToggle,
  manualOdds,
  bankroll,
  minEdge,
  onManualOdds,
  onExport,
  onPrint,
  onOpen,
  onLoad,
  onBetRecorded,
}: {
  slip: SoccerBetslip;
  selected: boolean;
  onToggle: () => void;
  manualOdds: string;
  bankroll: number;
  minEdge: number;
  onManualOdds: (value: string) => void;
  onExport: () => void;
  onPrint: () => void;
  onOpen: () => void;
  onLoad: () => void;
  onBetRecorded?: () => void;
}) {
  const scout = slip.scout_only === true;
  const gate = qualifySlip(slip, manualOdds, bankroll, minEdge);
  const odds = slip.book_decimal_odds ?? slip.minimum_acceptable_decimal;
  const willWin = slip.stake_usd * odds;
  const profit = willWin - slip.stake_usd;
  return (
    <div style={{
      border: scout ? "1px dashed var(--cyan-dim)" : "1px solid var(--border)",
      borderRadius: 4,
      background: scout ? "rgba(80,180,255,0.04)" : "rgba(255,255,255,0.02)",
      padding: "9px 10px",
    }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 10, alignItems: "start" }}>
        <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggle}
            title="Select this betslip for export/print"
            style={{ marginTop: 2, accentColor: "var(--cyan)" }}
          />
          <div>
          <div className="mono" style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
            {scout && (
              <span className="mono" style={{
                fontSize: 9, letterSpacing: "0.12em", color: "var(--cyan)",
                border: "1px solid var(--cyan-dim)", padding: "1px 5px", borderRadius: 2,
                background: "rgba(80,180,255,0.10)",
              }}>SCOUT</span>
            )}
            <span className="mono" style={{
              fontSize: 9,
              letterSpacing: "0.12em",
              color: gate.color,
              border: `1px solid ${gate.color}`,
              padding: "1px 5px",
              borderRadius: 2,
              background: "rgba(255,255,255,0.04)",
            }}>{gate.status}</span>
            {slip.title}
          </div>
          <div className="mono dim" style={{ fontSize: 10, marginTop: 2 }}>
            {slip.match_label} · <span style={{ color: typeColor(slip.slip_type) }}>{typeLabel(slip.slip_type)}</span>
          </div>
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

      {/* Wager Summary - What You're Actually Betting (only for non-scout bets) */}
      {!scout && (
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
      )}

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

      <GuruBlock slip={slip} />
      <RatingWindow slip={slip} />
      <DecisionStrip result={gate} />
      <Bet365PriceCheck
        slip={slip}
        manualOdds={manualOdds}
        bankroll={bankroll}
        minEdge={minEdge}
        onManualOdds={onManualOdds}
      />
      <QualificationGate result={gate} />

      <RecordBetWidget
        slip={slip}
        gate={gate}
        manualOdds={manualOdds}
        onRecorded={onBetRecorded}
      />

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
        <button onClick={onExport} style={buttonStyle("ghost")}>EXPORT</button>
        <button onClick={onPrint} style={buttonStyle("ghost")}>PRINT</button>
        <button onClick={onOpen} style={buttonStyle("ghost")}>OPEN MATCH</button>
        <button onClick={onLoad} style={buttonStyle("primary")}>
          {scout ? "VERIFY PRICE" : "LOAD BUILDER"}
        </button>
      </div>
    </div>
  );
}

function MarketCoveragePanel() {
  const columns = [
    {
      title: "LIVE ODDS",
      tone: "var(--green)",
      items: ["1X2", "Goal totals", "BTTS", "Alt totals*", "Anytime scorer*"],
      note: "*Only when the odds provider/book exposes it.",
    },
    {
      title: "MODEL SCOUT",
      tone: "var(--cyan)",
      items: ["Correct score", "Corners", "Shots", "SOT", "Cards", "Fouls", "Team props"],
      note: "Priced by simulation. Verify book odds before staking.",
    },
    {
      title: "NEEDS FEED",
      tone: "var(--amber)",
      items: ["Fouls won", "Tackles", "Offsides", "Keeper saves", "Bet365 SGP price"],
      note: "Requires a richer paid sportsbook/stat feed.",
    },
  ];
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
      gap: 8,
      border: "1px solid var(--border-dim)",
      borderRadius: 4,
      padding: 8,
      background: "rgba(255,255,255,0.012)",
    }}>
      {columns.map((col) => (
        <div key={col.title}>
          <div className="mono" style={{ color: col.tone, fontSize: 9, letterSpacing: "0.14em", marginBottom: 5 }}>{col.title}</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {col.items.map((item) => (
              <span key={item} className="mono" style={{
                fontSize: 9,
                color: "var(--fg-1)",
                border: "1px solid var(--border)",
                borderRadius: 2,
                padding: "2px 5px",
                background: "var(--bg-3)",
              }}>{item}</span>
            ))}
          </div>
          <div className="mono dim" style={{ fontSize: 8, lineHeight: 1.35, marginTop: 5 }}>{col.note}</div>
        </div>
      ))}
    </div>
  );
}

type ExposureSummaryData = {
  total: number;
  bettable: number;
  review: number;
  needsPrice: number;
  noBet: number;
  stake: number;
  maxReturn: number;
  byMatch: { match: string; stake: number; count: number }[];
};

function ExposureSummary({ summary, bankroll }: { summary: ExposureSummaryData; bankroll: number }) {
  const stakePct = bankroll > 0 ? summary.stake / bankroll : 0;
  const exposureColor = stakePct <= 0.04 ? "var(--green)" : stakePct <= 0.08 ? "var(--amber)" : "var(--red)";
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(6, minmax(0, 1fr))",
      gap: 8,
      border: "1px solid var(--border)",
      borderRadius: 4,
      padding: 8,
      background: "rgba(255,255,255,0.018)",
    }}>
      <SummaryCell label="BETTABLE" value={String(summary.bettable)} color="var(--green)" />
      <SummaryCell label="REVIEW" value={String(summary.review)} color="var(--amber)" />
      <SummaryCell label="NEEDS PRICE" value={String(summary.needsPrice)} color="var(--cyan)" />
      <SummaryCell label="NO BET" value={String(summary.noBet)} color="var(--red)" />
      <SummaryCell label="QUAL STAKE" value={money(summary.stake)} color={exposureColor} />
      <SummaryCell label="MAX RETURN" value={money(summary.maxReturn)} color="var(--fg-0)" />
      <div style={{ gridColumn: "1 / -1" }}>
        <div className="mono dim" style={{ fontSize: 9, marginBottom: 4 }}>
          MATCH EXPOSURE {bankroll > 0 ? `· ${pct(stakePct)} OF BANKROLL` : ""}
        </div>
        {summary.byMatch.length === 0 ? (
          <div className="mono dim" style={{ fontSize: 9 }}>No qualified stake yet. Paste/verify prices first.</div>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
            {summary.byMatch.slice(0, 6).map((m) => (
              <span key={m.match} className="mono" style={{
                fontSize: 9,
                border: "1px solid var(--border)",
                borderRadius: 2,
                padding: "3px 6px",
                color: "var(--fg-1)",
                background: "var(--bg-3)",
              }}>{m.match}: {money(m.stake)} ({m.count})</span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SummaryCell({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div>
      <div className="mono dim" style={{ fontSize: 8, letterSpacing: "0.12em" }}>{label}</div>
      <div className="mono" style={{ fontSize: 13, color, marginTop: 2 }}>{value}</div>
    </div>
  );
}

function summarizeExposure(
  slips: SoccerBetslip[],
  manualOdds: Record<string, string>,
  bankroll: number,
  minEdge: number,
): ExposureSummaryData {
  const out: ExposureSummaryData = {
    total: slips.length,
    bettable: 0,
    review: 0,
    needsPrice: 0,
    noBet: 0,
    stake: 0,
    maxReturn: 0,
    byMatch: [],
  };
  const byMatch = new Map<string, { match: string; stake: number; count: number }>();
  for (const slip of slips) {
    const gate = qualifySlip(slip, manualOdds[slip.slip_id] ?? "", bankroll, minEdge);
    if (gate.status === "BETTABLE") out.bettable += 1;
    else if (gate.status === "REVIEW") out.review += 1;
    else if (gate.status === "NEEDS PRICE") out.needsPrice += 1;
    else out.noBet += 1;
    if (gate.status === "BETTABLE" || gate.status === "REVIEW") {
      const stake = gate.stake;
      out.stake += stake;
      out.maxReturn += gate.availableOdds ? stake * gate.availableOdds : 0;
      const existing = byMatch.get(slip.match_label) ?? { match: slip.match_label, stake: 0, count: 0 };
      existing.stake += stake;
      existing.count += 1;
      byMatch.set(slip.match_label, existing);
    }
  }
  out.stake = Math.round(out.stake * 100) / 100;
  out.maxReturn = Math.round(out.maxReturn * 100) / 100;
  out.byMatch = Array.from(byMatch.values()).sort((a, b) => b.stake - a.stake);
  return out;
}

function Bet365PriceCheck({
  slip,
  manualOdds,
  bankroll,
  minEdge,
  onManualOdds,
}: {
  slip: SoccerBetslip;
  manualOdds: string;
  bankroll: number;
  minEdge: number;
  onManualOdds: (value: string) => void;
}) {
  const pasted = parseFloat(manualOdds);
  const hasPasted = Number.isFinite(pasted) && pasted > 1;
  const modelProb = slip.fair_probability;
  const edge = hasPasted ? modelProb * pasted - 1 : null;
  const bettable = edge != null && edge >= minEdge && pasted >= slip.minimum_acceptable_decimal;
  const stake = bettable ? suggestedStake(bankroll, modelProb, pasted, slip.confidence) : 0;
  const returnIfWin = stake * (hasPasted ? pasted : 0);
  const profitIfWin = returnIfWin - stake;
  return (
    <div style={{
      border: `1px solid ${bettable ? "var(--green-dim)" : "var(--border-dim)"}`,
      borderRadius: 3,
      padding: "7px 8px",
      marginBottom: 8,
      background: bettable ? "rgba(25,195,125,0.07)" : "rgba(255,255,255,0.012)",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <div>
          <div className="mono" style={{ fontSize: 9, letterSpacing: "0.14em", color: bettable ? "var(--green)" : "var(--cyan)" }}>
            BET365 PRICE CHECK
          </div>
          <div className="mono dim" style={{ fontSize: 9, marginTop: 3 }}>
            Need {slip.minimum_acceptable_decimal.toFixed(2)}+ decimal odds to clear your edge filter.
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span className="mono dim" style={{ fontSize: 10 }}>book odds</span>
          <input
            value={manualOdds}
            onChange={(e) => onManualOdds(e.target.value)}
            placeholder="e.g. 2.40"
            inputMode="decimal"
            style={{
              background: "var(--bg-3)",
              color: "var(--fg)",
              border: `1px solid ${bettable ? "var(--green-dim)" : "var(--border)"}`,
              padding: "3px 6px",
              borderRadius: 2,
              fontFamily: "var(--mono)",
              fontSize: 11,
              width: 84,
            }}
          />
        </div>
      </div>
      {hasPasted ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8, marginTop: 8 }}>
          <Mini label="STATUS" value={bettable ? "LIVE BET" : "NO BET"} tone={bettable ? "green" : "dim"} />
          <Mini label="EDGE" value={edge == null ? "--" : pct(edge)} tone={bettable ? "green" : "dim"} />
          <Mini label="STAKE" value={bettable ? money(stake) : "$0.00"} tone={bettable ? "cyan" : "dim"} />
          <Mini label="RETURN" value={bettable ? money(returnIfWin) : "--"} tone={bettable ? "green" : "dim"} />
          <Mini label="PROFIT" value={bettable ? money(profitIfWin) : "--"} tone={bettable ? "green" : "dim"} />
        </div>
      ) : (
        <div className="mono dim" style={{ fontSize: 9, marginTop: 7 }}>
          Paste the Bet365 combined decimal odds here. If the price is good enough, this scout turns into a live bet.
        </div>
      )}
      {hasPasted && !bettable ? (
        <div className="mono" style={{ color: "var(--amber)", fontSize: 9, marginTop: 6 }}>
          Price is too low for this model edge. Pass unless Bet365 offers at least {slip.minimum_acceptable_decimal.toFixed(2)}.
        </div>
      ) : null}
    </div>
  );
}

function DecisionStrip({ result }: { result: GateResult }) {
  const copy =
    result.status === "BETTABLE"
      ? "BETTABLE: price and risk checks cleared. Stake small and record the placed odds."
      : result.status === "REVIEW"
        ? "REVIEW: positive enough to inspect, but at least one warning remains."
        : result.status === "NEEDS PRICE"
          ? "DO NOT BET YET: paste or fetch real sportsbook odds first."
          : "NO BET: price/risk checks failed. Pass unless the market changes.";
  return (
    <div className="mono" style={{
      color: result.color,
      border: `1px solid ${result.color}`,
      borderRadius: 3,
      padding: "6px 8px",
      marginBottom: 8,
      fontSize: 9,
      letterSpacing: "0.04em",
      background: "rgba(255,255,255,0.018)",
    }}>
      {copy}
    </div>
  );
}

function QualificationGate({ result }: { result: GateResult }) {
  const passed = result.checks.filter((c) => c.pass).length;
  return (
    <div style={{
      border: `1px solid ${result.color}`,
      borderRadius: 3,
      padding: "7px 8px",
      marginBottom: 8,
      background: result.status === "BETTABLE" ? "rgba(25,195,125,0.065)" : "rgba(255,255,255,0.012)",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <div className="mono" style={{ color: result.color, fontSize: 9, letterSpacing: "0.14em" }}>
          BET QUALIFICATION GATE
        </div>
        <div className="mono" style={{ color: result.color, fontSize: 10 }}>
          {result.status} · {passed}/{result.checks.length}
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 5 }}>
        {result.checks.map((check) => (
          <div key={check.label} title={check.detail} style={{
            display: "flex",
            gap: 5,
            alignItems: "flex-start",
            border: "1px solid var(--border-dim)",
            borderRadius: 2,
            padding: "4px 5px",
            background: "rgba(255,255,255,0.012)",
          }}>
            <span className="mono" style={{ color: check.pass ? "var(--green)" : check.severity === "warn" ? "var(--amber)" : "var(--red)", fontSize: 10 }}>
              {check.pass ? "PASS" : check.severity === "warn" ? "WARN" : "FAIL"}
            </span>
            <div>
              <div className="mono" style={{ fontSize: 9, color: "var(--fg-1)" }}>{check.label}</div>
              <div className="mono dim" style={{ fontSize: 8, lineHeight: 1.3, marginTop: 1 }}>{check.detail}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function qualifySlip(slip: SoccerBetslip, manualOdds: string, bankroll: number, minEdge: number): GateResult {
  const pasted = parseFloat(manualOdds);
  const pastedValid = Number.isFinite(pasted) && pasted > 1;
  const source: GateResult["source"] = slip.book_decimal_odds ? "live" : pastedValid ? "pasted" : "none";
  const availableOdds = slip.book_decimal_odds ?? (pastedValid ? pasted : null);
  const edge = availableOdds ? slip.fair_probability * availableOdds - 1 : null;
  const stake = availableOdds
    ? (slip.book_decimal_odds ? slip.stake_usd : suggestedStake(bankroll, slip.fair_probability, availableOdds, slip.confidence))
    : 0;
  const playerKinds = new Set(["anytime_scorer", "first_scorer", "last_scorer", "player_yellow", "player_red"]);
  const hasPlayerProp = slip.legs.some((l) => playerKinds.has(l.kind));
  const highVariance = slip.risk_level === "high" || slip.risk_level === "lottery" || slip.risk_flags.some((f) => /lottery|high-variance|too many/i.test(f));
  const sameGameParlay = slip.slip_type === "model_parlay";
  const checks: GateCheck[] = [
    {
      label: "Price verified",
      pass: source !== "none",
      detail: source === "live" ? `Live book odds ${availableOdds?.toFixed(2)} available.` : source === "pasted" ? `Pasted Bet365 odds ${availableOdds?.toFixed(2)}.` : "No sportsbook price yet; this remains scout-only.",
      severity: "hard",
    },
    {
      label: "Edge clears filter",
      pass: edge != null && edge >= minEdge,
      detail: edge == null ? "Cannot calculate edge without odds." : `Edge ${pct(edge)} vs required ${pct(minEdge)}.`,
      severity: "hard",
    },
    {
      label: "Minimum odds",
      pass: availableOdds != null && availableOdds >= slip.minimum_acceptable_decimal,
      detail: availableOdds == null ? `Need ${slip.minimum_acceptable_decimal.toFixed(2)}+.` : `${availableOdds.toFixed(2)} vs minimum ${slip.minimum_acceptable_decimal.toFixed(2)}.`,
      severity: "hard",
    },
    {
      label: "Risk level",
      pass: !highVariance,
      detail: highVariance ? `Blocked/high risk: ${slip.risk_flags[0] || slip.risk_level}.` : `${slip.risk_level} risk is acceptable for review.`,
      severity: highVariance ? "hard" : undefined,
    },
    {
      label: "Model trust",
      pass: slip.safety_score >= 65 && slip.confidence >= 0.55,
      detail: `Safety ${slip.safety_score.toFixed(0)}/100, confidence ${pct(slip.confidence)}.`,
      severity: "warn",
    },
    {
      label: "Lineup/player props",
      pass: !hasPlayerProp,
      detail: hasPlayerProp ? "Player prop detected: do not bet until lineup/minutes are confirmed." : "No player-prop lineup dependency detected.",
      severity: "warn",
    },
    {
      label: "Parlay/correlation",
      pass: slip.legs.length <= 3 && (!sameGameParlay || source === "pasted"),
      detail: sameGameParlay && source !== "pasted"
        ? "Same-game parlay needs a real Bet365 SGP price before qualification."
        : `${slip.legs.length} leg(s); correlation priced by simulation, but book SGP tax still matters.`,
      severity: sameGameParlay && source !== "pasted" ? "hard" : "warn",
    },
    {
      label: "Stake exposure",
      pass: bankroll <= 0 ? true : stake <= bankroll * 0.02,
      detail: bankroll <= 0 ? "No bankroll set." : `Suggested stake ${money(stake)} vs 2% cap ${money(bankroll * 0.02)}.`,
      severity: "hard",
    },
  ];
  const hardFails = checks.filter((c) => !c.pass && c.severity !== "warn").length;
  const warns = checks.filter((c) => !c.pass && c.severity === "warn").length;
  let status: GateResult["status"] = "BETTABLE";
  if (source === "none") status = "NEEDS PRICE";
  else if (hardFails > 0) status = "NO BET";
  else if (warns > 0) status = "REVIEW";
  const color = status === "BETTABLE" ? "var(--green)" : status === "REVIEW" ? "var(--amber)" : status === "NEEDS PRICE" ? "var(--cyan)" : "var(--red)";
  return { status, color, checks, availableOdds, edge, stake, source };
}

function suggestedStake(bankroll: number, probability: number, decimalOdds: number, confidence: number): number {
  const b = decimalOdds - 1;
  if (bankroll <= 0 || b <= 0 || probability <= 0 || probability >= 1) return 0;
  const fullKelly = (b * probability - (1 - probability)) / b;
  const fractionalKelly = Math.max(0, fullKelly) * 0.25;
  const capped = Math.min(fractionalKelly, 0.02);
  return Math.round(bankroll * capped * Math.max(0.25, Math.min(0.9, confidence)) * 100) / 100;
}

function RatingWindow({ slip }: { slip: SoccerBetslip }) {
  const ratings = rateSlip(slip);
  const avg = ratings.reduce((sum, r) => sum + r.score, 0) / ratings.length;
  return (
    <div style={{
      border: "1px solid var(--border)",
      borderRadius: 3,
      padding: "7px 8px",
      marginBottom: 8,
      background: "rgba(255,255,255,0.018)",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 6 }}>
        <div className="mono" style={{ fontSize: 9, letterSpacing: "0.14em", color: "var(--fg-1)" }}>
          PREDICTION RATING
        </div>
        <div className="mono" style={{ fontSize: 10, color: avg >= 7 ? "var(--green)" : avg >= 5.5 ? "var(--amber)" : "var(--red)" }}>
          {avg.toFixed(1)}/10
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, minmax(0, 1fr))", gap: 6 }}>
        {ratings.map((r) => (
          <div key={r.label} title={r.note}>
            <div className="mono dim" style={{ fontSize: 8, letterSpacing: "0.08em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {r.label}
            </div>
            <div style={{ height: 4, background: "var(--bg-3)", borderRadius: 99, marginTop: 4, overflow: "hidden" }}>
              <div style={{
                width: `${r.score * 10}%`,
                height: "100%",
                background: r.score >= 7 ? "var(--green)" : r.score >= 5.5 ? "var(--amber)" : "var(--red)",
              }} />
            </div>
            <div className="mono" style={{ fontSize: 10, marginTop: 3 }}>{r.score.toFixed(1)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function rateSlip(slip: SoccerBetslip): { label: string; score: number; note: string }[] {
  const guru = slip.ai_guru;
  const confidence = clamp(slip.confidence * 10, 1, 10);
  const safety = clamp(slip.safety_score / 10, 1, 10);
  const edgeScore = slip.edge == null
    ? clamp((1 / slip.minimum_acceptable_decimal) * 12, 3, 6.5)
    : clamp(5 + slip.edge * 35, 1, 10);
  const evidence = clamp(
    5.2
      + Math.min(1.4, slip.legs.length * 0.25)
      + (guru?.key_insights?.length ? 1.0 : 0)
      + (slip.book_decimal_odds ? 0.9 : -0.8),
    1,
    10,
  );
  const form = clamp(
    5.0
      + (guru ? 1.2 : 0)
      + Math.max(-1.2, Math.min(1.2, ((guru?.confidence_multiplier ?? 1) - 1) * 4))
      + (confidence - 5) * 0.25,
    1,
    10,
  );
  const external = clamp(
    6.0
      + (guru ? 0.8 : -0.5)
      - slip.risk_flags.length * 0.45
      - (slip.scout_only ? 1.0 : 0),
    1,
    10,
  );
  return [
    { label: "HISTORICAL", score: evidence, note: "Model support, data depth, Guru match context, and whether a real book price exists." },
    { label: "FORM", score: form, note: "Current team form proxy from AI Guru confidence and model conviction." },
    { label: "EXTERNAL", score: external, note: "Injuries, rest, venue, market availability, and known warning flags." },
    { label: "MARKET EDGE", score: edgeScore, note: "How much the model price beats the available or required book price." },
    { label: "MODEL TRUST", score: (confidence * 0.55 + safety * 0.45), note: "Combined confidence, safety score, and downside risk." },
  ];
}

function clamp(x: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, x));
}

function GuruBlock({ slip }: { slip: SoccerBetslip }) {
  const guru = slip.ai_guru;
  if (!guru) {
    return (
      <div className="mono dim" style={{
        fontSize: 9,
        marginBottom: 8,
        border: "1px solid var(--border-dim)",
        borderRadius: 3,
        padding: "6px 7px",
        background: "rgba(255,255,255,0.015)",
      }}>
        AI GURU: no comment attached for this slip yet. Check SOCCER_AI_GURU_ENABLED and OPENAI_API_KEY, or use a same-match slip.
      </div>
    );
  }
  const adjustment = [
    guru.home_adjustment,
    guru.draw_adjustment,
    guru.away_adjustment,
  ].map((x) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`).join(" / ");
  return (
    <div style={{
      border: "1px solid rgba(216,100,255,0.35)",
      background: "rgba(216,100,255,0.075)",
      borderRadius: 3,
      padding: "7px 8px",
      marginBottom: 8,
    }}>
      <div className="mono" style={{
        color: "var(--magenta)",
        fontSize: 9,
        letterSpacing: "0.14em",
        marginBottom: 5,
      }}>
        AI GURU · {guru.model} · H/D/A {adjustment}
      </div>
      {guru.key_insights.slice(0, 3).map((insight) => (
        <div key={insight} className="mono" style={{ fontSize: 9, color: "var(--fg-1)", marginBottom: 3 }}>
          {insight}
        </div>
      ))}
      {guru.rationale ? (
        <div className="mono dim" style={{ fontSize: 9, lineHeight: 1.45, marginTop: 5 }}>
          {guru.rationale}
        </div>
      ) : null}
    </div>
  );
}

function RecordBetWidget({
  slip,
  gate,
  manualOdds,
  onRecorded,
}: {
  slip: SoccerBetslip;
  gate: GateResult;
  manualOdds: string;
  onRecorded?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ id: number; status: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [bookmaker, setBookmaker] = useState(gate.source === "live" ? "" : "bet365");
  const [confirmingReview, setConfirmingReview] = useState(false);
  const [guardrailBlock, setGuardrailBlock] = useState<SoccerGuardrailReport | null>(null);
  const [forceUnlock, setForceUnlock] = useState(false);
  const [lineupConfirmed, setLineupConfirmed] = useState(false);

  const canRecord = gate.status === "BETTABLE" || gate.status === "REVIEW";
  if (!canRecord) {
    return null;
  }

  const placedOdds = gate.availableOdds;
  const stake = gate.stake;
  const sourceForApi: SoccerBetSource = gate.source === "live" ? "live" : gate.source === "pasted" ? "pasted" : "manual";
  const reviewNeedsNote = gate.status === "REVIEW";
  const noteOk = !reviewNeedsNote || notes.trim().length > 0;
  const recordEnabled = !busy && !done && placedOdds != null && placedOdds > 1 && stake > 0 && noteOk;
  const ev = placedOdds != null
    ? slip.fair_probability * (placedOdds - 1) * stake - (1 - slip.fair_probability) * stake
    : null;

  async function record() {
    if (placedOdds == null) {
      setErr("No price available — paste book odds first.");
      return;
    }
    if (reviewNeedsNote && notes.trim().length === 0) {
      setConfirmingReview(true);
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const legs: SoccerBetLegRecord[] = slip.legs.map((l) => ({
        kind: l.kind,
        label: l.label ?? null,
        params: l.params ?? {},
      }));
      const checks: SoccerBetQualificationCheck[] = gate.checks.map((c) => ({
        label: c.label,
        pass: c.pass,
        detail: c.detail,
        severity: c.severity ?? null,
      }));
      const sameGame = slip.legs.length > 1;
      const bookDecimal = sourceForApi === "live" || sourceForApi === "pasted" ? placedOdds : null;
      const payload: RecordSoccerBetPayload = {
        fixture_id: slip.fixture_id,
        legs,
        model_probability: slip.fair_probability,
        fair_decimal_odds: slip.fair_decimal_odds,
        placed_decimal_odds: placedOdds,
        stake_usd: Math.round(stake * 100) / 100,
        qualification_status: gate.status,
        qualification_checks: checks,
        source: sourceForApi,
        slip_id: slip.slip_id,
        slip_type: slip.slip_type,
        match_label: slip.match_label,
        title: slip.title,
        edge: gate.edge ?? slip.edge ?? null,
        kelly_fraction: slip.kelly_fraction ?? null,
        expected_value_usd: ev,
        bookmaker: bookmaker.trim() || null,
        notes: notes.trim() || null,
        same_game: sameGame,
        lineup_confirmed: lineupConfirmed,
        book_decimal_odds: bookDecimal,
        force: forceUnlock,
      };
      const resp = await api.soccerRecordBet(payload);
      setDone({ id: resp.bet.id, status: resp.bet.status });
      setGuardrailBlock(null);
      onRecorded?.();
    } catch (e: unknown) {
      if (e instanceof SoccerGuardrailsBlockedError) {
        setGuardrailBlock(e.report);
        setForceUnlock(false);
        setErr(null);
      } else {
        setErr(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="mono" style={{
        border: "1px solid var(--green-dim)",
        background: "rgba(25,195,125,0.10)",
        borderRadius: 3,
        padding: "7px 8px",
        marginBottom: 8,
        color: "var(--green)",
        fontSize: 10,
      }}>
        ✓ Bet #{done.id} recorded as {done.status.toUpperCase()}.
        Track it in the BET JOURNAL panel — auto-grades on /resolve, manual mark won/lost otherwise.
      </div>
    );
  }

  const accent = gate.status === "BETTABLE" ? "var(--green)" : "var(--amber)";
  const accentDim = gate.status === "BETTABLE" ? "var(--green-dim)" : "var(--amber-dim)";
  return (
    <div style={{
      border: `1px solid ${accentDim}`,
      borderRadius: 3,
      padding: "7px 8px",
      marginBottom: 8,
      background: gate.status === "BETTABLE" ? "rgba(25,195,125,0.06)" : "rgba(255,196,80,0.06)",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <div className="mono" style={{ color: accent, fontSize: 9, letterSpacing: "0.14em" }}>
          RECORD BET TO JOURNAL
        </div>
        <div className="mono dim" style={{ fontSize: 9 }}>
          {placedOdds != null
            ? `${money(stake)} @ ${placedOdds.toFixed(2)} · ${gate.source.toUpperCase()}${ev != null ? ` · EV ${money(ev)}` : ""}`
            : "no price yet"}
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 6, alignItems: "center" }}>
        <label className="mono dim" style={{ fontSize: 9 }}>book</label>
        <input
          value={bookmaker}
          onChange={(e) => setBookmaker(e.target.value)}
          placeholder="bet365"
          style={{
            background: "var(--bg-3)", color: "var(--fg)",
            border: "1px solid var(--border)", padding: "3px 6px",
            borderRadius: 2, fontFamily: "var(--mono)", fontSize: 11, width: 110,
          }}
        />
        <label className="mono dim" style={{ fontSize: 9 }}>note{reviewNeedsNote ? " (required for REVIEW)" : ""}</label>
        <input
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder={reviewNeedsNote ? "why you're betting anyway" : "optional"}
          style={{
            background: "var(--bg-3)", color: "var(--fg)",
            border: `1px solid ${reviewNeedsNote && notes.trim().length === 0 ? "var(--amber-dim)" : "var(--border)"}`,
            padding: "3px 6px", borderRadius: 2,
            fontFamily: "var(--mono)", fontSize: 11, flex: "1 1 180px",
            minWidth: 120,
          }}
        />
        <button
          onClick={record}
          disabled={!recordEnabled}
          style={{
            background: gate.status === "BETTABLE" ? "rgba(25,195,125,0.20)" : "rgba(255,196,80,0.18)",
            color: accent,
            border: `1px solid ${accentDim}`,
            padding: "4px 12px",
            borderRadius: 2,
            fontFamily: "var(--mono)",
            fontSize: 11,
            letterSpacing: "0.1em",
            cursor: recordEnabled ? "pointer" : "not-allowed",
            opacity: recordEnabled ? 1 : 0.5,
          }}
          title={
            placedOdds == null
              ? "Paste/verify book odds first"
              : reviewNeedsNote && notes.trim().length === 0
                ? "Add a note acknowledging the warning"
                : "Persist this bet to the journal"
          }
        >{busy ? "RECORDING..." : forceUnlock && guardrailBlock ? "FORCE RECORD" : gate.status === "REVIEW" ? "RECORD REVIEW BET" : "RECORD BET"}</button>
      </div>
      {guardrailBlock ? (
        <GuardrailBlockStrip
          report={guardrailBlock}
          force={forceUnlock}
          onForceChange={setForceUnlock}
          lineupConfirmed={lineupConfirmed}
          onLineupChange={setLineupConfirmed}
        />
      ) : null}
      {confirmingReview && reviewNeedsNote && notes.trim().length === 0 ? (
        <div className="mono" style={{ color: "var(--amber)", fontSize: 9, marginTop: 6 }}>
          REVIEW bets need a note explaining why you're betting despite the warning.
        </div>
      ) : null}
      {err ? (
        <div className="mono" style={{ color: "var(--red)", fontSize: 9, marginTop: 6 }}>
          {err}
        </div>
      ) : null}
    </div>
  );
}

function GuardrailBlockStrip({
  report,
  force,
  onForceChange,
  lineupConfirmed,
  onLineupChange,
}: {
  report: SoccerGuardrailReport;
  force: boolean;
  onForceChange: (v: boolean) => void;
  lineupConfirmed: boolean;
  onLineupChange: (v: boolean) => void;
}) {
  const failing = report.checks.filter((c) => !c.passed);
  const hard = failing.filter((c) => c.severity === "hard");
  const warns = failing.filter((c) => c.severity !== "hard");
  const hasLineupBlock = hard.some((c) => c.rule === "player_prop_lineup");
  return (
    <div style={{
      marginTop: 8,
      border: "1px solid var(--red-dim)",
      background: "rgba(220,80,80,0.08)",
      borderRadius: 3,
      padding: "7px 8px",
    }}>
      <div className="mono" style={{
        color: "var(--red)", fontSize: 9, letterSpacing: "0.14em", marginBottom: 5,
      }}>
        SERVER GUARDRAILS BLOCKED THIS BET — {report.hard_fail_count} hard fail{report.hard_fail_count === 1 ? "" : "s"}
        {report.warn_count ? ` · ${report.warn_count} warning${report.warn_count === 1 ? "" : "s"}` : ""}
      </div>
      <div style={{ display: "grid", gap: 4 }}>
        {hard.map((c) => (
          <div key={c.rule} className="mono" style={{ fontSize: 10, color: "var(--red)" }}>
            ✗ <span style={{ color: "var(--fg-1)" }}>{c.label}</span> — {c.detail}
          </div>
        ))}
        {warns.map((c) => (
          <div key={c.rule} className="mono" style={{ fontSize: 10, color: "var(--amber)" }}>
            ⚠ <span style={{ color: "var(--fg-1)" }}>{c.label}</span> — {c.detail}
          </div>
        ))}
      </div>
      {hasLineupBlock ? (
        <label className="mono" style={{
          display: "flex", alignItems: "center", gap: 6,
          fontSize: 10, marginTop: 7, color: "var(--fg-1)", cursor: "pointer",
        }}>
          <input
            type="checkbox"
            checked={lineupConfirmed}
            onChange={(e) => onLineupChange(e.target.checked)}
          />
          Confirm starting XI is locked (clears player-prop block)
        </label>
      ) : null}
      <label className="mono" style={{
        display: "flex", alignItems: "center", gap: 6,
        fontSize: 10, marginTop: 7, color: "var(--red)", cursor: "pointer",
      }}>
        <input
          type="checkbox"
          checked={force}
          onChange={(e) => onForceChange(e.target.checked)}
        />
        FORCE RECORD ANYWAY — I accept these guardrail failures.
      </label>
      <div className="mono dim" style={{ fontSize: 9, marginTop: 4 }}>
        Hit RECORD BET again after toggling. Failing checks are persisted with the bet.
      </div>
    </div>
  );
}

function exportBetslips(slips: SoccerBetslip[], horizon: string) {
  if (slips.length === 0) return;
  const body = formatBetslipsText(slips, horizon);
  const blob = new Blob([body], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  a.href = url;
  a.download = `edge-soccer-betslips-${horizon.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${stamp}.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function printBetslips(slips: SoccerBetslip[], horizon: string) {
  if (slips.length === 0) return;
  const win = window.open("", "_blank", "width=980,height=760");
  if (!win) {
    exportBetslips(slips, horizon);
    return;
  }
  win.document.write(renderPrintHtml(slips, horizon));
  win.document.close();
  win.focus();
  setTimeout(() => win.print(), 250);
}

function formatBetslipsText(slips: SoccerBetslip[], horizon: string): string {
  const generated = new Date().toLocaleString();
  const totalStake = slips.reduce((sum, s) => sum + s.stake_usd, 0);
  const lines = [
    "EDGE SOCCER BETSLIPS",
    `Generated: ${generated}`,
    `Slate: ${horizon}`,
    `Slips: ${slips.length}`,
    `Total suggested stake: ${money(totalStake)}`,
    "",
  ];
  for (const [idx, slip] of slips.entries()) {
    const odds = slip.book_decimal_odds ?? slip.minimum_acceptable_decimal;
    const potentialReturn = slip.stake_usd * odds;
    const guru = slip.ai_guru;
    lines.push(
      `${idx + 1}. ${slip.title}`,
      `Match: ${slip.match_label}`,
      `Type: ${typeLabel(slip.slip_type)}`,
      `Risk: ${slip.risk_level.toUpperCase()} | Safety: ${slip.safety_score.toFixed(0)} | Confidence: ${pct(slip.confidence)}`,
      `Model: ${pct(slip.fair_probability)} | Fair odds: ${slip.fair_decimal_odds.toFixed(2)} | ${slip.book_decimal_odds ? "Book odds" : "Minimum book odds"}: ${odds.toFixed(2)}`,
      `Edge: ${slip.edge != null ? pct(slip.edge) : "price needed"} | Stake: ${slip.scout_only ? "VERIFY PRICE" : money(slip.stake_usd)} | Potential return: ${slip.scout_only ? "VERIFY PRICE" : money(potentialReturn)}`,
      "Legs:",
      ...slip.legs.map((leg) => `- ${leg.label}`),
    );
    if (guru) {
      lines.push(
        "AI Guru:",
        `- Model: ${guru.model}`,
        ...guru.key_insights.slice(0, 3).map((insight) => `- ${insight}`),
        ...(guru.rationale ? [`- ${guru.rationale}`] : []),
      );
    }
    if (slip.reasons.length) {
      lines.push("Why:", ...slip.reasons.slice(0, 4).map((r) => `- ${r}`));
    }
    if (slip.warnings.length) {
      lines.push("Warnings:", ...slip.warnings.slice(0, 4).map((w) => `- ${w}`));
    }
    lines.push("");
  }
  return lines.join("\n");
}

function renderPrintHtml(slips: SoccerBetslip[], horizon: string): string {
  const generated = new Date().toLocaleString();
  const totalStake = slips.reduce((sum, s) => sum + s.stake_usd, 0);
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>EDGE Soccer Betslips</title>
  <style>
    body { margin: 28px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: #111827; background: #fff; }
    h1 { margin: 0 0 6px; font-size: 22px; letter-spacing: 0.12em; }
    .meta { color: #4b5563; font-size: 12px; margin-bottom: 18px; }
    .slip { break-inside: avoid; border: 1px solid #d1d5db; border-radius: 6px; padding: 14px; margin: 0 0 14px; }
    .top { display: flex; justify-content: space-between; gap: 16px; border-bottom: 1px solid #e5e7eb; padding-bottom: 8px; margin-bottom: 10px; }
    .title { font-size: 15px; font-weight: 700; }
    .sub { color: #4b5563; font-size: 11px; margin-top: 3px; }
    .grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin: 10px 0; }
    .cell { border: 1px solid #e5e7eb; border-radius: 4px; padding: 7px; }
    .label { color: #6b7280; font-size: 9px; letter-spacing: 0.12em; text-transform: uppercase; }
    .value { font-size: 13px; margin-top: 3px; }
    .section { margin-top: 10px; }
    .section-title { color: #374151; font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase; margin-bottom: 4px; }
    ul { margin: 4px 0 0 18px; padding: 0; }
    li { margin-bottom: 3px; font-size: 11px; line-height: 1.4; }
    .guru { border-left: 3px solid #a855f7; padding-left: 9px; }
    @media print { body { margin: 16px; } .slip { page-break-inside: avoid; } }
  </style>
</head>
<body>
  <h1>EDGE SOCCER BETSLIPS</h1>
  <div class="meta">Generated: ${escapeHtml(generated)} | Slate: ${escapeHtml(horizon)} | Slips: ${slips.length} | Total suggested stake: ${escapeHtml(money(totalStake))}</div>
  ${slips.map((slip, idx) => renderSlipPrintHtml(slip, idx)).join("")}
</body>
</html>`;
}

function renderSlipPrintHtml(slip: SoccerBetslip, idx: number): string {
  const odds = slip.book_decimal_odds ?? slip.minimum_acceptable_decimal;
  const potentialReturn = slip.stake_usd * odds;
  const guru = slip.ai_guru;
  return `<section class="slip">
    <div class="top">
      <div>
        <div class="title">${idx + 1}. ${escapeHtml(slip.title)}</div>
        <div class="sub">${escapeHtml(slip.match_label)} | ${escapeHtml(typeLabel(slip.slip_type))}</div>
      </div>
      <div class="sub">${escapeHtml(slip.risk_level.toUpperCase())} | Safety ${slip.safety_score.toFixed(0)}</div>
    </div>
    <div class="grid">
      ${printCell("Model", pct(slip.fair_probability))}
      ${printCell("Fair", slip.fair_decimal_odds.toFixed(2))}
      ${printCell(slip.book_decimal_odds ? "Book" : "Min Book", odds.toFixed(2))}
      ${printCell("Stake", slip.scout_only ? "Verify price" : money(slip.stake_usd))}
      ${printCell("Return", slip.scout_only ? "Verify price" : money(potentialReturn))}
    </div>
    ${printList("Legs", slip.legs.map((l) => l.label))}
    ${guru ? `<div class="section guru">
      <div class="section-title">AI Guru - ${escapeHtml(guru.model)}</div>
      <ul>
        ${guru.key_insights.slice(0, 3).map((x) => `<li>${escapeHtml(x)}</li>`).join("")}
        ${guru.rationale ? `<li>${escapeHtml(guru.rationale)}</li>` : ""}
      </ul>
    </div>` : ""}
    ${printList("Why", slip.reasons.slice(0, 4))}
    ${printList("Warnings", slip.warnings.slice(0, 4))}
  </section>`;
}

function printCell(label: string, value: string): string {
  return `<div class="cell"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(value)}</div></div>`;
}

function printList(title: string, items: string[]): string {
  if (!items.length) return "";
  return `<div class="section"><div class="section-title">${escapeHtml(title)}</div><ul>${items.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}</ul></div>`;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
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

function Mini({ label, value, tone }: { label: string; value: string; tone?: "green" | "cyan" | "dim" }) {
  const color =
    tone === "green" ? "var(--green)" :
    tone === "cyan" ? "var(--cyan)" :
    tone === "dim" ? "var(--fg-2)" : "var(--fg-0)";
  return (
    <div>
      <div className="mono dim" style={{ fontSize: 9, letterSpacing: "0.14em" }}>{label}</div>
      <div className="mono" style={{ color, fontSize: 14 }}>{value}</div>
    </div>
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
