import { useEffect, useState } from "react";
import { api, SoccerCalibrationRow } from "../../api/client";

export default function SoccerCalibrationPanel() {
  const [rows, setRows] = useState<SoccerCalibrationRow[]>([]);
  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const r = await api.soccerCalibration();
        if (!cancelled) setRows(r);
      } catch { /* not fitted yet */ }
    }
    refresh();
    const id = setInterval(refresh, 30000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  return (
    <div className="panel">
      <div className="panel-header">
        <span><span className="ind" /> &nbsp; CALIBRATION</span>
        <span className="mono dim">{rows.length}</span>
      </div>
      <div className="panel-body">
        {rows.length === 0 ? (
          <div className="empty">no resolved soccer predictions yet</div>
        ) : (
          <table className="tight">
            <thead>
              <tr>
                <th>MARKET</th>
                <th className="right">N</th>
                <th className="right">BRIER</th>
                <th>RELIABILITY</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.market_type}>
                  <td className="mono">{r.market_type}</td>
                  <td className="right mono dim">{r.n_resolved}</td>
                  <td className="right mono">{r.brier.toFixed(4)}</td>
                  <td>
                    <span style={{ display: "flex", gap: 1 }}>
                      {r.reliability.map((b, i) => {
                        const dy = b.observed - b.predicted;
                        const color =
                          b.n === 0 ? "var(--bg-4)" :
                          Math.abs(dy) < 0.03 ? "var(--green)" :
                          Math.abs(dy) < 0.07 ? "var(--amber)" : "var(--red)";
                        return (
                          <span
                            key={i}
                            title={`bin [${b.bin_lo.toFixed(2)}, ${b.bin_hi.toFixed(2)}) n=${b.n} pred=${b.predicted.toFixed(3)} obs=${b.observed.toFixed(3)}`}
                            style={{ width: 10, height: 18, background: color, opacity: b.n === 0 ? 0.25 : 1 }}
                          />
                        );
                      })}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
