import { useEffect, useState } from "react";
import { api, useApi } from "../api";

const PHRASE = "I UNDERSTAND THIS TRADES REAL MONEY";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block text-xs text-zinc-400">{label}{children}</label>;
}

export default function Settings() {
  const { data, reload } = useApi("/settings");
  const [f, setF] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const [typed, setTyped] = useState("");
  useEffect(() => { if (data) setF(structuredClone(data)); }, [data]);
  if (!f) return <div className="text-zinc-400">Loading…</div>;
  const set = (path: string, v: any) => setF((cur: any) => { const n = structuredClone(cur); const p = path.split("."); let o = n; p.slice(0, -1).forEach((k) => (o = o[k])); o[p.at(-1)!] = v; return n; });
  const bind = (path: string, type: "text" | "number" = "text") => ({
    className: "input", type, value: path.split(".").reduce((o, k) => o[k], f) ?? "",
    onChange: (e: any) => set(path, type === "number" ? +e.target.value : e.target.value),
  });
  const tick = (path: string) => <input type="checkbox" checked={!!path.split(".").reduce((o, k) => o[k], f)} onChange={(e) => set(path, e.target.checked)} />;
  const run = async (label: string, fn: () => Promise<any>) => { setMsg(`${label}…`); try { const r = await fn(); setMsg(`${label}: ${JSON.stringify(r)}`); } catch (e: any) { setMsg(`${label}: ${e.message}`); } reload(); };
  const save = () => run("Save", () => api("/settings", { method: "PUT", body: { capital_usdt: f.capital_usdt, nobitex_proxy_url: f.nobitex_proxy_url, telegram: f.telegram, approval: f.approval, risk: f.risk, costs: f.costs, execution: f.execution, schedule: f.schedule, shadow: f.shadow } }));
  const setMode = (mode: string) => run(`Mode → ${mode}`, () => api("/mode", { body: { mode, confirm: typed, reset: mode === "paper" } }));

  return (
    <div className="space-y-4">
      <div className="card space-y-2">
        <div className="text-sm font-medium">Mode</div>
        <div className="text-sm">Current: <b>{f.mode}</b>. Paper → testnet → live; same code path, only the broker changes. Switching resets the bot book (flatten real positions with Kill first). Real modes start paused.</div>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn" onClick={() => setMode("paper")}>Paper</button>
          <button className="btn" onClick={() => setMode("testnet")}>Testnet</button>
          <input className="input w-80" placeholder={`Type: ${PHRASE}`} value={typed} onChange={(e) => setTyped(e.target.value)} />
          <button className="btn-danger" disabled={typed !== PHRASE} onClick={() => setMode("live")}>Go LIVE</button>
          <button className="btn" onClick={() => run("Nobitex test", () => api("/test/nobitex", { body: { mode: f.mode === "paper" ? "live" : f.mode } }))}>Test Nobitex keys</button>
        </div>
      </div>

      <div className="card grid gap-3 md:grid-cols-3">
        <div className="col-span-full text-sm font-medium">Capital & connection</div>
        <Field label="Capital (USDT)"><input {...bind("capital_usdt", "number")} /></Field>
        <Field label="Nobitex proxy (empty = direct)"><input {...bind("nobitex_proxy_url")} /></Field>
      </div>

      <div className="card grid gap-3 md:grid-cols-3">
        <div className="col-span-full text-sm font-medium">Approval per rebalance</div>
        <Field label="Require approval"><div>{tick("approval.enabled")}</div></Field>
        <Field label="Timeout (minutes) – on expiry the run is skipped and retried next cycle"><input {...bind("approval.timeout_minutes", "number")} /></Field>
      </div>

      <div className="card grid gap-3 md:grid-cols-3">
        <div className="col-span-full text-sm font-medium">Telegram (via local proxy)</div>
        <Field label="Enabled"><div>{tick("telegram.enabled")}</div></Field>
        <Field label="Bot token"><input {...bind("telegram.bot_token")} /></Field>
        <Field label="Chat ID (send /start to the bot to see it)"><input {...bind("telegram.chat_id")} /></Field>
        <Field label="Proxy URL (http:// or socks5://)"><input {...bind("telegram.proxy_url")} /></Field>
        <Field label="Daily report"><div>{tick("telegram.daily_report")}</div></Field>
        <Field label="Alerts"><div>{tick("telegram.alerts")}</div></Field>
        <div className="col-span-full flex flex-wrap gap-2">
          <button className="btn" onClick={() => run("Proxy test", () => api("/test/telegram", { body: { proxy_url: f.telegram.proxy_url, bot_token: f.telegram.bot_token } }))}>Test proxy + token</button>
          <button className="btn" onClick={() => run("Test message", () => api("/test/telegram-message", { method: "POST", body: {} }))}>Send test message</button>
        </div>
      </div>

      <div className="card grid gap-3 md:grid-cols-3">
        <div className="col-span-full text-sm font-medium">Risk limits</div>
        <Field label="Kill drawdown (0.25 = −25%)"><input {...bind("risk.kill_drawdown", "number")} /></Field>
        <Field label="Max per coin (fraction of sleeve)"><input {...bind("risk.max_coin_frac_of_sleeve", "number")} /></Field>
        <Field label="Short leverage (keep 1)"><input {...bind("risk.short_leverage", "number")} /></Field>
        <Field label="Max slippage for liquidity filter"><input {...bind("risk.max_slippage", "number")} /></Field>
        <Field label="Min order (USDT)"><input {...bind("risk.min_order_usdt", "number")} /></Field>
        <Field label="Stale data limit (hours)"><input {...bind("risk.stale_data_hours", "number")} /></Field>
      </div>

      <div className="card grid gap-3 md:grid-cols-3">
        <div className="col-span-full text-sm font-medium">Costs model & execution</div>
        <Field label="Spot taker fee"><input {...bind("costs.spot_taker_fee", "number")} /></Field>
        <Field label="Margin taker fee"><input {...bind("costs.margin_taker_fee", "number")} /></Field>
        <Field label="Slippage"><input {...bind("costs.slippage", "number")} /></Field>
        <Field label="Position fee / day (fallback)"><input {...bind("costs.position_fee_daily", "number")} /></Field>
        <Field label="Limit offset"><input {...bind("execution.limit_offset", "number")} /></Field>
        <Field label="Limit retries before market"><input {...bind("execution.limit_retries", "number")} /></Field>
        <Field label="Run time UTC (hour)"><input {...bind("schedule.run_hour_utc", "number")} /></Field>
        <Field label="Run time UTC (minute)"><input {...bind("schedule.run_minute_utc", "number")} /></Field>
        <Field label="Catch-up window (hours)"><input {...bind("schedule.catchup_max_hours", "number")} /></Field>
      </div>

      <div className="flex items-center gap-3"><button className="btn-primary" onClick={save}>Save settings</button><span className="break-all text-xs text-zinc-400">{msg}</span></div>
      <div className="card text-xs text-zinc-400">Strategy parameters are frozen (L{f.strategy.lookback}, k={f.strategy.k}, gate {f.strategy.gate_on ? "on" : "off"}); change them only via an approved Shadow switch. API keys live in the project-root <code>.env</code>, never in this panel.</div>
    </div>
  );
}
