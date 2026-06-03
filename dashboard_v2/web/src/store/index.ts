import { create } from "zustand";
import type { Fill, Portfolio, Position, Signal, CohortWallet, EquityPoint, LatencyBucket } from "../api/client";

type State = {
  venue: "all" | "kalshi" | "polymarket";
  wsAlive: boolean;
  portfolio: Portfolio | null;
  equity: EquityPoint[];
  positions: Position[];
  fills: Fill[];
  signals: Signal[];
  cohort: CohortWallet[];
  latency: LatencyBucket[];
  // mutators
  setVenue: (v: State["venue"]) => void;
  setWsAlive: (a: boolean) => void;
  setSnapshot: (s: Partial<State>) => void;
  pushFill: (f: Fill) => void;
  pushSignal: (s: Signal) => void;
};

export const useStore = create<State>((set) => ({
  venue: "all",
  wsAlive: false,
  portfolio: null,
  equity: [],
  positions: [],
  fills: [],
  signals: [],
  cohort: [],
  latency: [],
  setVenue: (venue) => set({ venue }),
  setWsAlive: (wsAlive) => set({ wsAlive }),
  setSnapshot: (s) => set(s as any),
  pushFill: (f) => set((st) => ({ fills: [f, ...st.fills].slice(0, 500) })),
  pushSignal: (s) => set((st) => {
    // Dedup by idempotency_key, keep latest 200
    const existing = st.signals.filter((x) => x.idempotency_key !== s.idempotency_key);
    return { signals: [s, ...existing].slice(0, 200) };
  }),
}));
