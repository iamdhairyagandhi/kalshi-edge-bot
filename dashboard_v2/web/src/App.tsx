import { useEffect, useState } from "react";
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
import BrierGauge from "./panels/BrierGauge";
import KillSwitchPanel from "./panels/KillSwitchPanel";
import OrderbookDepth from "./panels/OrderbookDepth";
import CrossVenueSpread from "./panels/CrossVenueSpread";
import TradeBlockers from "./panels/TradeBlockers";
import WeatherSpecialist from "./panels/WeatherSpecialist";
import SoccerTab from "./panels/soccer/SoccerTab";

type DashboardTab = "live" | "smart" | "weather" | "arbitrage" | "soccer" | "risk";

const tabs: { id: DashboardTab; label: string }[] = [
  { id: "live", label: "Live Trades" },
  { id: "smart", label: "Smart Money" },
  { id: "weather", label: "Weather" },
  { id: "arbitrage", label: "Arbitrage" },
  { id: "soccer", label: "Soccer" },
  { id: "risk", label: "Risk" },
];

export default function App() {
  const { venue, setSnapshot, setWsAlive, pushFill, pushSignal } = useStore();
  const [activeTab, setActiveTab] = useState<DashboardTab>("live");

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const [portfolio, equity, positions, fills, signals, cohort, latency, crossVenue, diagnostics, weather, brier] = await Promise.all([
          api.portfolio(venue === "all" ? undefined : venue),
          api.equity(venue === "all" ? undefined : venue),
          api.positions(venue === "all" ? undefined : venue),
          api.fills(venue === "all" ? undefined : venue),
          api.signals(),
          api.cohort(),
          api.latency(),
          api.crossVenue(),
          api.diagnostics(),
          api.weather(),
          api.brier().catch(() => []),
        ]);
        if (cancelled) return;
        setSnapshot({ portfolio, equity, positions, fills, signals, cohort, latency, crossVenue, diagnostics, weather, brier });
      } catch (e) {
        console.warn("snapshot refresh failed", e);
      }
    }
    refresh();
    const id = setInterval(refresh, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [venue, setSnapshot]);

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
      <div className="workspace">
        <nav className="tabbar" aria-label="Dashboard sections">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              className={`tab ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
              type="button"
            >
              {tab.label}
            </button>
          ))}
        </nav>
        {activeTab === "live" ? <LiveTradesTab /> : null}
        {activeTab === "smart" ? <SmartMoneyTab /> : null}
        {activeTab === "weather" ? <WeatherTab /> : null}
        {activeTab === "arbitrage" ? <ArbitrageTab /> : null}
        {activeTab === "soccer" ? <SoccerTab /> : null}
        {activeTab === "risk" ? <RiskTab /> : null}
      </div>
    </>
  );
}

function LiveTradesTab() {
  return (
    <div className="tab-grid live-grid">
      <section className="span-2"><EquityCurve /></section>
      <section><PortfolioCard /></section>
      <section><OrderbookDepth /></section>
      <section className="span-2"><PositionsTable /></section>
      <section className="span-2"><FillsTable /></section>
    </div>
  );
}

function SmartMoneyTab() {
  return (
    <div className="tab-grid smart-grid">
      <section><CohortTable /></section>
      <section><ConsensusFeed /></section>
      <section><LatencyHistogram /></section>
      <section className="span-3"><TradeBlockers /></section>
    </div>
  );
}

function WeatherTab() {
  return (
    <div className="tab-grid weather-grid">
      <section className="span-3"><WeatherSpecialist /></section>
      <section className="span-3"><TradeBlockers /></section>
    </div>
  );
}

function ArbitrageTab() {
  return (
    <div className="tab-grid arbitrage-grid">
      <section className="span-2"><CrossVenueSpread /></section>
      <section><OrderbookDepth /></section>
      <section className="span-3"><TradeBlockers /></section>
    </div>
  );
}

function RiskTab() {
  return (
    <div className="tab-grid risk-grid">
      <section><PortfolioCard /></section>
      <section><BrierGauge /></section>
      <section><KillSwitchPanel /></section>
      <section className="span-3"><TradeBlockers /></section>
    </div>
  );
}
