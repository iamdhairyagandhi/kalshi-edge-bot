import { useEffect } from "react";
import { api, StreamClient } from "./api/client";
import { useStore } from "./store";
import TopBar from "./panels/TopBar";
import EquityCurve from "./panels/EquityCurve";
import PositionsTable from "./panels/PositionsTable";
import ConsensusFeed from "./panels/ConsensusFeed";
import CohortTable from "./panels/CohortTable";
import FillsTable from "./panels/FillsTable";
import LatencyHistogram from "./panels/LatencyHistogram";
import PortfolioCard from "./panels/PortfolioCard";

export default function App() {
  const { venue, setSnapshot, setWsAlive, pushFill, pushSignal } = useStore();

  // Initial snapshot + 5s polling for things the WS doesn't push.
  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const [portfolio, equity, positions, fills, signals, cohort, latency] = await Promise.all([
          api.portfolio(venue === "all" ? undefined : venue),
          api.equity(venue === "all" ? undefined : venue),
          api.positions(venue === "all" ? undefined : venue),
          api.fills(venue === "all" ? undefined : venue),
          api.signals(),
          api.cohort(),
          api.latency(),
        ]);
        if (cancelled) return;
        setSnapshot({ portfolio, equity, positions, fills, signals, cohort, latency });
      } catch (e) {
        console.warn("snapshot refresh failed", e);
      }
    }
    refresh();
    const id = setInterval(refresh, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [venue, setSnapshot]);

  // WebSocket stream for low-latency push.
  useEffect(() => {
    const c = new StreamClient(
      (ev) => {
        if (ev.type === "fill") pushFill(ev.payload);
        else if (ev.type === "signal") pushSignal(ev.payload);
      },
      (alive) => setWsAlive(alive),
    );
    c.connect();
    return () => c.close();
  }, [pushFill, pushSignal, setWsAlive]);

  return (
    <>
      <TopBar />
      <div className="dash-grid">
        <div className="cell-equity"><EquityCurve /></div>
        <div className="cell-cohort"><CohortTable /></div>
        <div className="cell-positions"><PositionsTable /></div>
        <div className="cell-signals"><ConsensusFeed /></div>
        <div className="cell-fills"><FillsTable /></div>
        <div className="cell-latency"><LatencyHistogram /></div>
        <div className="cell-portfolio"><PortfolioCard /></div>
      </div>
    </>
  );
}
