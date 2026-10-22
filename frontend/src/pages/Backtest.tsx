import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, num, pct } from "../api";

type Variant = { name: string; params: Record<string, unknown>; color: string; kind?: string };

// Setups taken from STRATEGY.md / the PDF, plus baselines that show whether the profit is skill or market.
const PALETTE = ["#34d399", "#60a5fa", "#f472b6", "#a78bfa", "#fb923c", "#facc15", "#2dd4bf", "#f87171", "#94a3b8", "#c084fc", "#4ade80", "#38bdf8"];
const PRESETS: { id: string; name: string; note: string; params: Record<string, unknown>; kind?: string }[] = [
  { id: "spec", name: "Spec: L14 k5 gated", note: "STRATEGY.md §1", params: { lookback: 14, k: 5, gate_on: true } },
  { id: "k3", name: "L14 k3 gated", note: "higher return, bigger drawdown", params: { lookback: 14, k: 3, gate_on: true } },
  { id: "combo5", name: "7/14/30 combined k5", note: "PDF §7", params: { combo: true, k: 5, gate_on: true } },
  { id: "combo3", name: "7/14/30 combined k3", note: "PDF §7", params: { combo: true, k: 3, gate_on: true } },
  { id: "l7", name: "L7 k5 gated", note: "edge died after 2021", params: { lookback: 7, k: 5, gate_on: true } },
  { id: "l30", name: "L30 k5 gated", note: "weaker after 2021", params: { lookback: 30, k: 5, gate_on: true } },
  { id: "nogate", name: "L14 k5 NO gate", note: "shows what the BTC gate adds", params: { lookback: 14, k: 5, gate_on: false } },
  { id: "invvol", name: "L14 k5 inverse-vol", note: "PDF §8: less risk", params: { lookback: 14, k: 5, gate_on: true, weighting: "invvol" } },
  { id: "exp60", name: "L14 k5 at 60% exposure", note: "PDF §8: cap risk", params: { lookback: 14, k: 5, gate_on: true, exposure: 0.6 } },
  { id: "longonly", name: "Long-only top-5 gated", note: "no shorts (no position fee)", params: { lookback: 14, k: 5, gate_on: true, long_frac: 1, short_frac: 0 } },
  { id: "random", name: "Random picks (null)", note: "same rules, random coins: is it skill?", params: { lookback: 14, k: 5, gate_on: true }, kind: "random" },
  { id: "btctrend", name: "BTC trend only", note: "hold BTC only above SMA100", params: {}, kind: "btc_trend" },
  { id: "btchold", name: "BTC buy & hold", note: "benchmark", params: {}, kind: "btc_hold" },
];

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

  const [picked, setPicked] = useState<string[]>(["spec", "combo5", "btctrend", "btchold"]);
  const [progress, setProgress] = useState("");
  const execute = async (variants: Variant[]) => {
    setBusy(true); setErr("");
    try {
      const out = [];
      for (const [i, v] of variants.entries()) {
        setProgress(`${i + 1}/${variants.length}: ${v.name}`);
        out.push({ name: v.name, color: v.color, res: await api("/backtest", { body: { source, costs, kind: v.kind, name: v.name, params: v.params } }) });
      }
      setRuns(out); setProgress("");
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  const base = { k, gate_on: gate };
  const run = () => execute([{ name: combo ? "7/14/30 combined" : `L${lookback}`, params: { ...base, lookback, combo }, color: "#34d399" }]);
  const runPresets = () => execute(PRESETS.filter((x) => picked.includes(x.id)).map((x, i) => ({ name: x.name, params: x.params, kind: x.kind, color: PALETTE[i % PALETTE.length] })));
  const toggle = (id: string) => setPicked((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

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
        <button className="btn" disabled={busy} onClick={run}>{busy ? "Running…" : "Run custom"}</button>
      </div>
      <div className="card space-y-2">
        <div className="text-sm font-medium">Compare setups (same data and costs as above)</div>
        <div className="grid gap-x-4 gap-y-1 md:grid-cols-2">
          {PRESETS.map((x) => <label key={x.id} className="text-sm"><input type="checkbox" checked={picked.includes(x.id)} onChange={() => toggle(x.id)} /> {x.name} <span className="text-xs text-zinc-500">– {x.note}</span></label>)}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-primary" disabled={busy || picked.length === 0} onClick={runPresets}>{busy ? "Running…" : `Run ${picked.length} selected`}</button>
          <button className="btn" onClick={() => setPicked(PRESETS.map((x) => x.id))}>All</button>
          <button className="btn" onClick={() => setPicked([])}>None</button>
          <span className="text-xs text-zinc-400">{progress}</span>
        </div>
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
