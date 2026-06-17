import { SoccerFixture } from "../../api/client";

type Props = {
  fixtures: SoccerFixture[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  empty: boolean;
  onSeedDemo: () => void;
  onFitReal: () => void;
  fitting: boolean;
  fitInfo: string | null;
  seedError: string | null;
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
  fixtures, selectedId, onSelect, empty, onSeedDemo, onFitReal, fitting, fitInfo, seedError,
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
                  background: "rgba(80,180,255,0.18)", color: "var(--blue)",
                  border: "1px solid rgba(80,180,255,0.45)",
                  padding: "4px 12px", borderRadius: 2, fontFamily: "var(--mono)",
                  fontSize: 11, cursor: fitting ? "wait" : "pointer", letterSpacing: "0.1em",
                  opacity: fitting ? 0.5 : 1,
                }}
              >{fitting ? "FETCHING…" : "FIT REAL DATA (WC18/WC22/EURO20/EURO24)"}</button>
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
              Free, takes ~5s on a warm cache. <strong>SEED DEMO</strong> uses synthetic priors only.
            </span>
          </div>
        ) : (
          <table className="tight">
            <thead>
              <tr>
                <th>KICKOFF</th>
                <th>MATCH</th>
                <th className="right">H</th>
                <th className="right">D</th>
                <th className="right">A</th>
                <th className="right">O2.5</th>
                <th className="right">BTTS</th>
              </tr>
            </thead>
            <tbody>
              {fixtures.map((f) => {
                const sel = selectedId === f.fixture_id;
                return (
                  <tr
                    key={f.fixture_id}
                    onClick={() => onSelect(f.fixture_id)}
                    style={{
                      cursor: "pointer",
                      background: sel ? "rgba(80,180,255,0.10)" : undefined,
                    }}
                  >
                    <td className="mono dim" style={{ fontSize: 11 }}>{fmtTime(f.kickoff_unix)}</td>
                    <td className="mono">
                      {f.home_team_name || f.home_team_id} <span className="dim">vs</span>{" "}
                      {f.away_team_name || f.away_team_id}
                    </td>
                    <td className="right mono">{fmtPct(f.home_win)}</td>
                    <td className="right mono dim">{fmtPct(f.draw)}</td>
                    <td className="right mono">{fmtPct(f.away_win)}</td>
                    <td className="right mono dim">{fmtPct(f.over_2_5)}</td>
                    <td className="right mono dim">{fmtPct(f.btts_yes)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
