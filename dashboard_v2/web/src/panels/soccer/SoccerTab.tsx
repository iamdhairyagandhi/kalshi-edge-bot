/**
 * Soccer / FIFA WC bet-builder tab.
 *
 * Layout: fixtures list on the left, selected match panel + bet builder on
 * the right, calibration at the bottom.
 *
 * The tab owns its own state (independent of the global zustand store)
 * because the soccer engine is server-side and only consulted on demand.
 */

import { useEffect, useMemo, useState } from "react";
import { api, SoccerBetLeg, SoccerFixture, SoccerMatchSummary } from "../../api/client";
import FixturesPanel from "./FixturesPanel";
import MatchProbabilitiesPanel from "./MatchProbabilitiesPanel";
import BetBuilderPanel from "./BetBuilderPanel";
import SoccerCalibrationPanel from "./SoccerCalibrationPanel";
import EdgeBoardPanel from "./EdgeBoardPanel";
import BetslipCreatorPanel from "./BetslipCreatorPanel";

export default function SoccerTab() {
  const [fixtures, setFixtures] = useState<SoccerFixture[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [match, setMatch] = useState<SoccerMatchSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [seedingError, setSeedingError] = useState<string | null>(null);
  const [fitting, setFitting] = useState(false);
  const [fitInfo, setFitInfo] = useState<string | null>(null);
  const [presetLegs, setPresetLegs] = useState<SoccerBetLeg[]>([]);
  const [presetKey, setPresetKey] = useState(0);
  const [presetBookOdds, setPresetBookOdds] = useState<number | null>(null);

  async function refreshFixtures() {
    try {
      const f = await api.soccerFixtures();
      setFixtures(f);
      if (!selectedId && f.length > 0) setSelectedId(f[0].fixture_id);
    } catch (e: any) {
      console.warn("soccerFixtures failed", e);
    }
  }

  async function seedDemo() {
    setSeedingError(null);
    try {
      await api.soccerSeedDemo();
      setFitInfo("demo seed");
      await refreshFixtures();
    } catch (e: any) {
      setSeedingError(String(e));
    }
  }

  async function fitReal() {
    setSeedingError(null);
    setFitting(true);
    try {
      const r = await api.soccerFitStatsBomb();
      setFitInfo(`real · ${r.matches_used} matches · ${r.teams} teams`);
      await refreshFixtures();
    } catch (e: any) {
      setSeedingError(String(e));
    } finally {
      setFitting(false);
    }
  }

  async function loadOddsFixtures() {
    setSeedingError(null);
    setFitting(true);
    try {
      const r = await api.soccerIngestOddsFixtures();
      if (!r.odds_api_key_configured) {
        setSeedingError("ODDS_API_KEY is not configured, so no real fixtures can be loaded.");
      } else {
        setFitInfo(`odds · ${r.fixtures_added}/${r.events_seen} matched`);
      }
      await refreshFixtures();
    } catch (e: any) {
      setSeedingError(String(e));
    } finally {
      setFitting(false);
    }
  }

  useEffect(() => {
    refreshFixtures();
    // refresh fixtures probabilities periodically so any state-space update
    // on the backend reflects in the UI.
    const id = setInterval(refreshFixtures, 30000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedId) { setMatch(null); return; }
    let cancelled = false;
    setLoading(true);
    api.soccerMatch(selectedId, 5000)
      .then((m) => { if (!cancelled) setMatch(m); })
      .catch((e) => {
        if (!cancelled) {
          console.warn("soccerMatch failed", e);
          setMatch(null);
        }
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selectedId]);

  const empty = useMemo(() => fixtures.length === 0, [fixtures.length]);

  function useLegsInBuilder(legs: SoccerBetLeg[], fixtureId?: string, bookOdds?: number | null) {
    if (fixtureId) setSelectedId(fixtureId);
    setPresetLegs(legs);
    setPresetBookOdds(bookOdds ?? null);
    setPresetKey((k) => k + 1);
  }

  const selectedFixture = fixtures.find((f) => f.fixture_id === selectedId) || null;
  const selectedName = selectedFixture
    ? `${selectedFixture.home_team_name || selectedFixture.home_team_id} vs ${selectedFixture.away_team_name || selectedFixture.away_team_id}`
    : "No fixture selected";

  return (
    <div className="soccer-desk">
      <section className="soccer-rail">
        <FixturesPanel
          fixtures={fixtures}
          selectedId={selectedId}
          onSelect={setSelectedId}
          empty={empty}
          onSeedDemo={seedDemo}
          onFitReal={fitReal}
          onLoadOddsFixtures={loadOddsFixtures}
          fitting={fitting}
          fitInfo={fitInfo}
          seedError={seedingError}
        />
      </section>

      <section className="soccer-status">
        <div className="soccer-status-card">
          <div>
            <div className="mono dim" style={{ fontSize: 10, letterSpacing: "0.14em" }}>SELECTED MATCH</div>
            <div className="mono" style={{ fontSize: 18 }}>{selectedName}</div>
          </div>
          <div className="soccer-status-metrics">
            <Mini label="FIXTURES" value={String(fixtures.length)} />
            <Mini label="MODEL" value={match ? "READY" : loading ? "LOADING" : "WAIT"} tone={match ? "green" : "dim"} />
            <Mini label="SOURCE" value={fitInfo || "STORE"} tone="cyan" />
          </div>
        </div>
      </section>

      <section className="soccer-match">
        <MatchProbabilitiesPanel match={match} loading={loading} />
      </section>

      <section className="soccer-edge">
        <EdgeBoardPanel match={match} onUseLegs={useLegsInBuilder} />
      </section>

      <section className="soccer-slip">
        <BetslipCreatorPanel onUseLegs={useLegsInBuilder} onSelectFixture={setSelectedId} />
      </section>

      <section className="soccer-builder">
        <BetBuilderPanel
          match={match}
          presetLegs={presetLegs}
          presetKey={presetKey}
          presetBookOdds={presetBookOdds}
        />
      </section>

      <section className="soccer-calibration">
        <SoccerCalibrationPanel />
      </section>
    </div>
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
