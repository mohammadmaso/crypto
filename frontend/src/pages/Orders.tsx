import { num, useApi } from "../api";

export default function Orders() {
  const { data: runs } = useApi("/runs", 20000);
  const { data: orders } = useApi("/orders", 20000);
  return (
    <div className="space-y-4">
      <div className="card"><div className="mb-2 text-sm font-medium">Runs</div>
        <table className="w-full"><thead><tr><th>#</th><th>Created</th><th>Day</th><th>Mode</th><th>Kind</th><th>Status</th><th>Gate</th><th>Detail</th></tr></thead>
          <tbody>{(runs ?? []).map((r: any) => <tr key={r.id} className="border-t border-zinc-800"><td>{r.id}</td><td>{String(r.created).slice(0, 16)}</td><td>{r.day}</td>
            <td>{r.mode}</td><td>{r.kind}</td><td>{r.status}</td><td>{r.summary?.gate == null ? "–" : r.summary.gate ? "on" : "off"}</td><td className="text-xs text-zinc-400">{r.summary?.reason ?? ""}</td></tr>)}</tbody></table></div>
      <div className="card"><div className="mb-2 text-sm font-medium">Orders</div>
        <table className="w-full"><thead><tr><th>#</th><th>Run</th><th>Action</th><th>Symbol</th><th>Amount</th><th>Signal px</th><th>Fill px</th><th>Filled</th><th>Fee</th><th>Status</th></tr></thead>
          <tbody>{(orders ?? []).map((o: any) => <tr key={o.id} className="border-t border-zinc-800" title={o.error ?? ""}><td>{o.id}</td><td>{o.run_id}</td><td>{o.action}</td><td>{o.symbol}</td>
            <td>{num(o.amount, 8)}</td><td>{num(o.signal_price, 6)}</td><td>{num(o.avg_price, 6)}</td><td>{num(o.filled, 8)}</td><td>{num(o.fee, 4)}</td><td>{o.status}</td></tr>)}</tbody></table></div>
    </div>
  );
}
