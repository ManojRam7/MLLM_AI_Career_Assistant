"use client";
import type { Job } from "@/lib/types";

function Stat({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent: string }) {
  return (
    <div className="card p-4">
      <div className="text-xs uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`mt-1 text-3xl font-semibold ${accent}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-400">{sub}</div>}
    </div>
  );
}

export default function Kpis({ jobs }: { jobs: Job[] }) {
  const real = jobs.filter((j) => !j.is_custom);
  const scored = real.filter((j) => (j.fit_score ?? 0) > 0);
  const avg = scored.length
    ? Math.round(scored.reduce((a, j) => a + (j.fit_score || 0), 0) / scored.length)
    : 0;
  const strong = real.filter((j) => (j.fit_score ?? 0) >= 85).length;
  const fresh = real.filter((j) => {
    const t = j.first_seen_at ? Date.parse(j.first_seen_at) : 0;
    return t && Date.now() - t < 36 * 3600 * 1000;
  }).length;

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <Stat label="Total jobs" value={String(real.length)} sub={`${scored.length} scored`} accent="text-white" />
      <Stat label="Avg fit" value={String(avg)} sub="of scored roles" accent="text-brand2" />
      <Stat label="Strong (85+)" value={String(strong)} sub="top matches" accent="text-emerald-300" />
      <Stat label="New (36h)" value={String(fresh)} sub="just added" accent="text-amber-300" />
    </div>
  );
}
