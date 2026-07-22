"use client";
import {
  Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { Job } from "@/lib/types";
import { categoryLabel } from "@/lib/format";

const PALETTE = ["#6366f1", "#22d3ee", "#f59e0b", "#34d399", "#f472b6", "#a78bfa", "#fb7185", "#38bdf8"];

function countBy(jobs: Job[], key: (j: Job) => string) {
  const m = new Map<string, number>();
  for (const j of jobs) {
    const k = key(j) || "—";
    m.set(k, (m.get(k) || 0) + 1);
  }
  return Array.from(m, ([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value);
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card p-4">
      <div className="mb-2 text-sm font-semibold text-slate-200">{title}</div>
      <div style={{ height: 240 }}>{children}</div>
    </div>
  );
}

const tip = {
  contentStyle: { background: "#141a2e", border: "1px solid #2a3358", borderRadius: 12, color: "#e6e9f5" },
};

export default function Charts({ jobs }: { jobs: Job[] }) {
  const real = jobs.filter((j) => !j.is_custom);
  const bySector = countBy(real, (j) => j.sector || "— other —");
  const bySource = countBy(real, (j) => (j.source || "—").replace(/\s*\(.*\)/, ""));
  const byCat = countBy(real, (j) => categoryLabel(j.category));

  const fitBuckets = [
    { name: "85+", value: real.filter((j) => (j.fit_score ?? 0) >= 85).length },
    { name: "75–84", value: real.filter((j) => (j.fit_score ?? 0) >= 75 && (j.fit_score ?? 0) < 85).length },
    { name: "65–74", value: real.filter((j) => (j.fit_score ?? 0) >= 65 && (j.fit_score ?? 0) < 75).length },
    { name: "<65", value: real.filter((j) => (j.fit_score ?? 0) > 0 && (j.fit_score ?? 0) < 65).length },
  ];

  return (
    <div className="grid md:grid-cols-2 gap-3">
      <Panel title="By sector">
        <ResponsiveContainer>
          <BarChart data={bySector} layout="vertical" margin={{ left: 10, right: 20 }}>
            <XAxis type="number" stroke="#64748b" fontSize={11} />
            <YAxis type="category" dataKey="name" width={130} stroke="#94a3b8" fontSize={11} />
            <Tooltip {...tip} cursor={{ fill: "rgba(255,255,255,0.04)" }} />
            <Bar dataKey="value" radius={[0, 6, 6, 0]}>
              {bySector.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Panel>

      <Panel title="By source">
        <ResponsiveContainer>
          <BarChart data={bySource} margin={{ left: 0, right: 10 }}>
            <XAxis dataKey="name" stroke="#94a3b8" fontSize={11} />
            <YAxis stroke="#64748b" fontSize={11} />
            <Tooltip {...tip} cursor={{ fill: "rgba(255,255,255,0.04)" }} />
            <Bar dataKey="value" radius={[6, 6, 0, 0]}>
              {bySource.map((_, i) => <Cell key={i} fill={PALETTE[(i + 2) % PALETTE.length]} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Panel>

      <Panel title="Fit distribution">
        <ResponsiveContainer>
          <BarChart data={fitBuckets} margin={{ left: 0, right: 10 }}>
            <XAxis dataKey="name" stroke="#94a3b8" fontSize={11} />
            <YAxis stroke="#64748b" fontSize={11} />
            <Tooltip {...tip} cursor={{ fill: "rgba(255,255,255,0.04)" }} />
            <Bar dataKey="value" radius={[6, 6, 0, 0]}>
              {["#34d399", "#38bdf8", "#f59e0b", "#fb7185"].map((c, i) => <Cell key={i} fill={c} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Panel>

      <Panel title="By category (DS · AI · DA)">
        <ResponsiveContainer>
          <PieChart>
            <Pie data={byCat} dataKey="value" nameKey="name" innerRadius={55} outerRadius={95} paddingAngle={3}>
              {byCat.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
            </Pie>
            <Tooltip {...tip} />
          </PieChart>
        </ResponsiveContainer>
      </Panel>
    </div>
  );
}
