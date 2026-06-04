import { useEffect, useMemo, useState } from "react";
import { api, type Orderbook } from "../api/client";
import { useStore } from "../store";

/* Renders a bid/ask depth ladder for a selected Polymarket market.
   Defaults to the most recently signaled market. Will gracefully
   degrade to an error state if the live Polymarket API is unreachable
   (e.g. on a corp network that blocks egress). */

export default function OrderbookDepth() {
  const signals = useStore((s) => s.signals);
  const defaultCond = useMemo(() => signals.find((s) => s.condition_id)?.condition_id, [signals]);
  const defaultIdx = useMemo(() => signals.find((s) => s.condition_id)?.outcome_index ?? 0, [signals]);
  const [book, setBook] = useState<Orderbook | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!defaultCond) { setBook(null); return; }
    let cancelled = false;
    async function refresh() {
      try {
        const b = await api.book(defaultCond!, defaultIdx);
        if (!cancelled) { setBook(b); setErr(null); }
      } catch (e: any) {
        if (!cancelled) setErr(String(e?.message || e));
      }
    }
    refresh();
    const id = setInterval(refresh, 4000);
    return () => { cancelled = true; clearInterval(id); };
  }, [defaultCond, defaultIdx]);

  const maxSize = book
    ? Math.max(1, ...book.bids.slice(0, 8).map((l) => l.size), ...book.asks.slice(0, 8).map((l) => l.size))
    : 1;

  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; ORDERBOOK</span>
        <span className="mono dim" title={defaultCond}>
          {defaultCond ? `${defaultCond.slice(0, 10)}…#${defaultIdx}` : "no market"}
        </span>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        {!defaultCond ? (
          <div className="empty">no recent signal to track</div>
        ) : err ? (
          <div className="empty" style={{ color: "var(--red)" }}>
            book unavailable
            <div className="dim" style={{ fontSize: 10, marginTop: 6 }}>{err}</div>
          </div>
        ) : !book ? (
          <div className="empty">loading…</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", padding: 8, gap: 0, fontFamily: "var(--mono)", fontSize: 11 }}>
            {book.asks.slice(0, 8).reverse().map((l, i) => (
              <Row key={`a${i}`} side="ask" price={l.price} size={l.size} maxSize={maxSize} />
            ))}
            <div style={{
              borderTop: "1px solid var(--border-hot)", borderBottom: "1px solid var(--border-hot)",
              padding: "4px 6px", color: "var(--cyan)", fontSize: 10, letterSpacing: "0.12em",
              textTransform: "uppercase", display: "flex", justifyContent: "space-between", margin: "2px 0",
            }}>
              <span>spread</span>
              <span>{book.asks[0] && book.bids[0]
                ? ((book.asks[0].price - book.bids[0].price) * 100).toFixed(1) + "¢"
                : "—"}</span>
            </div>
            {book.bids.slice(0, 8).map((l, i) => (
              <Row key={`b${i}`} side="bid" price={l.price} size={l.size} maxSize={maxSize} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ side, price, size, maxSize }: { side: "bid" | "ask"; price: number; size: number; maxSize: number }) {
  const color = side === "bid" ? "var(--green)" : "var(--red)";
  const bg = side === "bid" ? "rgba(25,195,125,0.10)" : "rgba(255,77,79,0.10)";
  const pct = (size / maxSize) * 100;
  return (
    <div style={{ position: "relative", display: "grid", gridTemplateColumns: "1fr 1fr", padding: "2px 6px" }}>
      <div style={{ position: "absolute", inset: 0, background: bg, width: `${pct}%`, [side === "bid" ? "left" : "right"]: 0 } as any} />
      <span style={{ position: "relative", color }}>{price.toFixed(3)}</span>
      <span style={{ position: "relative", textAlign: "right", color: "var(--fg-0)" }}>{size.toFixed(0)}</span>
    </div>
  );
}
