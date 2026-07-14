"use client";
import { useMemo, useState } from "react";
import type { Job } from "@/lib/types";
import { categoryLabel, fitColor, jobLocation, shortDate } from "@/lib/format";

type ColKey = "fit" | "title" | "company" | "category" | "sector" | "location" | "source" | "added";
type Kind = "num" | "text" | "enum";

const COLUMNS: { key: ColKey; label: string; kind: Kind; width?: string }[] = [
  { key: "fit", label: "Fit", kind: "num", width: "w-16" },
  { key: "title", label: "Title", kind: "text" },
  { key: "company", label: "Company", kind: "text", width: "w-40" },
  { key: "category", label: "Category", kind: "enum", width: "w-32" },
  { key: "sector", label: "Sector", kind: "enum", width: "w-44" },
  { key: "location", label: "Location", kind: "text", width: "w-44" },
  { key: "source", label: "Source", kind: "enum", width: "w-36" },
  { key: "added", label: "Added", kind: "num", width: "w-24" },
];

function valueOf(j: Job, key: ColKey): string {
  switch (key) {
    case "fit": return String(j.fit_score ?? "");
    case "title": return j.title || "";
    case "company": return j.company || "";
    case "category": return categoryLabel(j.category);
    case "sector": return j.sector || "— other —";
    case "location": return jobLocation(j);
    case "source": return j.source || "—";
    case "added": return j.first_seen_at || "";
    default: return "";
  }
}

