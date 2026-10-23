import { useApi } from "../api";

const color: Record<string, string> = { critical: "text-red-400", warning: "text-amber-400", info: "text-zinc-300" };

export default function Logs() {
  const { data } = useApi("/alerts?limit=200", 20000);
  return (
    <div className="card"><table className="w-full"><thead><tr><th>Time (UTC)</th><th>Level</th><th>Kind</th><th>Message</th></tr></thead>
      <tbody>{(data ?? []).map((a: any) => <tr key={a.id} className="border-t border-zinc-800 align-top"><td className="whitespace-nowrap">{String(a.created).slice(0, 19).replace("T", " ")}</td>
        <td className={color[a.level]}>{a.level}</td><td>{a.kind}</td><td className="whitespace-pre-wrap">{a.message}</td></tr>)}</tbody></table></div>
  );
}
