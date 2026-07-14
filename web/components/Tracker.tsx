"use client";
import { useState } from "react";
import type { Job, Stage } from "@/lib/types";
import { TRACKER_STAGES } from "@/lib/types";
import { supabase } from "@/lib/supabase";
import { fitColor } from "@/lib/format";

const STAGE_COLOR: Record<string, string> = {
  "To apply": "from-slate-500/30 to-slate-500/10",
  Applied: "from-indigo-500/30 to-indigo-500/10",
  Assessment: "from-amber-500/30 to-amber-500/10",
  Interview: "from-cyan-500/30 to-cyan-500/10",
  Offer: "from-emerald-500/30 to-emerald-500/10",
  Rejected: "from-rose-500/30 to-rose-500/10",
};

function normStage(status: string | null): Stage {
  const s = (status || "").toLowerCase();
  if (s.includes("offer")) return "Offer";
  if (s.includes("reject")) return "Rejected";
  if (s.includes("interview")) return "Interview";
  if (s.includes("assess")) return "Assessment";
  if (s.includes("appl")) return "Applied";
  return "To apply";
}

export default function Tracker({ jobs, onChange }: { jobs: Job[]; onChange: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const tracked = jobs.filter((j) => j.tracked || j.is_custom);

  async function move(job: Job, stage: Stage) {
    setBusy(job.dedupe_key); setErr(null);
    const { error } = await supabase
      .from("jobs")
      .update({ status: stage, tracked: true })
      .eq("dedupe_key", job.dedupe_key);
    setBusy(null);
    if (error) setErr(`Could not save — check the UPDATE RLS policy. (${error.message})`);
    else onChange();
  }

  if (!tracked.length) {
    return (
      <div className="card p-8 text-center text-slate-400">
        No tracked jobs yet. Open the Jobs tab, and add roles to your tracker.
      </div>
    );
  }

  return (
    <div>
      {err && <div className="card p-3 mb-3 text-sm text-rose-300 border-rose-500/40">{err}</div>}
      <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${TRACKER_STAGES.length}, minmax(190px, 1fr))` }}>
        {TRACKER_STAGES.map((stage) => {
          const cards = tracked.filter((j) => normStage(j.status) === stage);
          return (
            <div key={stage} className="card p-0 overflow-hidden">
              <div className={`px-3 py-2 bg-gradient-to-r ${STAGE_COLOR[stage]} border-b border-line flex items-center justify-between`}>
                <span className="text-sm font-semibold text-slate-100">{stage}</span>
                <span className="pill bg-ink/60 text-slate-300">{cards.length}</span>
              </div>
              <div className="p-2 space-y-2 min-h-[120px] max-h-[68vh] overflow-auto scroll-thin">
                {cards.map((j) => {
                  const fc = fitColor(j.fit_score);
                  return (
                    <div key={j.dedupe_key} className="rounded-xl bg-panel2 border border-line p-2.5">
                      <div className="flex items-start justify-between gap-2">
                        <div className="text-sm font-medium text-slate-100 leading-snug">{j.title}</div>
                        <span className={`pill ${fc.bg} ${fc.text} font-semibold`}>{fc.label}</span>
                      </div>
                      <div className="text-xs text-slate-400 mt-0.5">{j.company || "—"}</div>
                      <div className="flex items-center gap-1 mt-2">
                        <select
                          disabled={busy === j.dedupe_key}
                          value={stage}
                          onChange={(e) => move(j, e.target.value as Stage)}
                          className="flex-1 bg-ink border border-line rounded-lg px-1.5 py-1 text-xs outline-none focus:border-brand"
                        >
                          {TRACKER_STAGES.map((s) => <option key={s} value={s}>{s}</option>)}
                        </select>
                        {j.url && (
                          <a href={j.url} target="_blank" rel="noreferrer"
                            className="pill bg-panel text-slate-300 px-2">↗</a>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
