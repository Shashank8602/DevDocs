import { useQuery } from "@tanstack/react-query";

const BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

interface Health { postgres: string; elasticsearch: string }

async function fetchHealth(): Promise<Health> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 4000);
  try {
    const res = await fetch(`${BASE}/health`, { signal: ctl.signal, cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

/** Header indicator: green when the API, Postgres and Elasticsearch answer; red (with the reason) otherwise. */
export default function ServerStatus() {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 5000,
    retry: false,
    refetchIntervalInBackground: true,
  });

  let ok = false;
  let text = "Checking…";
  let detail = "";
  if (isError) {
    text = "Offline";
    detail = "The DevDocs backend isn't responding. Start it (uvicorn) and check Docker.";
  } else if (data) {
    const down = [data.postgres !== "ok" && "Postgres", data.elasticsearch !== "ok" && "Elasticsearch"].filter(Boolean);
    ok = down.length === 0;
    text = ok ? "Online" : "Degraded";
    detail = ok ? "Backend, Postgres and Elasticsearch are reachable." : `${down.join(" and ")} isn't reachable. Check Docker.`;
  }
  const tone = !data && !isError ? "bg-slate-400" : ok ? "bg-emerald-500" : "bg-red-500";
  const textTone = !data && !isError ? "text-slate-500" : ok ? "text-emerald-700" : "text-red-600";
  return (
    <span
      role="status"
      title={detail}
      className={`flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs font-medium ${textTone} ${
        ok || (!data && !isError) ? "border-slate-200" : "border-red-500/40 bg-red-500/10"
      }`}
    >
      <span className="relative flex h-2 w-2">
        {ok && <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${tone}`} />}
        <span className={`relative inline-flex h-2 w-2 rounded-full ${tone}`} />
      </span>
      <span className="hidden sm:inline">{text}</span>
    </span>
  );
}
