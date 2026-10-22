import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, num, pct } from "../api";

export default function Backtest() {
  const [source, setSource] = useState("binance");
  const [costs, setCosts] = useState("nobitex");
  const [lookback, setLookback] = useState(14);
  const [k, setK] = useState(5);
  const [gate, setGate] = useState(true);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true); setErr("");
    try { setRes(await api("/backtest", { body: { source, costs, params: { lookback, k, gate_on: gate } } })); } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  const merged = res ? res.equity.map((e: any, i: number) => ({ day: e.day, strategy: e.equity, btc: res.btc[i]?.equity })) : [];
  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-end gap-3">
        <label className="text-xs">Data<select className="input" value={source} onChange={(e) => setSource(e.target.value)}><option value="binance">Binance research (survivorship-free)</option><option value="nobitex">Nobitex stored candles</option></select></label>
        <label className="text-xs">Costs<select className="input" value={costs} onChange={(e) => setCosts(e.target.value)}><option value="nobitex">Nobitex (fees + position fee)</option><option value="binance">Binance research model</option></select></label>
        <label className="text-xs">Lookback<input className="input w-20" type="number" value={lookback} onChange={(e) => setLookback(+e.target.value)} /></label>
        <label className="text-xs">k<input className="input w-16" type="number" value={k} onChange={(e) => setK(+e.target.value)} /></label>
        <label className="text-xs"><input type="checkbox" checked={gate} onChange={(e) => setGate(e.target.checked)} /> BTC gate</label>
        <button className="btn-primary" disabled={busy} onClick={run}>{busy ? "Running…" : "Run backtest"}</button>
      </div>
      {err && <div className="card text-amber-400">{err}</div>}
      {res && <>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {[["All", res.stats], ["2022+", res.post2021]].map(([n, s]: any) => <div key={n} className="card"><div className="text-xs text-zinc-400">{n}</div>
            <div>CAGR {pct(s.cagr)} · Sharpe {num(s.sharpe)}</div><div>Max DD {pct(s.max_dd)} · Vol {pct(s.vol)}</div></div>)}
          <div className="card col-span-2"><div className="text-xs text-zinc-400">P&L contribution (sum of daily fractions)</div>
            <div className="text-sm">{Object.entries(res.contrib).map(([a, b]: any) => `${a} ${num(b, 3)}`).join(" · ")}</div></div>
        </div>
        <div className="card h-72"><ResponsiveContainer><LineChart data={merged}><CartesianGrid stroke="#27272a" /><XAxis dataKey="day" stroke="#71717a" fontSize={11} /><YAxis scale="log" domain={["auto", "auto"]} stroke="#71717a" fontSize={11} />
          <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46" }} /><Legend /><Line dataKey="strategy" stroke="#34d399" dot={false} /><Line dataKey="btc" stroke="#f59e0b" dot={false} /></LineChart></ResponsiveContainer></div>
        <div className="card"><div className="mb-1 text-sm font-medium">Calendar years</div>{Object.entries(res.years).map(([y, v]: any) => <span key={y} className="mr-4 text-sm">{y}: <span className={v >= 0 ? "text-emerald-400" : "text-red-400"}>{pct(v)}</span></span>)}</div>
      </>}
    </div>
  );
}
