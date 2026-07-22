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

## 3. Deploy free on GitHub Pages

The repo already includes `.github/workflows/pages.yml`, which builds this app into a
static site and publishes it. To turn it on:

1. **Add two repo secrets** — Settings → Secrets and variables → Actions → *New repository secret*:
   - `NEXT_PUBLIC_SUPABASE_URL`
   - `NEXT_PUBLIC_SUPABASE_ANON_KEY`
2. **Set the Pages source to Actions** — Settings → Pages → *Build and deployment* →
   Source = **GitHub Actions** (not "Deploy from a branch"). This is the fix for the
   README-showing problem: "deploy from a branch" just renders your README.
3. Push to `main` (or run the **deploy-web-to-pages** workflow manually). It builds
   `web/` and publishes to `https://<user>.github.io/MLLM_AI_Career_Assistant/`.

The base path (`/MLLM_AI_Career_Assistant`) is already set in the workflow. If you rename
the repo, update `PAGES_BASE_PATH` in `.github/workflows/pages.yml`.

### ⚠️ Security note (public site)
A GitHub Pages site is **public**, and the anon key is embedded in the page. With RLS
`select using(true)` anyone with the URL can *read* your jobs; with `update ... using(true)`
anyone could change your tracker. Options:
- **Recommended for a public URL:** add only the **select** policy (read-only dashboard).
  The Tracker will still load and just show a save error if someone edits — use the tracker
  from `npm run dev` locally, or
- Add **Supabase Auth** (email magic-link) and scope the update policy to your user, so only
  you can edit — best of both. Ask and I'll wire it in.

(Alternatively, Vercel gives the same result with a private-by-default URL and zero base-path
setup — import the repo, set Root Directory = `web`, add the two env vars.)

## Notes
- The pipeline (Python) is unchanged — it keeps writing to Supabase on its schedule.
- Data is cached by the browser between tab switches; hit **↻ Refresh** for the latest.
- Tracker/manual jobs (`is_custom`/`tracked`) show only in the Tracker tab.
