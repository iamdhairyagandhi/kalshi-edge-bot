/**
 * Soccer Bet Journal panel — Phase 2.
 *
 * Lists bets the user actually placed (from RECORD BET on the slip card)
 * grouped by status. The header strip shows open exposure, settled W-L-P,
 * and net PnL across all settled/cashed-out bets. Inline buttons let the
 * user mark an open bet won/lost/pushed/voided/cashed-out without leaving
 * the page; the resolution loop also auto-grades open bets when the user
 * resolves the fixture.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  SoccerBet,
  SoccerBetCalibrationBucket,
  SoccerBetCalibrationCurvePoint,
  SoccerBetCalibrationSummary,
  SoccerBetClvBucket,
  SoccerBetClvSummary,
  SoccerBetExposure,
  SoccerBetListResponse,
  SoccerBetSettledSummary,
  SoccerBetStatus,
  UpdateSoccerBetPayload,
} from "../../api/client";

type Tab = "open" | "settled" | "voided" | "all";

type Props = {
  refreshKey?: number;
};

function pct(x: number | null | undefined, digits = 1): string {
  if (x == null || !Number.isFinite(x)) return "--";
  return `${(x * 100).toFixed(digits)}%`;
}

function money(x: number | null | undefined): string {
  if (x == null || !Number.isFinite(x)) return "--";
  return `${x < 0 ? "-" : ""}$${Math.abs(x).toFixed(2)}`;
}

function fmtTime(unix: number | null | undefined): string {
  if (!unix) return "--";
  const d = new Date(unix * 1000);
  return d.toLocaleString();
}

function statusColor(s: SoccerBetStatus): string {
  if (s === "open") return "var(--cyan)";
  if (s === "won") return "var(--green)";
  if (s === "lost") return "var(--red)";
  if (s === "pushed") return "var(--fg-1)";
  if (s === "void") return "var(--fg-2)";
  if (s === "cashed_out") return "var(--magenta)";
  return "var(--fg-1)";
}

function sourceColor(s: string): string {
  if (s === "live") return "var(--green)";
  if (s === "pasted") return "var(--cyan)";
  return "var(--fg-2)";
}

const TABS: { id: Tab; label: string; statuses: SoccerBetStatus[] | null }[] = [
  { id: "open",    label: "OPEN",    statuses: ["open"] },
  { id: "settled", label: "SETTLED", statuses: ["won", "lost", "pushed", "cashed_out"] },
  { id: "voided",  label: "VOIDED",  statuses: ["void"] },
  { id: "all",     label: "ALL",     statuses: null },
];

export default function BetJournalPanel({ refreshKey }: Props) {
  const [tab, setTab] = useState<Tab>("open");
  const [data, setData] = useState<SoccerBetListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.soccerListBets({ status: "all", limit: 200 });
      setData(resp);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 30000);
    return () => window.clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    if (refreshKey != null) {
      refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  const visible = useMemo(() => {
    if (!data) return [];
    const t = TABS.find((x) => x.id === tab);
    if (!t || t.statuses == null) return data.bets;
    const allowed = new Set<SoccerBetStatus>(t.statuses);
    return data.bets.filter((b) => allowed.has(b.status));
  }, [data, tab]);

  const counts = useMemo(() => {
    if (!data) return { open: 0, settled: 0, voided: 0, all: 0 };
    let open = 0, settled = 0, voided = 0;
    for (const b of data.bets) {
      if (b.status === "open") open += 1;
      else if (b.status === "void") voided += 1;
      else settled += 1;
    }
    return { open, settled, voided, all: data.bets.length };
  }, [data]);

  async function patchBet(id: number, payload: UpdateSoccerBetPayload) {
    setBusyId(id);
    try {
      await api.soccerUpdateBet(id, payload);
      await refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function snapshotClosing(
    id: number,
    closing_decimal: number,
    closing_source?: string,
  ) {
    setBusyId(id);
    try {
      await api.soccerSnapshotClosing(id, { closing_decimal, closing_source });
      await refresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; SOCCER BET JOURNAL</span>
        <span className="mono dim">
          {data
            ? `${data.bets.length} BETS · ${counts.open} OPEN · ${counts.settled} SETTLED`
            : loading
              ? "LOADING..."
              : "READY"}
        </span>
      </div>
      <div className="panel-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {error ? (
          <div className="mono" style={{ color: "var(--red)", fontSize: 11 }}>{error}</div>
        ) : null}

        <JournalHeaderStrip
          exposure={data?.exposure ?? null}
          settled={data?.settled ?? null}
        />

        <ClvSummaryStrip summary={data?.clv_summary ?? null} />

        <CalibrationSummaryStrip summary={data?.calibration_summary ?? null} />

        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              style={tabStyle(tab === t.id)}
            >{t.label} ({tabCount(counts, t.id)})</button>
          ))}
          <button
            onClick={refresh}
            disabled={loading}
            style={{
              marginLeft: "auto",
              background: "rgba(255,255,255,0.04)", color: "var(--fg-1)",
              border: "1px solid var(--border)",
              padding: "3px 10px",
              borderRadius: 2,
              fontFamily: "var(--mono)", fontSize: 10,
              letterSpacing: "0.1em",
              cursor: loading ? "wait" : "pointer",
            }}
          >{loading ? "..." : "REFRESH"}</button>
        </div>

        {!data ? (
          <div className="empty" style={{ minHeight: 80 }}>Loading bets…</div>
        ) : visible.length === 0 ? (
          <div className="empty" style={{ minHeight: 80, fontSize: 11 }}>
            No bets in this view yet. Place a {tab === "open" ? "BETTABLE" : ""} bet from the BETSLIP CREATOR.
          </div>
        ) : (
          <div style={{ display: "grid", gap: 6 }}>
            {visible.map((bet) => (
              <BetRow
                key={bet.id}
                bet={bet}
                busy={busyId === bet.id}
                onPatch={(payload) => patchBet(bet.id, payload)}
                onSnapshotClosing={(closing, src) => snapshotClosing(bet.id, closing, src)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function tabCount(counts: { open: number; settled: number; voided: number; all: number }, id: Tab): number {
  if (id === "open") return counts.open;
  if (id === "settled") return counts.settled;
  if (id === "voided") return counts.voided;
  return counts.all;
}

function tabStyle(active: boolean) {
  return {
    background: active ? "rgba(80,180,255,0.18)" : "rgba(255,255,255,0.04)",
    color: active ? "var(--cyan)" : "var(--fg-1)",
    border: `1px solid ${active ? "var(--cyan-dim)" : "var(--border)"}`,
    padding: "3px 10px",
    borderRadius: 2,
    fontFamily: "var(--mono)",
    fontSize: 10,
    letterSpacing: "0.12em",
    cursor: "pointer",
  } as const;
}

function JournalHeaderStrip({
  exposure,
  settled,
}: {
  exposure: SoccerBetExposure | null;
  settled: SoccerBetSettledSummary | null;
}) {
  const stake = exposure?.open_stake_usd ?? 0;
  const max = exposure?.open_max_return_usd ?? 0;
  const pnl = settled?.net_pnl_usd ?? 0;
  const wlp = settled
    ? `${settled.won}-${settled.lost}-${settled.pushed}${settled.cashed_out ? `+${settled.cashed_out}` : ""}`
    : "0-0-0";
  const pnlColor = pnl > 0 ? "var(--green)" : pnl < 0 ? "var(--red)" : "var(--fg-1)";
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
      gap: 8,
      border: "1px solid var(--border)",
      borderRadius: 4,
      padding: 8,
      background: "rgba(255,255,255,0.018)",
    }}>
      <Cell label="OPEN STAKE"   value={money(stake)} color="var(--cyan)" />
      <Cell label="MAX RETURN"   value={money(max)}   color="var(--fg-0)" />
      <Cell label="SETTLED W-L-P" value={wlp}         color="var(--fg-1)" />
      <Cell label="NET PNL"      value={money(pnl)}   color={pnlColor} />
      {exposure && exposure.by_match.length > 0 ? (
        <div style={{ gridColumn: "1 / -1" }}>
          <div className="mono dim" style={{ fontSize: 9, marginBottom: 4 }}>OPEN MATCH EXPOSURE</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
            {exposure.by_match.slice(0, 8).map((m) => (
              <span key={m.match_label} className="mono" style={{
                fontSize: 9, border: "1px solid var(--border)", borderRadius: 2,
                padding: "3px 6px", color: "var(--fg-1)", background: "var(--bg-3)",
              }}>{m.match_label}: {money(m.stake_usd)} ({m.n})</span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Cell({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div>
      <div className="mono dim" style={{ fontSize: 8, letterSpacing: "0.12em" }}>{label}</div>
      <div className="mono" style={{ fontSize: 14, color, marginTop: 2 }}>{value}</div>
    </div>
  );
}

function BetRow({
  bet,
  busy,
  onPatch,
  onSnapshotClosing,
}: {
  bet: SoccerBet;
  busy: boolean;
  onPatch: (payload: UpdateSoccerBetPayload) => void;
  onSnapshotClosing: (closing_decimal: number, closing_source?: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [cashOut, setCashOut] = useState("");
  const [closeOdds, setCloseOdds] = useState("");
  const [closeSource, setCloseSource] = useState("pinnacle");
  const odds = bet.placed_decimal_odds;
  const ev =
    bet.expected_value_usd ??
    (bet.stake_usd * (bet.model_probability * (odds - 1) - (1 - bet.model_probability)));
  const accent = statusColor(bet.status);
  const pnlColor = bet.pnl_usd == null
    ? "var(--fg-2)"
    : bet.pnl_usd > 0 ? "var(--green)" : bet.pnl_usd < 0 ? "var(--red)" : "var(--fg-1)";
  const isOpen = bet.status === "open";
  const hasClosing = bet.closing_decimal != null;
  const clvColor = bet.clv_pct == null
    ? "var(--fg-2)"
    : bet.clv_pct > 0.005 ? "var(--green)"
      : bet.clv_pct < -0.005 ? "var(--red)" : "var(--fg-1)";
  return (
    <div style={{
      border: `1px solid ${isOpen ? "var(--border)" : "var(--border-dim)"}`,
      borderRadius: 3,
      background: isOpen ? "rgba(80,180,255,0.04)" : "rgba(255,255,255,0.012)",
      padding: "7px 8px",
    }}>
      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr auto auto auto", gap: 8, alignItems: "center" }}>
        <span className="mono" style={{
          fontSize: 9, letterSpacing: "0.14em", color: accent,
          border: `1px solid ${accent}`, padding: "1px 6px", borderRadius: 2,
        }}>{bet.status.toUpperCase().replace("_", " ")}</span>
        <div style={{ minWidth: 0 }}>
          <div className="mono" style={{ fontSize: 11, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {bet.title || bet.match_label || bet.fixture_id}
          </div>
          <div className="mono dim" style={{ fontSize: 9, marginTop: 1 }}>
            {bet.match_label ? `${bet.match_label} · ` : ""}
            <span style={{ color: sourceColor(bet.source) }}>{bet.source.toUpperCase()}</span>
            {bet.bookmaker ? ` · ${bet.bookmaker}` : ""} · #{bet.id}
          </div>
        </div>
        <div className="mono" style={{ fontSize: 11, textAlign: "right", color: "var(--fg-1)" }}>
          {money(bet.stake_usd)} @ {odds.toFixed(2)}
          <div className="mono dim" style={{ fontSize: 9, marginTop: 1 }}>
            MODEL {pct(bet.model_probability)} · EDGE {bet.edge != null ? pct(bet.edge) : "--"}
          </div>
        </div>
        <div className="mono" style={{
          fontSize: 10, textAlign: "right", color: clvColor, minWidth: 78,
        }}>
          {hasClosing
            ? `CLOSE ${(bet.closing_decimal ?? 0).toFixed(2)}`
            : <span className="dim">CLOSE --</span>}
          <div className="mono" style={{ fontSize: 9, marginTop: 1, color: clvColor }}>
            {bet.clv_pct != null ? `CLV ${pct(bet.clv_pct)}` : <span className="dim">CLV --</span>}
          </div>
        </div>
        <div className="mono" style={{ fontSize: 11, textAlign: "right", color: pnlColor, minWidth: 70 }}>
          {bet.pnl_usd != null ? money(bet.pnl_usd) : `EV ${money(ev)}`}
          <div className="mono dim" style={{ fontSize: 9, marginTop: 1 }}>
            {fmtTime(bet.placed_unix)}
          </div>
        </div>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", marginTop: 6, flexWrap: "wrap" }}>
        <button
          onClick={() => setExpanded((v) => !v)}
          style={{
            background: "transparent", color: "var(--fg-2)",
            border: "1px solid var(--border-dim)", padding: "2px 8px",
            borderRadius: 2, fontFamily: "var(--mono)", fontSize: 9,
            letterSpacing: "0.1em", cursor: "pointer",
          }}
        >{expanded ? "HIDE" : "DETAILS"}</button>
        {isOpen ? (
          <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
            <ActionBtn busy={busy} color="var(--green)" onClick={() => onPatch({ status: "won" })}>WON</ActionBtn>
            <ActionBtn busy={busy} color="var(--red)"   onClick={() => onPatch({ status: "lost" })}>LOST</ActionBtn>
            <ActionBtn busy={busy} color="var(--fg-1)"  onClick={() => onPatch({ status: "pushed" })}>PUSH</ActionBtn>
            <ActionBtn busy={busy} color="var(--fg-2)"  onClick={() => onPatch({ status: "void" })}>VOID</ActionBtn>
          </div>
        ) : null}
      </div>

      {expanded ? (
        <div style={{ marginTop: 8, borderTop: "1px solid var(--border-dim)", paddingTop: 8, display: "grid", gap: 6 }}>
          <div className="mono dim" style={{ fontSize: 9 }}>
            QUALIFICATION: {bet.qualification_status} · created {fmtTime(bet.created_unix)}
            {bet.settled_unix ? ` · settled ${fmtTime(bet.settled_unix)}` : ""}
          </div>
          {bet.legs.length > 0 ? (
            <div>
              <div className="mono dim" style={{ fontSize: 9, marginBottom: 3 }}>LEGS</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {bet.legs.map((leg, idx) => (
                  <span key={`${idx}-${leg.kind}`} className="mono" style={{
                    fontSize: 9, color: "var(--fg-1)",
                    border: "1px solid var(--border)", padding: "2px 5px",
                    borderRadius: 2, background: "var(--bg-3)",
                  }}>{(leg.label as string) || leg.kind}</span>
                ))}
              </div>
            </div>
          ) : null}
          {bet.qualification_checks && bet.qualification_checks.length > 0 ? (
            <div>
              <div className="mono dim" style={{ fontSize: 9, marginBottom: 3 }}>GATE SNAPSHOT</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 4 }}>
                {bet.qualification_checks.map((c) => (
                  <div key={c.label} className="mono" style={{
                    fontSize: 9,
                    color: c.pass ? "var(--green)" : c.severity === "warn" ? "var(--amber)" : "var(--red)",
                  }}>
                    {c.pass ? "✓" : c.severity === "warn" ? "⚠" : "✗"} {c.label}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {bet.notes ? (
            <div className="mono dim" style={{ fontSize: 10, lineHeight: 1.5 }}>
              <span className="mono" style={{ color: "var(--fg-1)" }}>NOTE:</span> {bet.notes}
            </div>
          ) : null}
          {isOpen ? (
            <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
              <label className="mono dim" style={{ fontSize: 9 }}>cash out actual return $</label>
              <input
                value={cashOut}
                onChange={(e) => setCashOut(e.target.value)}
                placeholder="e.g. 12.40"
                inputMode="decimal"
                style={{
                  background: "var(--bg-3)", color: "var(--fg)",
                  border: "1px solid var(--border)", padding: "2px 6px",
                  borderRadius: 2, fontFamily: "var(--mono)", fontSize: 11, width: 100,
                }}
              />
              <ActionBtn
                busy={busy}
                color="var(--magenta)"
                onClick={() => {
                  const v = parseFloat(cashOut);
                  if (!Number.isFinite(v) || v < 0) return;
                  onPatch({ status: "cashed_out", actual_return_usd: v });
                }}
              >CASH OUT</ActionBtn>
            </div>
          ) : null}
          {bet.closing_decimal != null ? (
            <div className="mono dim" style={{ fontSize: 9 }}>
              CLOSING {bet.closing_decimal.toFixed(2)} ({bet.closing_source ?? "unknown"})
              {bet.clv_pct != null ? ` · CLV ${pct(bet.clv_pct)}` : ""}
              {bet.closing_unix ? ` · ${fmtTime(bet.closing_unix)}` : ""}
            </div>
          ) : (
            <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
              <label className="mono dim" style={{ fontSize: 9 }}>snapshot closing decimal</label>
              <input
                value={closeOdds}
                onChange={(e) => setCloseOdds(e.target.value)}
                placeholder="e.g. 1.95"
                inputMode="decimal"
                style={{
                  background: "var(--bg-3)", color: "var(--fg)",
                  border: "1px solid var(--border)", padding: "2px 6px",
                  borderRadius: 2, fontFamily: "var(--mono)", fontSize: 11, width: 80,
                }}
              />
              <select
                value={closeSource}
                onChange={(e) => setCloseSource(e.target.value)}
                style={{
                  background: "var(--bg-3)", color: "var(--fg)",
                  border: "1px solid var(--border)", padding: "2px 6px",
                  borderRadius: 2, fontFamily: "var(--mono)", fontSize: 10,
                }}
              >
                <option value="pinnacle">pinnacle</option>
                <option value="bet365">bet365</option>
                <option value="odds_api">odds_api</option>
                <option value="manual">manual</option>
              </select>
              <ActionBtn
                busy={busy}
                color="var(--cyan)"
                onClick={() => {
                  const v = parseFloat(closeOdds);
                  if (!Number.isFinite(v) || v <= 1.0) return;
                  onSnapshotClosing(v, closeSource);
                }}
              >SNAPSHOT CLOSE</ActionBtn>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

function ActionBtn({
  children,
  color,
  busy,
  onClick,
}: {
  children: React.ReactNode;
  color: string;
  busy: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      style={{
        background: "rgba(255,255,255,0.05)",
        color,
        border: `1px solid ${color}`,
        padding: "2px 8px",
        borderRadius: 2,
        fontFamily: "var(--mono)",
        fontSize: 10,
        letterSpacing: "0.1em",
        cursor: busy ? "wait" : "pointer",
        opacity: busy ? 0.5 : 1,
      }}
    >{children}</button>
  );
}

function ClvSummaryStrip({ summary }: { summary: SoccerBetClvSummary | null }) {
  if (!summary) {
    return (
      <div style={{
        border: "1px dashed var(--border-dim)",
        borderRadius: 4,
        padding: 8,
        background: "rgba(255,255,255,0.012)",
      }}>
        <div className="mono dim" style={{ fontSize: 9, letterSpacing: "0.12em" }}>
          CLV TRACKING — no data yet. Snapshot a closing price on a settled bet to start measuring.
        </div>
      </div>
    );
  }
  const overall = summary.overall;
  const hasAny = overall.count_with_clv > 0;
  const avgColor = overall.avg_clv_pct == null
    ? "var(--fg-2)"
    : overall.avg_clv_pct > 0.005 ? "var(--green)"
      : overall.avg_clv_pct < -0.005 ? "var(--red)" : "var(--fg-1)";
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
      gap: 8,
      border: "1px solid var(--border)",
      borderRadius: 4,
      padding: 8,
      background: "rgba(80,180,255,0.025)",
    }}>
      <Cell label="BETS W/ CLOSE" value={`${overall.count_with_clv} / ${overall.count}`} color="var(--cyan)" />
      <Cell label="AVG CLV"       value={pct(overall.avg_clv_pct)}                       color={avgColor} />
      <Cell label="POSITIVE CLV"  value={overall.positive_clv_share != null ? pct(overall.positive_clv_share) : "--"} color="var(--fg-0)" />
      <Cell label="NET PNL"       value={money(overall.net_pnl_usd)}                     color={overall.net_pnl_usd >= 0 ? "var(--green)" : "var(--red)"} />

      {hasAny ? (
        <>
          <ClvBucketRow label="BY MARKET"   buckets={summary.by_market} />
          <ClvBucketRow label="BY RATING"   buckets={summary.by_rating_bucket} />
          <ClvBucketRow label="BY SOURCE"   buckets={summary.by_source} />
          {summary.by_slip_type.length > 0 ? (
            <ClvBucketRow label="BY SLIP" buckets={summary.by_slip_type} />
          ) : null}
        </>
      ) : (
        <div style={{ gridColumn: "1 / -1" }}>
          <div className="mono dim" style={{ fontSize: 9 }}>
            CLV is measured per bet from placed vs closing decimal odds. Hit SNAPSHOT CLOSE on a bet to record one.
          </div>
        </div>
      )}
    </div>
  );
}

function ClvBucketRow({
  label,
  buckets,
}: {
  label: string;
  buckets: SoccerBetClvBucket[];
}) {
  const items = buckets.filter((b) => b.count_with_clv > 0);
  if (items.length === 0) return null;
  return (
    <div style={{ gridColumn: "1 / -1" }}>
      <div className="mono dim" style={{ fontSize: 9, letterSpacing: "0.12em", marginBottom: 4 }}>{label}</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
        {items.slice(0, 8).map((b) => {
          const color = b.avg_clv_pct == null
            ? "var(--fg-2)"
            : b.avg_clv_pct > 0.005 ? "var(--green)"
              : b.avg_clv_pct < -0.005 ? "var(--red)" : "var(--fg-1)";
          return (
            <span
              key={`${label}-${b.bucket}`}
              className="mono"
              style={{
                fontSize: 9,
                border: `1px solid ${color}`,
                borderRadius: 2,
                padding: "2px 6px",
                color,
                background: "var(--bg-3)",
              }}
              title={`${b.count_with_clv} of ${b.count} bets with closing line · positive ${b.positive_clv_share != null ? pct(b.positive_clv_share) : "--"}`}
            >
              {b.bucket}: {pct(b.avg_clv_pct)} ({b.count_with_clv})
            </span>
          );
        })}
      </div>
    </div>
  );
}


function CalibrationSummaryStrip({ summary }: { summary: SoccerBetCalibrationSummary | null }) {
  if (!summary || summary.overall.count === 0) {
    return (
      <div style={{
        border: "1px dashed var(--border-dim)",
        borderRadius: 4,
        padding: 8,
        background: "rgba(255,255,255,0.012)",
      }}>
        <div className="mono dim" style={{ fontSize: 9, letterSpacing: "0.12em" }}>
          MODEL CALIBRATION — settle some bets (won/lost) to start scoring the model.
        </div>
      </div>
    );
  }
  const o = summary.overall;
  const brierColor = o.brier == null ? "var(--fg-2)"
    : o.brier <= 0.18 ? "var(--green)"
      : o.brier <= 0.25 ? "var(--amber)" : "var(--red)";
  const roiColor = o.roi == null ? "var(--fg-2)"
    : o.roi > 0 ? "var(--green)" : o.roi < 0 ? "var(--red)" : "var(--fg-1)";
  const evColor = o.ev_per_dollar == null ? "var(--fg-2)"
    : o.ev_per_dollar > 0 ? "var(--green)" : o.ev_per_dollar < 0 ? "var(--red)" : "var(--fg-1)";
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(5, minmax(0, 1fr))",
      gap: 8,
      border: "1px solid var(--border)",
      borderRadius: 4,
      padding: 8,
      background: "rgba(180,140,255,0.025)",
    }}>
      <Cell label="SETTLED W-L"   value={`${o.won}-${o.count - o.won}`} color="var(--fg-0)" />
      <Cell label="HIT RATE"      value={pct(o.hit_rate)}                     color="var(--fg-0)" />
      <Cell label="BRIER"         value={fmtNum(o.brier, 3)}                  color={brierColor} />
      <Cell label="MODEL EV / $"  value={pct(o.ev_per_dollar)}                color={evColor} />
      <Cell label="ROI"           value={pct(o.roi)}                          color={roiColor} />

      <CalibrationBucketRow label="BY MARKET"     buckets={summary.by_market}      mode="hit"   />
      <CalibrationBucketRow label="BY RATING"     buckets={summary.by_rating_bucket} mode="hit" />
      <CalibrationBucketRow label="BY ODDS BAND"  buckets={summary.by_odds_bucket}   mode="hit" />
      {summary.by_slip_type.length > 1 ? (
        <CalibrationBucketRow label="BY SLIP" buckets={summary.by_slip_type} mode="roi" />
      ) : null}

      <ReliabilityCurveRow points={summary.reliability_curve} />
    </div>
  );
}

function fmtNum(x: number | null | undefined, digits = 2): string {
  if (x == null || !Number.isFinite(x)) return "--";
  return x.toFixed(digits);
}

function CalibrationBucketRow({
  label,
  buckets,
  mode,
}: {
  label: string;
  buckets: SoccerBetCalibrationBucket[];
  mode: "hit" | "roi";
}) {
  const items = buckets.filter((b) => b.count > 0);
  if (items.length === 0) return null;
  return (
    <div style={{ gridColumn: "1 / -1" }}>
      <div className="mono dim" style={{ fontSize: 9, letterSpacing: "0.12em", marginBottom: 4 }}>{label}</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
        {items.slice(0, 10).map((b) => {
          const gap = b.hit_rate != null && b.avg_predicted != null
            ? b.hit_rate - b.avg_predicted
            : null;
          const color = mode === "hit"
            ? (gap == null ? "var(--fg-2)"
                : gap > 0.04 ? "var(--green)"
                  : gap < -0.04 ? "var(--red)" : "var(--fg-1)")
            : (b.roi == null ? "var(--fg-2)"
                : b.roi > 0 ? "var(--green)"
                  : b.roi < 0 ? "var(--red)" : "var(--fg-1)");
          const valueText = mode === "hit"
            ? pct(b.hit_rate) + " / " + pct(b.avg_predicted)
            : pct(b.roi);
          return (
            <span
              key={`${label}-${b.bucket}`}
              className="mono"
              style={{
                fontSize: 9,
                border: `1px solid ${color}`,
                borderRadius: 2,
                padding: "2px 6px",
                color,
                background: "var(--bg-3)",
              }}
              title={`${b.count} bets · won ${b.won} · brier ${fmtNum(b.brier, 3)} · roi ${pct(b.roi)}`}
            >
              {b.bucket}: {valueText} ({b.count})
            </span>
          );
        })}
      </div>
    </div>
  );
}

function ReliabilityCurveRow({ points }: { points: SoccerBetCalibrationCurvePoint[] }) {
  const total = points.reduce((s, p) => s + p.count, 0);
  if (total === 0) return null;
  return (
    <div style={{ gridColumn: "1 / -1" }}>
      <div className="mono dim" style={{ fontSize: 9, letterSpacing: "0.12em", marginBottom: 4 }}>
        RELIABILITY CURVE — predicted vs observed (per decile)
      </div>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(10, minmax(0, 1fr))",
        gap: 3,
      }}>
        {points.map((p) => {
          const empty = p.count === 0 || p.gap == null;
          const gapColor = empty ? "var(--fg-2)"
            : Math.abs(p.gap as number) <= 0.05 ? "var(--green)"
              : Math.abs(p.gap as number) <= 0.10 ? "var(--amber)" : "var(--red)";
          const lo = Math.round(p.p_lo * 100);
          const hi = Math.round(p.p_hi * 100);
          return (
            <div
              key={p.bucket}
              className="mono"
              style={{
                fontSize: 9,
                padding: "3px 4px",
                border: `1px solid ${empty ? "var(--border-dim)" : gapColor}`,
                borderRadius: 2,
                background: "var(--bg-3)",
                color: empty ? "var(--fg-2)" : "var(--fg-0)",
                textAlign: "center",
              }}
              title={`${p.count} bets · predicted ${pct(p.avg_predicted)} · observed ${pct(p.observed_hit_rate)} · gap ${pct(p.gap)}`}
            >
              <div style={{ fontSize: 8, opacity: 0.7 }}>{lo}-{hi}%</div>
              <div style={{ color: empty ? "var(--fg-2)" : gapColor }}>
                {empty ? "--" : pct(p.observed_hit_rate)}
              </div>
              <div style={{ fontSize: 8, opacity: 0.6 }}>n={p.count}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
