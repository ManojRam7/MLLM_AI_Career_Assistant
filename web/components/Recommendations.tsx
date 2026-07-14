"use client";
import { useState } from "react";
import type { Job } from "@/lib/types";
import { fitColor } from "@/lib/format";

export default function Recommendations({ jobs }: { jobs: Job[] }) {
  const recs = jobs
    .filter((j) => (j.recommendations && j.recommendations.trim()) || (j.cover_text && j.cover_text.trim()))
    .sort((a, b) => (b.fit_score ?? 0) - (a.fit_score ?? 0));
  const [open, setOpen] = useState<string | null>(recs[0]?.dedupe_key ?? null);

  if (!recs.length) {
    return (
      <div className="card p-8 text-center text-slate-400">
        No CV / cover-letter recommendations yet. They are generated for high-fit roles on each run.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {recs.map((j) => {
        const fc = fitColor(j.fit_score);
        const isOpen = open === j.dedupe_key;
        return (
          <div key={j.dedupe_key} className="card overflow-hidden">
            <button onClick={() => setOpen(isOpen ? null : j.dedupe_key)}
              className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-panel2/50">
              <div className="flex items-center gap-3">
                <span className={`pill ${fc.bg} ${fc.text} font-semibold`}>{fc.label}</span>
                <div>
                  <div className="text-sm font-medium text-slate-100">{j.title}</div>
                  <div className="text-xs text-slate-400">{j.company || "—"}</div>
                </div>
              </div>
              <span className="text-slate-400">{isOpen ? "▲" : "▼"}</span>
            </button>
            {isOpen && (
              <div className="px-4 pb-4 grid md:grid-cols-2 gap-4">
                {j.recommendations && (
                  <div>
                    <div className="text-xs uppercase tracking-wide text-brand2 mb-1">Tailoring guidance</div>
                    <pre className="whitespace-pre-wrap text-sm text-slate-200 bg-ink/60 rounded-xl p-3 border border-line">{j.recommendations}</pre>
                  </div>
                )}
                {j.cover_text && (
                  <div>
                    <div className="text-xs uppercase tracking-wide text-brand2 mb-1">Cover letter</div>
                    <pre className="whitespace-pre-wrap text-sm text-slate-200 bg-ink/60 rounded-xl p-3 border border-line">{j.cover_text}</pre>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
