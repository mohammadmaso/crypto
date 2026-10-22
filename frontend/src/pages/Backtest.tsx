import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, num, pct } from "../api";

type Variant = { name: string; params: Record<string, unknown>; color: string };

export default function Backtest() {
  const [source, setSource] = useState("binance");
  const [costs, setCosts] = useState("nobitex");
  const [lookback, setLookback] = useState(14);
  const [k, setK] = useState(5);
  const [gate, setGate] = useState(true);
  const [combo, setCombo] = useState(false);
  const [runs, setRuns] = useState<{ name: string; color: string; res: any }[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const execute = async (variants: Variant[]) => {
    setBusy(true); setErr("");
    try {
      const out = [];
      for (const v of variants) out.push({ name: v.name, color: v.color, res: await api("/backtest", { body: { source, costs, params: v.params } }) });
      setRuns(out);
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  const base = { k, gate_on: gate };
  const run = () => execute([{ name: combo ? "7/14/30 combined" : `L${lookback}`, params: { ...base, lookback, combo }, color: "#34d399" }]);
  const compare = () => execute([
    { name: `L${lookback}`, params: { ...base, lookback, combo: false }, color: "#34d399" },
    { name: "7/14/30 combined", params: { ...base, lookback, combo: true }, color: "#60a5fa" },
  ]);

  const first = runs[0]?.res;
  const merged = first ? first.equity.map((e: any, i: number) => {
    const row: any = { day: e.day, btc: first.btc[i]?.equity };
    runs.forEach((r) => (row[r.name] = r.res.equity[i]?.equity));
    return row;
  }) : [];

  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-end gap-3">
        <label className="text-xs">Data<select className="input" value={source} onChange={(e) => setSource(e.target.value)}><option value="binance">Binance research (survivorship-free)</option><option value="nobitex">Nobitex stored candles</option></select></label>
        <label className="text-xs">Costs<select className="input" value={costs} onChange={(e) => setCosts(e.target.value)}><option value="nobitex">Nobitex (fees + position fee)</option><option value="binance">Binance research model</option></select></label>
        <label className="text-xs">Lookback<input className="input w-20" type="number" disabled={combo} value={lookback} onChange={(e) => setLookback(+e.target.value)} /></label>
        <label className="text-xs">k<input className="input w-16" type="number" value={k} onChange={(e) => setK(+e.target.value)} /></label>
        <label className="text-xs"><input type="checkbox" checked={combo} onChange={(e) => setCombo(e.target.checked)} /> Combine 7/14/30</label>
        <label className="text-xs"><input type="checkbox" checked={gate} onChange={(e) => setGate(e.target.checked)} /> BTC gate</label>
        <button className="btn-primary" disabled={busy} onClick={run}>{busy ? "Running…" : "Run backtest"}</button>
        <button className="btn" disabled={busy} onClick={compare}>Compare: L{lookback} vs 7/14/30 combined</button>
      </div>
      {err && <div className="card text-amber-400">{err}</div>}
      {runs.length > 0 && <>
        <div className="card overflow-x-auto"><table className="w-full"><thead><tr><th>Strategy</th><th>CAGR</th><th>Sharpe</th><th>Max DD</th><th>Vol</th><th>2022+ CAGR</th><th>2022+ Sharpe</th><th>P&L: long / short / pos.fee / fees</th></tr></thead>
          <tbody>{runs.map((r) => <tr key={r.name} className="border-t border-zinc-800">
            <td style={{ color: r.color }}>{r.name}</td><td>{pct(r.res.stats.cagr)}</td><td>{num(r.res.stats.sharpe)}</td><td>{pct(r.res.stats.max_dd)}</td><td>{pct(r.res.stats.vol)}</td>
            <td>{pct(r.res.post2021.cagr)}</td><td>{num(r.res.post2021.sharpe)}</td>
            <td className="text-xs">{["long_pnl", "short_pnl", "position_fee", "fees"].map((x) => num(r.res.contrib[x], 3)).join(" / ")}</td></tr>)}</tbody></table></div>
        <div className="card h-72"><ResponsiveContainer><LineChart data={merged}><CartesianGrid stroke="#27272a" /><XAxis dataKey="day" stroke="#71717a" fontSize={11} /><YAxis scale="log" domain={["auto", "auto"]} stroke="#71717a" fontSize={11} />
          <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46" }} formatter={(v: any) => Number(v).toFixed(2)} /><Legend />
          {runs.map((r) => <Line key={r.name} dataKey={r.name} stroke={r.color} dot={false} />)}<Line dataKey="btc" name="BTC" stroke="#f59e0b" dot={false} /></LineChart></ResponsiveContainer></div>
        <div className="card"><div className="mb-1 text-sm font-medium">Calendar years</div>
          <table className="w-full"><thead><tr><th>Year</th>{runs.map((r) => <th key={r.name}>{r.name}</th>)}</tr></thead>
            <tbody>{Object.keys(first.years).map((y) => <tr key={y} className="border-t border-zinc-800"><td>{y}</td>
              {runs.map((r) => { const v = r.res.years[y]; return <td key={r.name} className={v >= 0 ? "text-emerald-400" : "text-red-400"}>{pct(v)}</td>; })}</tr>)}</tbody></table></div>
      </>}
    </div>
  );
}
