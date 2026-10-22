import { useState } from "react";
import { api, num, pct, useApi } from "../api";

export default function Shadow() {
  const { data, reload } = useApi("/shadow", 60000);
  const [msg, setMsg] = useState("");
  if (!data) return <div className="text-zinc-400">Loading…</div>;
  const apply = async (params: any, label: string) => {
    if (!confirm(`Switch LIVE parameters to ${label}? This changes what the bot trades from the next rebalance.`)) return;
    try { await api("/shadow/apply", { body: params }); setMsg(`Switched to ${label}`); reload(); } catch (e: any) { setMsg(e.message); }
  };
  return (
    <div className="space-y-4">
      <div className="card text-sm text-zinc-300">Live parameters stay frozen (STRATEGY.md §7). The 12 variants run as forward paper books on Nobitex data with Nobitex costs.
        A switch is suggested only after {data.min_days} days and a clear margin over the live setting, and always needs your approval.</div>
      {data.suggestion && <div className="card border-amber-600">
        <div className="font-medium">Suggestion: {data.suggestion.from} → {data.suggestion.to}</div>
        <div className="text-sm text-zinc-400">{data.suggestion.why}</div>
        <button className="btn-primary mt-2" onClick={() => apply(data.suggestion.params, data.suggestion.to)}>Approve switch</button></div>}
      {msg && <div className="text-sm text-emerald-400">{msg}</div>}
      <div className="card"><table className="w-full"><thead><tr><th></th><th>Variant</th><th>Days</th><th>Return</th><th>Sharpe</th><th>Max DD</th><th>Long P&L</th><th>Short P&L</th><th>Fees</th><th>Pos. fees</th><th></th></tr></thead>
        <tbody>{data.rows.map((r: any) => <tr key={r.label} className="border-t border-zinc-800">
          <td>{r.live ? "★" : ""}</td><td>{r.label}</td><td>{r.days}</td><td>{pct(r.return)}</td><td>{num(r.sharpe)}</td><td>{pct(r.max_dd)}</td>
          <td>{num(r.long_pnl)}</td><td>{num(r.short_pnl)}</td><td>{num(r.fees)}</td><td>{num(r.position_fees)}</td>
          <td>{!r.live && <button className="btn" onClick={() => apply(r.params, r.label)}>Use</button>}</td></tr>)}</tbody></table></div>
    </div>
  );
}
