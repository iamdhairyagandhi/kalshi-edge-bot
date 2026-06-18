/**
 * Phase 6 — Bet365 Paste Workflow.
 *
 * The user pastes a Bet365 betslip into the textarea. The backend parses
 * the legs (1.65 vs 11/4 vs +150), matches them against an optional set of
 * model legs (the slip currently selected in the Bet Builder), and re-prices
 * the matched parlay through the simulator + correlation engine.
 *
 * The panel intentionally avoids any external API calls — everything is
 * local: parse → match → reprice — so it works on a restricted network.
 */

import { useEffect, useMemo, useState } from "react";
import {
  Bet365PasteResponse,
  SoccerBetLeg,
  SoccerFixture,
  SoccerMatchSummary,
  api,
} from "../../api/client";

type Props = {
  match: SoccerMatchSummary | null;
  fixtures: SoccerFixture[];
  presetLegs: SoccerBetLeg[];
};

export default function Bet365PastePanel({ match, fixtures, presetLegs }: Props) {
  const fixtureId = match?.fixture.fixture_id ?? null;
  const [text, setText] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<Bet365PasteResponse | null>(null);
  const [useBuilderLegs, setUseBuilderLegs] = useState(true);
  const [sameGame, setSameGame] = useState(true);
  const [lineupConfirmed, setLineupConfirmed] = useState(false);

  useEffect(() => {
    // Reset result if the user picks a different fixture or new preset legs.
    setResult(null);
  }, [fixtureId, presetLegs]);

  const sampleSlip = useMemo(
    () => sampleSlipFor(match),
    [match],
  );

  async function reprice() {
    if (!text.trim()) {
      setErr("Paste a Bet365 slip first.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const r = await api.soccerPasteBet365({
        slip_text: text,
        fixture_id: fixtureId ?? undefined,
        model_legs: useBuilderLegs ? presetLegs : undefined,
        same_game: sameGame,
        lineup_confirmed: lineupConfirmed,
      });
      setResult(r);
    } catch (e: any) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; BET365 PASTE & REPRICE</span>
        <span className="mono dim">
          {fixtureId ? `fixture ${fixtureId}` : "no fixture selected"}
        </span>
      </div>
      <div className="panel-body" style={{ display: "grid", gridTemplateColumns: "1.1fr 1fr", gap: 14 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
          <div className="mono dim" style={{ fontSize: 10, letterSpacing: "0.14em" }}>
            BETSLIP TEXT
          </div>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={"Paste your Bet365 slip here.\nExample:\n\n" + sampleSlip}
            spellCheck={false}
            rows={12}
            style={{
              background: "var(--bg-3)",
              color: "var(--fg)",
              border: "1px solid var(--bg-4)",
              padding: "8px 10px",
              fontFamily: "var(--mono)",
              fontSize: 11,
              borderRadius: 2,
              resize: "vertical",
              minHeight: 180,
              width: "100%",
            }}
          />
          <div style={{ display: "flex", flexWrap: "wrap", gap: 14, fontSize: 10 }}>
            <label className="mono dim" style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <input
                type="checkbox"
                checked={useBuilderLegs}
                onChange={(e) => setUseBuilderLegs(e.target.checked)}
              />
              match against builder legs ({presetLegs.length})
            </label>
            <label className="mono dim" style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <input
                type="checkbox"
                checked={sameGame}
                onChange={(e) => setSameGame(e.target.checked)}
              />
              same-game parlay
            </label>
            <label className="mono dim" style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <input
                type="checkbox"
                checked={lineupConfirmed}
                onChange={(e) => setLineupConfirmed(e.target.checked)}
              />
              lineups confirmed
            </label>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button
              disabled={busy}
              onClick={reprice}
              style={{
                background: "rgba(25,195,125,0.18)",
                color: "var(--green)",
                border: "1px solid var(--green-dim)",
                padding: "4px 14px",
                borderRadius: 2,
                fontFamily: "var(--mono)",
                fontSize: 11,
                letterSpacing: "0.12em",
                cursor: busy ? "not-allowed" : "pointer",
              }}
            >
              {busy ? "PARSING…" : "PARSE + REPRICE"}
            </button>
            <button
              onClick={() => { setText(""); setResult(null); setErr(null); }}
              style={{
                background: "var(--bg-3)",
                color: "var(--fg-2)",
                border: "1px solid var(--bg-4)",
                padding: "4px 10px",
                borderRadius: 2,
                fontFamily: "var(--mono)",
                fontSize: 11,
                cursor: "pointer",
              }}
            >
              CLEAR
            </button>
            {err ? <span className="mono" style={{ color: "var(--red)", fontSize: 11 }}>{err}</span> : null}
          </div>
        </div>
        <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 10 }}>
          {!result ? (
            <div className="empty">paste a Bet365 slip and click PARSE + REPRICE</div>
          ) : (
            <ResultBlock result={result} presetLegs={presetLegs} fixtures={fixtures} />
          )}
        </div>
      </div>
    </div>
  );
}

