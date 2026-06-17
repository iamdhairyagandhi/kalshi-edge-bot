import React, { useState } from "react";
import { api, SoccerFixture } from "../../api/client";

type Props = {
  fixtures: SoccerFixture[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  empty: boolean;
  onSeedDemo: () => void;
  onFitReal: () => void;
  onFitXg: () => void;
  onLoadOddsFixtures: () => void;
  fitting: boolean;
  fitInfo: string | null;
  seedError: string | null;
  onResolved?: () => void;
};

function fmtPct(p: number | null | undefined): string {
  if (p == null) return "—";
  return `${(p * 100).toFixed(1)}%`;
}

function fmtTime(unix: number): string {
  const d = new Date(unix * 1000);
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export default function FixturesPanel({
  fixtures, selectedId, onSelect, empty, onSeedDemo, onFitReal, onFitXg, onLoadOddsFixtures, fitting, fitInfo, seedError, onResolved,
}: Props) {
  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; FIXTURES</span>
        <span className="mono dim">
          {fitInfo ? <span style={{ color: "var(--green)" }}>{fitInfo} · </span> : null}
          {fixtures.length}
        </span>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        {empty ? (
          <div className="empty" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            <span>no fixtures loaded yet</span>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button
                disabled={fitting}
                onClick={onFitReal}
                style={{
                  background: "rgba(80,180,255,0.18)", color: "var(--cyan)",
                  border: "1px solid rgba(80,180,255,0.45)",
                  padding: "4px 12px", borderRadius: 2, fontFamily: "var(--mono)",
                  fontSize: 11, cursor: fitting ? "wait" : "pointer", letterSpacing: "0.1em",
                  opacity: fitting ? 0.5 : 1,
                }}
              >{fitting ? "FETCHING…" : "FIT REAL DATA (WC18/WC22/EURO20/EURO24)"}</button>
              <button
                disabled={fitting}
                onClick={onFitXg}
                style={{
                  background: "rgba(180,120,255,0.18)", color: "#cda5ff",
                  border: "1px solid rgba(180,120,255,0.45)",
                  padding: "4px 12px", borderRadius: 2, fontFamily: "var(--mono)",
                  fontSize: 11, cursor: fitting ? "wait" : "pointer", letterSpacing: "0.1em",
                  opacity: fitting ? 0.5 : 1,
                }}
                title="Train shot-level xG model on StatsBomb open-data and refit Dixon-Coles with xG-derived targets (lower variance than raw goals)."
              >{fitting ? "TRAINING…" : "FIT xG MODEL"}</button>
              <button
                disabled={fitting}
                onClick={onLoadOddsFixtures}
                style={{
                  background: "rgba(255,180,40,0.18)", color: "var(--amber)",
                  border: "1px solid rgba(255,180,40,0.45)",
                  padding: "4px 12px", borderRadius: 2, fontFamily: "var(--mono)",
                  fontSize: 11, cursor: fitting ? "wait" : "pointer", letterSpacing: "0.1em",
                  opacity: fitting ? 0.5 : 1,
                }}
              >LOAD REAL FIXTURES</button>
              <button
                disabled={fitting}
                onClick={onSeedDemo}
                style={{
                  background: "rgba(25,195,125,0.18)", color: "var(--green)",
                  border: "1px solid var(--green-dim)",
                  padding: "4px 12px", borderRadius: 2, fontFamily: "var(--mono)",
                  fontSize: 11, cursor: fitting ? "wait" : "pointer", letterSpacing: "0.1em",
                  opacity: fitting ? 0.5 : 1,
                }}
              >SEED DEMO MODEL</button>
            </div>
            {seedError ? <span className="mono" style={{ color: "var(--red)", fontSize: 11 }}>{seedError}</span> : null}
            <span className="dim" style={{ fontSize: 11 }}>
              <strong>FIT REAL DATA</strong> pulls 230 matches from StatsBomb's open-data
              (WC 2018, WC 2022, Euro 2020, Euro 2024) and refits Dixon-Coles + Elo on real results.
              It does not create fixtures. <strong>FIT xG MODEL</strong> additionally trains a
              shot-level Expected-Goals model on the same dataset and uses xG totals (instead of
              raw goals) as the DC fit target — far less noisy on small samples.
              <strong> LOAD REAL FIXTURES</strong> uses The Odds API and only
              inserts events whose teams match the fitted model. <strong>SEED DEMO</strong> is synthetic.
            </span>
          </div>
        ) : (
          <div style={{ display: "grid", gap: 6, padding: 8 }}>
            {fixtures.map((f) => {
              const sel = selectedId === f.fixture_id;
              const home = f.home_team_name || f.home_team_id;
              const away = f.away_team_name || f.away_team_id;
              return (
                <div key={f.fixture_id}>
                  <button
                    onClick={() => onSelect(f.fixture_id)}
                    style={{
                      width: "100%",
                      textAlign: "left",
                      background: sel ? "rgba(80,180,255,0.13)" : "rgba(255,255,255,0.02)",
                      border: `1px solid ${sel ? "var(--cyan-dim)" : "var(--border)"}`,
                      borderRadius: 4,
                      padding: "8px 9px",
                      cursor: "pointer",
                      color: "var(--fg-0)",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                      <div className="mono dim" style={{ fontSize: 10 }}>{fmtTime(f.kickoff_unix)}</div>
                      <div className="mono" style={{ fontSize: 10, color: sel ? "var(--cyan)" : "var(--fg-2)" }}>
                        {f.competition?.replace("soccer_", "") || "fixture"}
                      </div>
                    </div>
                    <div className="mono" style={{ fontSize: 14, marginTop: 5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {home} <span className="dim">vs</span> {away}
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 4, marginTop: 8 }}>
                      <Chip label="H" value={fmtPct(f.home_win)} hot={sel} />
                      <Chip label="D" value={fmtPct(f.draw)} />
                      <Chip label="A" value={fmtPct(f.away_win)} />
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4, marginTop: 4 }}>
                      <Chip label="O2.5" value={fmtPct(f.over_2_5)} muted />
                      <Chip label="BTTS" value={fmtPct(f.btts_yes)} muted />
                    </div>
                  </button>
                  {sel ? (
                    <ResolveBar fixtureId={f.fixture_id} onResolved={onResolved} />
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function Chip({ label, value, hot, muted }: { label: string; value: string; hot?: boolean; muted?: boolean }) {
  return (
    <span
      className="mono"
      style={{
        display: "flex",
        justifyContent: "space-between",
        gap: 5,
        border: "1px solid var(--border)",
        background: hot ? "rgba(24,210,224,0.12)" : "var(--bg-3)",
        color: muted ? "var(--fg-2)" : "var(--fg-1)",
        padding: "3px 5px",
        borderRadius: 2,
        fontSize: 10,
      }}
    >
      <span className="dim">{label}</span>
      <span>{value}</span>
    </span>
  );
}

function ResolveBar({
  fixtureId, onResolved,
}: { fixtureId: string; onResolved?: () => void }) {
  const [open, setOpen] = useState(false);
  const [home, setHome] = useState("");
  const [away, setAway] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  async function resolve() {
    const hg = parseInt(home, 10);
    const ag = parseInt(away, 10);
    if (!Number.isFinite(hg) || !Number.isFinite(ag) || hg < 0 || ag < 0) {
      setStatus("enter non-negative integers");
      return;
    }
    setBusy(true);
    setStatus(null);
    try {
      const r = await api.soccerResolveFixture(fixtureId, { home_goals: hg, away_goals: ag });
      setStatus(
        `graded ${r.predictions_graded} preds, ${r.bet_builder_graded} parlays, ` +
        `skipped ${r.skipped_unsupported_market} stat markets · ` +
        `${r.calibration_records} resolved samples now feeding calibration`,
      );
      setHome("");
      setAway("");
      onResolved?.();
    } catch (e) {
      setStatus(`error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function snapshot() {
    setBusy(true);
    setStatus(null);
    try {
      const r = await api.soccerSnapshotClosingLine(fixtureId);
      if (!r.ok) {
        setStatus(`snapshot skipped: ${r.reason ?? "no_match"}`);
      } else {
        setStatus(`snapshot updated ${r.updated} prediction(s)`);
      }
    } catch (e) {
      setStatus(`error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 6, marginTop: 4 }}>
        <button
          onClick={() => setOpen(true)}
          className="mono"
          style={{
            background: "var(--bg-3)", color: "var(--fg-2)", border: "1px solid var(--border)",
            padding: "2px 8px", borderRadius: 2, fontSize: 10, cursor: "pointer",
            letterSpacing: "0.1em",
          }}
        >RESOLVE</button>
      </div>
    );
  }

  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: 4, padding: 8, marginTop: 4,
      display: "grid", gap: 6, background: "rgba(255,255,255,0.02)",
    }}>
      <div className="mono dim" style={{ fontSize: 10, letterSpacing: "0.1em" }}>
        FINAL SCORE · grades 1X2 / totals / BTTS / team_total / correct_score
      </div>
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <input
          inputMode="numeric"
          placeholder="H"
          value={home}
          onChange={(e) => setHome(e.target.value)}
          disabled={busy}
          style={inputStyle()}
        />
        <span className="mono dim">:</span>
        <input
          inputMode="numeric"
          placeholder="A"
          value={away}
          onChange={(e) => setAway(e.target.value)}
          disabled={busy}
          style={inputStyle()}
        />
        <button
          onClick={resolve}
          disabled={busy}
          className="mono"
          style={{
            background: "rgba(25,195,125,0.18)", color: "var(--green)",
            border: "1px solid var(--green-dim)",
            padding: "3px 10px", borderRadius: 2, fontSize: 10, cursor: busy ? "wait" : "pointer",
            letterSpacing: "0.1em",
          }}
        >{busy ? "…" : "GRADE"}</button>
        <button
          onClick={snapshot}
          disabled={busy}
          className="mono"
          style={{
            background: "var(--bg-3)", color: "var(--cyan)", border: "1px solid var(--cyan-dim)",
            padding: "3px 10px", borderRadius: 2, fontSize: 10, cursor: busy ? "wait" : "pointer",
            letterSpacing: "0.1em",
          }}
        >SNAPSHOT CL</button>
        <button
          onClick={() => { setOpen(false); setStatus(null); }}
          disabled={busy}
          className="mono"
          style={{
            background: "var(--bg-3)", color: "var(--fg-2)", border: "1px solid var(--border)",
            padding: "3px 8px", borderRadius: 2, fontSize: 10, cursor: "pointer",
          }}
        >×</button>
      </div>
      {status ? (
        <div className="mono" style={{ fontSize: 10, color: status.startsWith("error") ? "var(--red)" : "var(--fg-2)" }}>
          {status}
        </div>
      ) : null}
    </div>
  );
}

function inputStyle(): React.CSSProperties {
  return {
    width: 44, background: "var(--bg-3)", color: "var(--fg)",
    border: "1px solid var(--border)", padding: "3px 6px",
    borderRadius: 2, fontFamily: "var(--mono)", fontSize: 12, textAlign: "center",
  };
}
