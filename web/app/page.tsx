"use client";
import { useCallback, useEffect, useState } from "react";
import { supabase, supabaseConfigured } from "@/lib/supabase";
import type { Job } from "@/lib/types";
import Kpis from "@/components/Kpis";
import Charts from "@/components/Charts";
import JobsTable from "@/components/JobsTable";
import Tracker from "@/components/Tracker";
import Recommendations from "@/components/Recommendations";

const TABS = ["Overview", "Jobs", "Tracker", "Recommendations"] as const;
type Tab = (typeof TABS)[number];

const COLS =
  "dedupe_key,title,company,location,locations,source,in_bucket,bucket_tier,category,sector,fit_score,fit_reasoning,seniority,status,url,posted_date,first_seen_at,is_custom,tracked,ghost_flag,recommendations,cover_text";

export default function Home() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [tab, setTab] = useState<Tab>("Overview");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!supabaseConfigured) { setLoading(false); return; }
    setLoading(true); setError(null);
    const { data, error } = await supabase
      .from("jobs")
      .select(COLS)
      .order("fit_score", { ascending: false })
      .limit(5000);
    if (error) setError(error.message);
    else setJobs((data as Job[]) || []);
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  if (!supabaseConfigured) {
    return (
      <Shell tab={tab} setTab={setTab} onRefresh={load} count={0}>
        <div className="card p-8 max-w-xl">
          <h2 className="text-lg font-semibold mb-2">Connect Supabase</h2>
          <p className="text-slate-300 text-sm">
            Create <code className="text-brand2">web/.env.local</code> from{" "}
            <code className="text-brand2">.env.local.example</code> with your project URL and anon key,
            then restart the dev server. See <code className="text-brand2">web/README.md</code>.
          </p>
        </div>
      </Shell>
    );
  }

  return (
    <Shell tab={tab} setTab={setTab} onRefresh={load} count={jobs.filter((j) => !j.is_custom).length}>
      {loading && <div className="text-slate-400 text-sm">Loading jobs…</div>}
      {error && <div className="card p-4 text-rose-300 text-sm border-rose-500/40">Error: {error}</div>}
      {!loading && !error && (
        <>
          {tab === "Overview" && (
            <div className="space-y-4">
              <Kpis jobs={jobs} />
              <Charts jobs={jobs} />
            </div>
          )}
          {tab === "Jobs" && <JobsTable jobs={jobs.filter((j) => !j.is_custom)} />}
          {tab === "Tracker" && <Tracker jobs={jobs} onChange={load} />}
          {tab === "Recommendations" && <Recommendations jobs={jobs} />}
        </>
      )}
    </Shell>
  );
}

function Shell({
  children, tab, setTab, onRefresh, count,
}: {
  children: React.ReactNode; tab: Tab; setTab: (t: Tab) => void; onRefresh: () => void; count: number;
}) {
  return (
    <main className="max-w-7xl mx-auto px-4 py-6">
      <header className="flex flex-wrap items-center justify-between gap-3 mb-5">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-2xl bg-gradient-to-br from-brand to-brand2 grid place-items-center text-white font-bold">JS</div>
          <div>
            <h1 className="text-xl font-semibold leading-tight">Job Search Assistant</h1>
            <p className="text-xs text-slate-400">UK data science & analytics · {count} live roles</p>
          </div>
        </div>
        <button onClick={onRefresh} className="pill bg-panel2 text-slate-200 px-3 py-1.5 hover:bg-panel">
          ↻ Refresh
        </button>
      </header>

      <nav className="flex gap-2 mb-5 flex-wrap">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`tab ${tab === t ? "tab-active" : "tab-idle"}`}>
            {t}
          </button>
        ))}
      </nav>

      {children}
    </main>
  );
}