function ResultBlock({
  result,
  presetLegs,
  fixtures,
}: {
  result: Bet365PasteResponse;
  presetLegs: SoccerBetLeg[];
  fixtures: SoccerFixture[];
}) {
  const parsed = result.parsed;
  return (
    <>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
        <Mini label="LEGS" value={String(parsed.legs.length)} />
        <Mini label="TYPE" value={parsed.slip_type.toUpperCase()} />
        <Mini
          label="COMBINED"
          value={parsed.combined_decimal_odds ? parsed.combined_decimal_odds.toFixed(2) : "—"}
        />
        <Mini label="STAKE" value={parsed.stake != null ? `$${parsed.stake.toFixed(2)}` : "—"} />
        <Mini label="RETURNS" value={parsed.returns != null ? `$${parsed.returns.toFixed(2)}` : "—"} />
        {result.model_legs_count > 0 ? (
          <Mini
            label="MATCH CONF"
            value={`${(result.overall_match_confidence * 100).toFixed(0)}%`}
            tone={result.overall_match_confidence >= 0.7 ? "green" : "amber"}
          />
        ) : null}
      </div>

      {parsed.notes.length > 0 ? (
        <div className="mono dim" style={{ fontSize: 10 }}>
          {parsed.notes.join("  ·  ")}
        </div>
      ) : null}

      <div>
        <div className="mono dim" style={{ fontSize: 10, letterSpacing: "0.14em", marginBottom: 4 }}>
          DETECTED LEGS
        </div>
        <table className="tight" style={{ fontSize: 11 }}>
          <thead>
            <tr>
              <th>#</th>
              <th>SELECTION</th>
              <th>MARKET</th>
              <th className="right">ODDS</th>
              <th>KIND</th>
              {result.model_legs_count > 0 ? <th>MATCH</th> : null}
            </tr>
          </thead>
          <tbody>
            {parsed.legs.map((leg, i) => {
              const match = result.matches.find((m) => m.parsed_index === i);
              const matched =
                match && match.matched_model_index != null
                  ? presetLegs[match.matched_model_index]
                  : null;
              return (
                <tr key={i}>
                  <td className="mono dim">{i + 1}</td>
                  <td>{leg.selection}</td>
                  <td className="mono dim">{leg.market || "—"}</td>
                  <td className="right mono">{leg.decimal_odds?.toFixed(2) ?? "—"}</td>
                  <td className="mono dim">{leg.kind || "?"}</td>
                  {result.model_legs_count > 0 ? (
                    <td className="mono" style={{ fontSize: 10 }}>
                      {matched ? (
                        <span style={{ color: (match!.confidence >= 0.7) ? "var(--green)" : "var(--amber)" }}>
                          ✓ {matched.label || matched.kind} ({(match!.confidence * 100).toFixed(0)}%)
                        </span>
                      ) : (
                        <span style={{ color: "var(--red)" }}>✕ no match</span>
                      )}
                    </td>
                  ) : null}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {parsed.unrecognised_lines.length > 0 ? (
        <div className="mono" style={{ color: "var(--amber)", fontSize: 10 }}>
          Unrecognised: {parsed.unrecognised_lines.join("  ·  ")}
        </div>
      ) : null}

      {result.gate_blocking_issues.length > 0 ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          {result.gate_blocking_issues.map((iss, i) => (
            <div key={i} className="mono" style={{ color: "var(--red)", fontSize: 10 }}>
              ✕ {iss}
            </div>
          ))}
        </div>
      ) : null}

      {result.reprice ? (
        <RepriceBlock reprice={result.reprice} />
      ) : result.reprice_error ? (
        <div className="mono" style={{ color: "var(--amber)", fontSize: 10 }}>
          Reprice unavailable: {result.reprice_error}
        </div>
      ) : null}
    </>
  );
}

function RepriceBlock({ reprice }: { reprice: Bet365PasteResponse["reprice"] }) {
  if (!reprice) return null;
  const recColor =
    reprice.recommendation === "bet"
      ? "var(--green)"
      : reprice.recommendation === "thin_edge" || reprice.recommendation === "risky_edge"
      ? "var(--amber)"
      : "var(--red)";
  return (
    <div style={{
      border: "1px solid var(--bg-4)",
      borderRadius: 2,
      padding: "8px 10px",
      background: "rgba(80,180,255,0.04)",
      display: "flex",
      flexDirection: "column",
      gap: 8,
    }}>
      <div className="mono dim" style={{ fontSize: 10, letterSpacing: "0.14em" }}>
        REPRICE THROUGH MODEL
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
        <Mini
          label="FAIR PROB"
          value={`${(reprice.fair_probability * 100).toFixed(2)}%`}
        />
        <Mini
          label="FAIR ODDS"
          value={reprice.fair_decimal_odds.toFixed(2)}
        />
        <Mini
          label="BOOK ODDS"
          value={reprice.book_decimal_odds != null ? reprice.book_decimal_odds.toFixed(2) : "—"}
        />
        <Mini
          label="EDGE"
          value={reprice.edge != null ? `${(reprice.edge * 100).toFixed(2)}%` : "—"}
          tone={(reprice.edge ?? 0) > 0 ? "green" : "red"}
        />
        <Mini
          label="CORR TAX"
          value={reprice.correlation_tax != null ? `${(reprice.correlation_tax * 100).toFixed(2)}%` : "—"}
          tone={(reprice.correlation_tax ?? 0) >= 0 ? "amber" : "green"}
        />
        <Mini
          label="SAFETY"
          value={`${reprice.safety_score.toFixed(0)}/100`}
        />
      </div>
      <div style={{
        padding: "5px 10px",
        border: `1px solid ${recColor}`,
        borderRadius: 2,
        fontFamily: "var(--mono)",
        fontSize: 12,
        color: recColor,
        letterSpacing: "0.15em",
        textAlign: "center",
      }}>
        {reprice.recommendation.toUpperCase()}
      </div>
      {reprice.parlay_rules.length > 0 ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          {reprice.parlay_rules.map((rule) => {
            const color = rule.passed
              ? "var(--green)"
              : rule.severity === "hard"
              ? "var(--red)"
              : "var(--amber)";
            const icon = rule.passed ? "✓" : rule.severity === "hard" ? "✕" : "!";
            return (
              <div
                key={rule.rule}
                className="mono"
                style={{
                  fontSize: 10,
                  color,
                  borderLeft: `2px solid ${color}`,
                  paddingLeft: 6,
                  lineHeight: 1.3,
                }}
                title={rule.rule}
              >
                {icon} {rule.detail}
              </div>
            );
          })}
        </div>
      ) : null}
      {reprice.failure_modes.length > 0 ? (
        <div>
          <div className="mono dim" style={{ fontSize: 10, letterSpacing: "0.14em", marginBottom: 2 }}>
            WHY THIS PARLAY COULD LOSE
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {reprice.failure_modes.map((mode, i) => (
              <div key={i} className="mono" style={{ fontSize: 10, color: "var(--fg-2)" }}>
                <span style={{ color: "var(--amber)", marginRight: 6 }}>
                  {(mode.share * 100).toFixed(0)}%
                </span>
                {mode.why}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Mini({ label, value, tone }: { label: string; value: string; tone?: "green" | "amber" | "red" | "cyan" }) {
  const color =
    tone === "green" ? "var(--green)" :
    tone === "amber" ? "var(--amber)" :
    tone === "red" ? "var(--red)" :
    tone === "cyan" ? "var(--cyan)" :
    "var(--fg)";
  return (
    <div>
      <div className="mono dim" style={{ fontSize: 9, letterSpacing: "0.14em" }}>{label}</div>
      <div className="mono" style={{ color, fontSize: 13 }}>{value}</div>
    </div>
  );
}

function sampleSlipFor(match: SoccerMatchSummary | null): string {
  const home = match?.fixture.home_team_name ?? "Manchester City";
  const lines = [
    "Same Game Multi (3)",
    "",
    `${home} to Win`,
    "Match Result",
    "2.10",
    "",
    "Over 2.5",
    "Total Goals",
    "1.80",
    "",
    "Both Teams To Score - Yes",
    "1.75",
    "",
    "Stake: $20.00   Returns: $130.00   Odds: 6.50",
  ];
  return lines.join("\n");
}
