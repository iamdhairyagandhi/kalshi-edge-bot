import { useEffect, useRef } from "react";
import { createChart, ColorType, IChartApi, ISeriesApi, LineStyle } from "lightweight-charts";
import { useStore } from "../store";

export default function EquityCurve() {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const equity = useStore((s) => s.equity);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#aab4c2", fontFamily: "JetBrains Mono" },
      grid: { vertLines: { color: "#1b2129" }, horzLines: { color: "#1b2129" } },
      rightPriceScale: { borderColor: "#232a36" },
      timeScale: { borderColor: "#232a36", timeVisible: true, secondsVisible: false },
      crosshair: { vertLine: { color: "#18d2e0", style: LineStyle.Dotted, width: 1 }, horzLine: { color: "#18d2e0", style: LineStyle.Dotted, width: 1 } },
      width: ref.current.clientWidth, height: ref.current.clientHeight,
    });
    const series = chart.addAreaSeries({
      lineColor: "#19c37d",
      topColor: "rgba(25, 195, 125, 0.32)",
      bottomColor: "rgba(25, 195, 125, 0.02)",
      lineWidth: 2,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });
    chartRef.current = chart;
    seriesRef.current = series;

    const ro = new ResizeObserver(() => {
      if (ref.current && chartRef.current) chartRef.current.applyOptions({ width: ref.current.clientWidth, height: ref.current.clientHeight });
    });
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.remove(); chartRef.current = null; seriesRef.current = null; };
  }, []);

  useEffect(() => {
    if (!seriesRef.current) return;
    if (equity.length === 0) { seriesRef.current.setData([]); return; }
    const data = equity.map((p) => ({ time: p.timestamp_unix as any, value: p.equity }));
    seriesRef.current.setData(data);
    chartRef.current?.timeScale().fitContent();
  }, [equity]);

  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; EQUITY CURVE</span>
        <span className="mono dim">{equity.length} pts</span>
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        {equity.length === 0 && <div className="empty">no trades yet — equity will populate once the runners fill</div>}
        <div ref={ref} style={{ width: "100%", height: "100%" }} />
      </div>
    </div>
  );
}
