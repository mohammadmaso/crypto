import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import "./index.css";
import { useApi } from "./api";
import Dashboard from "./pages/Dashboard";
import Positions from "./pages/Positions";
import Orders from "./pages/Orders";
import Shadow from "./pages/Shadow";
import Backtest from "./pages/Backtest";
import Logs from "./pages/Logs";
import Settings from "./pages/Settings";

const links: [string, string][] = [["/", "Dashboard"], ["/positions", "Positions"], ["/orders", "Orders"],
  ["/shadow", "Shadow"], ["/backtest", "Backtest"], ["/logs", "Logs"], ["/settings", "Settings"]];

function Shell() {
  const { data: s } = useApi("/status", 15000);
  const modeColor = s?.mode === "live" ? "bg-red-600" : s?.mode === "testnet" ? "bg-amber-600" : "bg-sky-700";
  return (
    <div className="mx-auto max-w-6xl p-4">
      <header className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">NobiMomentum</h1>
        {s && <span className={`rounded px-2 py-0.5 text-xs font-bold uppercase ${modeColor}`}>{s.mode}</span>}
        {s?.paused && <span className="rounded bg-zinc-700 px-2 py-0.5 text-xs">PAUSED</span>}
        <nav className="ml-auto flex flex-wrap gap-1">
          {links.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/"}
              className={({ isActive }) => `rounded px-3 py-1 text-sm ${isActive ? "bg-zinc-700" : "hover:bg-zinc-800"}`}>{label}</NavLink>
          ))}
        </nav>
      </header>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/positions" element={<Positions />} />
        <Route path="/orders" element={<Orders />} />
        <Route path="/shadow" element={<Shadow />} />
        <Route path="/backtest" element={<Backtest />} />
        <Route path="/logs" element={<Logs />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><BrowserRouter><Shell /></BrowserRouter></React.StrictMode>);