export default function JobsTable({ jobs }: { jobs: Job[] }) {
  const [text, setText] = useState<Record<string, string>>({});
  const [enums, setEnums] = useState<Record<string, Set<string>>>({});
  const [minFit, setMinFit] = useState(0);
  const [sort, setSort] = useState<{ col: ColKey; dir: "asc" | "desc" }>({ col: "fit", dir: "desc" });
  const [openCol, setOpenCol] = useState<ColKey | null>(null);

  const distinct = useMemo(() => {
    const d: Record<string, string[]> = {};
    for (const c of COLUMNS) if (c.kind === "enum") {
      d[c.key] = Array.from(new Set(jobs.map((j) => valueOf(j, c.key)))).sort();
    }
    return d;
  }, [jobs]);

  const rows = useMemo(() => {
    let out = jobs.filter((j) => {
      if ((j.fit_score ?? 0) < minFit) return false;
      for (const c of COLUMNS) {
        const v = valueOf(j, c.key);
        if (c.kind === "text") {
          const q = (text[c.key] || "").trim().toLowerCase();
          if (q && !v.toLowerCase().includes(q)) return false;
        } else if (c.kind === "enum") {
          const sel = enums[c.key];
          if (sel && sel.size && !sel.has(v)) return false;
        }
      }
      return true;
    });
    const { col, dir } = sort;
    out = [...out].sort((a, b) => {
      const av = valueOf(a, col), bv = valueOf(b, col);
      let cmp: number;
      if (col === "fit") cmp = (a.fit_score ?? -1) - (b.fit_score ?? -1);
      else cmp = av.localeCompare(bv);
      return dir === "asc" ? cmp : -cmp;
    });
    return out;
  }, [jobs, text, enums, minFit, sort]);

  const toggleEnum = (col: ColKey, val: string) => {
    setEnums((prev) => {
      const next = new Set(prev[col] || []);
      next.has(val) ? next.delete(val) : next.add(val);
      return { ...prev, [col]: next };
    });
  };

  const activeCount = (col: ColKey, kind: Kind) =>
    kind === "enum" ? (enums[col]?.size || 0) : kind === "text" ? ((text[col] || "").trim() ? 1 : 0)
      : (col === "fit" && minFit > 0 ? 1 : 0);

  const clearAll = () => { setText({}); setEnums({}); setMinFit(0); };

  return (
    <div className="card p-0 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-line">
        <div className="text-sm text-slate-300">
          <span className="font-semibold text-white">{rows.length}</span> of {jobs.length} jobs
        </div>
        <button onClick={clearAll} className="text-xs text-slate-300 hover:text-white pill bg-panel2">
          Clear all filters
        </button>
      </div>

      <div className="overflow-auto scroll-thin" style={{ maxHeight: "70vh" }}>
        <table className="grid w-full text-sm">
          <thead className="sticky top-0 z-10 bg-panel2/95 backdrop-blur">
            <tr>
              {COLUMNS.map((c) => {
                const n = activeCount(c.key, c.kind);
                const sorted = sort.col === c.key;
                return (
                  <th key={c.key} className={`relative px-3 py-2 ${c.width || ""} whitespace-nowrap`}>
                    <div className="flex items-center gap-1">
                      <button
                        className="font-semibold text-slate-200 hover:text-white"
                        onClick={() =>
                          setSort((s) => ({ col: c.key, dir: s.col === c.key && s.dir === "desc" ? "asc" : "desc" }))
                        }
                      >
                        {c.label}{sorted ? (sort.dir === "desc" ? " ↓" : " ↑") : ""}
                      </button>
                      <button
                        className={`pill ${n ? "bg-brand text-white" : "bg-panel text-slate-400"} px-1.5`}
                        onClick={() => setOpenCol(openCol === c.key ? null : c.key)}
                        title="Filter"
                      >
                        ⌄{n ? ` ${n}` : ""}
                      </button>
                    </div>

                    {openCol === c.key && (
                      <div className="absolute mt-2 w-56 card p-3 z-20 font-normal">
                        {c.kind === "text" && (
                          <input
                            autoFocus
                            value={text[c.key] || ""}
                            onChange={(e) => setText({ ...text, [c.key]: e.target.value })}
                            placeholder={`Search ${c.label.toLowerCase()}…`}
                            className="w-full bg-ink border border-line rounded-lg px-2 py-1.5 text-sm outline-none focus:border-brand"
                          />
                        )}
                        {c.kind === "num" && c.key === "fit" && (
                          <div>
                            <div className="flex justify-between text-xs text-slate-400 mb-1">
                              <span>Min fit</span><span className="text-brand2 font-semibold">{minFit}</span>
                            </div>
                            <input type="range" min={0} max={100} step={5} value={minFit}
                              onChange={(e) => setMinFit(Number(e.target.value))} className="w-full accent-indigo-500" />
                          </div>
                        )}
                        {c.kind === "num" && c.key === "added" && (
                          <div className="text-xs text-slate-400">Use the ↑/↓ on the header to sort by date.</div>
                        )}
                        {c.kind === "enum" && (
                          <div className="max-h-52 overflow-auto scroll-thin space-y-1">
                            {distinct[c.key]?.map((v) => (
                              <label key={v} className="flex items-center gap-2 text-sm text-slate-200 cursor-pointer">
                                <input type="checkbox" checked={enums[c.key]?.has(v) || false}
                                  onChange={() => toggleEnum(c.key, v)} className="accent-indigo-500" />
                                <span className="truncate">{v}</span>
                              </label>
                            ))}
                          </div>
                        )}
                        <button onClick={() => setOpenCol(null)}
                          className="mt-3 w-full pill bg-panel2 text-slate-300 justify-center py-1">Done</button>
                      </div>
                    )}
                  </th>
                );
              })}
              <th className="px-3 py-2 w-12" />
            </tr>
          </thead>
          <tbody>
            {rows.map((j) => {
              const fc = fitColor(j.fit_score);
              return (
                <tr key={j.dedupe_key} className="hover:bg-panel2/50">
                  <td className="px-3 py-2">
                    <span className={`pill ${fc.bg} ${fc.text} font-semibold justify-center w-9`}>{fc.label}</span>
                  </td>
                  <td className="px-3 py-2 text-slate-100">
                    {j.title}
                    {j.ghost_flag && <span className="ml-1 pill bg-rose-500/15 text-rose-300">ghost</span>}
                  </td>
                  <td className="px-3 py-2 text-slate-200">{j.company || "—"}</td>
                  <td className="px-3 py-2">
                    <span className={`pill ${j.category === "data-science" ? "bg-indigo-500/20 text-indigo-300" : "bg-cyan-500/20 text-cyan-300"}`}>
                      {categoryLabel(j.category)}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-slate-300">{j.sector || "— other —"}</td>
                  <td className="px-3 py-2 text-slate-300">{jobLocation(j)}</td>
                  <td className="px-3 py-2 text-slate-400">{(j.source || "—").replace(/\s*\(.*\)/, "")}</td>
                  <td className="px-3 py-2 text-slate-400">{shortDate(j.first_seen_at)}</td>
                  <td className="px-3 py-2">
                    {j.url && (
                      <a href={j.url} target="_blank" rel="noreferrer"
                        className="pill bg-gradient-to-r from-brand to-brand2 text-white px-2">open ↗</a>
                    )}
                  </td>
                </tr>
              );
            })}
            {rows.length === 0 && (
              <tr><td colSpan={COLUMNS.length + 1} className="px-4 py-10 text-center text-slate-400">
                No jobs match these filters.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
