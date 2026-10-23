import { api, num, useApi } from "../api";

export default function Positions() {
  const { data: s } = useApi("/status", 15000);
  const { data: acct, error } = useApi("/account");
  if (!s) return <div className="text-zinc-400">Loading…</div>;
  return (
    <div className="space-y-4">
      <div className="card">
        <table className="w-full"><thead><tr><th>Sleeve</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Price</th><th>Value</th><th>P&L</th></tr></thead>
          <tbody>{s.positions.map((p: any) => (
            <tr key={p.sleeve + p.symbol} className="border-t border-zinc-800">
              <td>{p.sleeve}</td><td>{p.symbol}</td><td className={p.side === "long" ? "text-emerald-400" : "text-red-400"}>{p.side}</td>
              <td>{num(p.qty, 6)}</td><td>{num(p.entry, 6)}</td><td>{num(p.price, 6)}</td><td>{num(p.value)}</td>
              <td className={p.pnl >= 0 ? "text-emerald-400" : "text-red-400"}>{num(p.pnl)}</td></tr>))}
            {s.positions.length === 0 && <tr><td colSpan={8} className="text-zinc-500">No positions – holding USDT.</td></tr>}</tbody></table>
      </div>
      <div className="card">
        <div className="mb-2 text-sm font-medium">Sleeves</div>
        <table className="w-full"><thead><tr><th>Sleeve</th><th>Cash</th><th>Equity</th><th>Last rebalance</th></tr></thead>
          <tbody>{s.sleeves.map((x: any) => <tr key={x.sleeve}><td>{x.sleeve}</td><td>{num(x.cash)}</td><td>{num(x.equity)}</td><td>{s.last_rebalance?.[x.sleeve] ?? "–"}</td></tr>)}</tbody></table>
      </div>
      <div className="card">
        <div className="mb-2 text-sm font-medium">Exchange account / reconciliation</div>
        {error && <div className="text-sm text-amber-400">{error}</div>}
        {acct?.mode === "paper" && <div className="text-sm text-zinc-400">Paper mode – no exchange account.</div>}
        {acct && acct.mode !== "paper" && <>
          <pre className="overflow-auto text-xs text-zinc-300">{JSON.stringify({ spot: acct.spot, margin_usdt_free: acct.margin_usdt_free, shorts: acct.shorts }, null, 2)}</pre>
          <div className={acct.reconcile.length ? "text-red-400" : "text-emerald-400"}>{acct.reconcile.length ? acct.reconcile.join("\n") : "Reconciled: book matches exchange."}</div></>}
      </div>
    </div>
  );
}
