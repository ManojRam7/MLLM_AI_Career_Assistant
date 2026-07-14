import type { Job } from "./types";

export function fitColor(fit: number | null): { bg: string; text: string; label: string } {
  const f = fit ?? -1;
  if (f < 0) return { bg: "bg-slate-700/40", text: "text-slate-300", label: "—" };
  if (f >= 85) return { bg: "bg-emerald-500/20", text: "text-emerald-300", label: String(f) };
  if (f >= 75) return { bg: "bg-sky-500/20", text: "text-sky-300", label: String(f) };
  if (f >= 65) return { bg: "bg-amber-500/20", text: "text-amber-300", label: String(f) };
  return { bg: "bg-rose-500/15", text: "text-rose-300", label: String(f) };
}

export function jobLocation(j: Job): string {
  return (j.locations || j.location || "").trim() || "—";
}

export function shortDate(s: string | null): string {
  if (!s) return "—";
  return s.slice(0, 10);
}

export function categoryLabel(c: string | null): string {
  if (c === "data-science") return "Data Science";
  if (c === "data-analysis") return "Data Analysis";
  return c || "—";
}
