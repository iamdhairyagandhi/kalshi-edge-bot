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
import { api, SoccerFixture, SoccerMatchSummary } from "../../api/client";
import FixturesPanel from "./FixturesPanel";
import MatchProbabilitiesPanel from "./MatchProbabilitiesPanel";
import BetBuilderPanel from "./BetBuilderPanel";
import SoccerCalibrationPanel from "./SoccerCalibrationPanel";

export default function SoccerTab() {
  const [fixtures, setFixtures] = useState<SoccerFixture[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [match, setMatch] = useState<SoccerMatchSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [seedingError, setSeedingError] = useState<string | null>(null);
  const [fitting, setFitting] = useState(false);
  const [fitInfo, setFitInfo] = useState<string | null>(null);

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

  return (
    <div className="tab-grid soccer-grid" style={{ gridTemplateColumns: "1fr 1.3fr" }}>
      <section style={{ gridColumn: "1 / 2" }}>
        <FixturesPanel
          fixtures={fixtures}
          selectedId={selectedId}
          onSelect={setSelectedId}
          empty={empty}
          onSeedDemo={seedDemo}
          onFitReal={fitReal}
          fitting={fitting}
          fitInfo={fitInfo}
          seedError={seedingError}
        />
      </section>
      <section style={{ gridColumn: "2 / 3" }}>
        <MatchProbabilitiesPanel match={match} loading={loading} />
      </section>
      <section className="span-2">
        <BetBuilderPanel match={match} />
      </section>
      <section className="span-2">
        <SoccerCalibrationPanel />
      </section>
    </div>
  );
}
