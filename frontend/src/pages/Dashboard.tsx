import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, num, pct, useApi } from "../api";

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return <div className="card"><div className="text-xs text-zinc-400">{label}</div><div className="text-xl font-semibold">{value}</div>{sub && <div className="text-xs text-zinc-500">{sub}</div>}</div>;
}

export default function Dashboard() {
  const { data: s, reload } = useApi("/status", 15000);
  const { data: pv, error: pvErr } = useApi("/preview");
  const { data: ap, reload: reloadAp } = useApi("/approvals?status=pending", 10000);
  const [msg, setMsg] = useState("");
  const act = async (label: string, fn: () => Promise<any>) => {
    setMsg(`${label}…`);
    try { await fn(); setMsg(`${label}: done`); } catch (e: any) { setMsg(`${label}: ${e.message}`); }
    reload(); reloadAp();
  };
  if (!s) return <div className="text-zinc-400">Loading…</div>;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Equity (USDT)" value={num(s.equity)} sub={`capital ${num(s.capital, 0)}`} />
        <Stat label="Drawdown" value={pct(s.drawdown)} sub={`peak ${num(s.peak)}`} />
        <Stat label="BTC gate" value={s.gate == null ? "–" : s.gate ? "ON" : "OFF → cash"} sub={`strategy ${s.label}`} />
        <Stat label="Last run day" value={s.last_run_day ?? "never"} sub={s.next_run ? `next ${s.next_run.slice(0, 16)}` : ""} />
      </div>

      <div className="card flex flex-wrap items-center gap-2">
        <button className="btn-primary" disabled={s.running} onClick={() => act("Run rebalance", () => api("/run", { body: { force: true } }))}>Run rebalance now</button>
        <button className="btn" onClick={() => act("Sync candles", () => api("/sync", { method: "POST", body: {} }))}>Sync candles</button>
        {s.paused ? <button className="btn" onClick={() => act("Resume", () => api("/resume", { method: "POST", body: {} }))}>Resume</button>
          : <button className="btn" onClick={() => act("Pause", () => api("/pause", { method: "POST", body: {} }))}>Pause</button>}
        <button className="btn-danger ml-auto" onClick={() => confirm("Close ALL positions and pause?") && act("Kill", () => api("/kill", { body: { confirm: true } }))}>KILL switch</button>
        <div className="w-full text-xs text-zinc-400">{s.running ? "A run is in progress… " : ""}{msg}{!s.keys_configured && " · API keys not configured in .env"}</div>
      </div>

      {(ap ?? []).map((a: any) => (
        <div key={a.id} className="card border-amber-600">
          <div className="mb-2 font-medium">Pending approval #{a.id} (run #{a.run_id}) – expires {String(a.expires).slice(0, 16)} UTC</div>
          <table className="w-full"><tbody>{a.orders.map((o: any) => (
            <tr key={o.id}><td>#{o.id}</td><td>{o.action}</td><td>{o.symbol}</td><td>{o.amount}</td><td>≈ {num(o.amount * o.signal_price)} USDT</td>
              <td><button className="btn" onClick={() => act("Toggle", () => api(`/approvals/${a.id}/decide`, { body: { order_id: o.id } }))}>
                {(a.decisions?.[String(o.id)] ?? true) ? "✅ include" : "❌ excluded"}</button></td></tr>))}</tbody></table>
          <div className="mt-2 flex gap-2">
            <button className="btn-primary" onClick={() => act("Approve", () => api(`/approvals/${a.id}/decide`, { body: { approve: true, keep_toggles: true } }))}>Approve selection</button>
            <button className="btn-danger" onClick={() => act("Reject", () => api(`/approvals/${a.id}/decide`, { body: { approve: false } }))}>Reject all</button>
          </div>
        </div>
      ))}

      <div className="card">
        <div className="mb-2 text-sm font-medium">Equity</div>
        <div className="h-64">
          <ResponsiveContainer>
            <LineChart data={s.equity_curve}>
              <CartesianGrid stroke="#27272a" /><XAxis dataKey="day" stroke="#71717a" fontSize={11} /><YAxis stroke="#71717a" fontSize={11} domain={["auto", "auto"]} />
              <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46" }} />
              <Line type="monotone" dataKey="equity" stroke="#34d399" dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="card">
        <div className="mb-2 text-sm font-medium">Today's signal preview {pv && `(${pv.day})`}</div>
        {pvErr && <div className="text-sm text-amber-400">{pvErr}</div>}
        {pv && <>
          <div className="mb-2 text-sm text-zinc-400">BTC {num(pv.btc)} vs SMA {num(pv.btc_sma)} · data age {num(pv.data_age_hours, 1)}h</div>
          <div className="grid gap-4 md:grid-cols-2">
            <div><div className="text-xs text-emerald-400">LONG</div>{pv.longs.join(", ") || "–"}</div>
            <div><div className="text-xs text-red-400">SHORT</div>{pv.shorts.join(", ") || "–"}</div>
          </div></>}
      </div>
    </div>
  );
}
