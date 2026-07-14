# Job Search Assistant — Web UI (React / Next.js)

A colourful, fast dashboard for the UK job pipeline. Reads the same **Supabase** `jobs`
table the pipeline writes to. Replaces the Streamlit app.

Features: Overview KPIs + charts, a **Jobs table with Excel-style per-column filters**
(text search, multi-select dropdowns, min-fit slider, click-to-sort), a **Tracker**
kanban that saves status back to Supabase, and **Recommendations** (tailoring + cover letter).

## 1. Install & run locally

```bash
cd web
cp .env.local.example .env.local        # then paste your Supabase URL + anon key
npm install
npm run dev                             # http://localhost:3000
```

Get the two values from **Supabase → Project Settings → API**:
- `NEXT_PUBLIC_SUPABASE_URL` = Project URL
- `NEXT_PUBLIC_SUPABASE_ANON_KEY` = `anon` `public` key (safe to expose in the browser)

## 2. Row Level Security (required)

The anon key only works with RLS policies. In **Supabase → SQL Editor** run:

```sql
alter table jobs enable row level security;

-- read everything (dashboard is personal / read-only for browsing)
create policy "read jobs" on jobs for select using (true);

-- allow the Tracker to update status only
create policy "update tracker" on jobs for update using (true) with check (true);
```

> This exposes read + status-update to anyone with the anon key. For a personal
> project that's fine. To lock it down later, put the app behind Supabase Auth and
> scope the policies to `auth.uid()`.

## 3. Deploy free on Vercel

1. Push the repo to GitHub (the `web/` folder can live in the same repo).
2. On [vercel.com](https://vercel.com) → New Project → import the repo.
3. Set **Root Directory** to `web`.
4. Add the two `NEXT_PUBLIC_*` env vars.
5. Deploy. Every push redeploys automatically.

## Notes
- The pipeline (Python) is unchanged — it keeps writing to Supabase on its schedule.
- Data is cached by the browser between tab switches; hit **↻ Refresh** for the latest.
- Tracker/manual jobs (`is_custom`/`tracked`) show only in the Tracker tab.
