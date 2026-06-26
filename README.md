# uk-jobops

Automated UK data-science job search. It discovers Data Scientist / ML roles from
multiple legitimate job APIs every few hours, scores each one against your real CV,
auto-tailors an ATS-safe CV and cover letter for the strong matches, stores everything
in a free cloud database, and gives you a dashboard with an editable application tracker.

Built to run for **£0** on free API tiers and GitHub Actions.

```
                ┌──────────── every 6 hours (GitHub Actions cron) ────────────┐
                │                                                             │
   Reed API ─┐  │  normalise → filter (IC-level DS only) → fuzzy de-dupe →    │
 Adzuna API ─┼──┤  bucket-list boost → store (Supabase) → LLM fit-score →     │
 ATS boards ─┘  │  tailor CV + cover letter (≥ threshold) → digest / Telegram │
                │                                                             │
                └──────────────────────────┬──────────────────────────────────┘
                                            │
                          Streamlit dashboard reads the same DB:
                  Overview · Pipeline & LLMs · Job tracker · Tailored CVs · Add a job
```

---

## What it does

1. **Discovers** roles from sources that allow programmatic access (no ToS-violating scraping of LinkedIn/Indeed/Google).
2. **Filters** to individual-contributor data-science roles and drops senior/lead/manager titles.
3. **De-duplicates** the same job across sources with fuzzy matching.
4. **Boosts** jobs whose employer is on your bucket list of ~640 target UK companies, so they reach the front of the queue.
5. **Scores** each job 0-100 for fit against your real experience, and flags likely "ghost" listings.
6. **Tailors** a truthful, ATS-passing CV and cover letter for the strong matches, then validates them against a deterministic rulebook (no em-dashes, no AI-trace clichés, UK spelling, keyword coverage).
7. **Stores** every job, score and document in Supabase (free Postgres) so nothing is lost between runs.
8. **Tracks** applications in an editable dashboard you can run locally or deploy free.

---

## Project structure

```
uk-jobops/
├── app/
│   └── dashboard.py              # Streamlit dashboard (overview, runs, tracker, CV downloads, add-job)
├── config/
│   ├── settings.yaml             # all tunable settings: search, filters, scoring caps, sources, llm
│   └── rulebook.md               # the CV-tailoring system prompt (rules the model MUST follow)
├── data/
│   └── companies_bucketlist.csv  # ~640 target UK employers: company_name, sector, careers_url
├── src/uk_jobops/
│   ├── config.py                 # load settings.yaml + .env secrets + base CV
│   ├── models.py                 # Job, FitResult, TailoredCV dataclasses
│   ├── bucketlist.py             # load + match target companies (the priority boost)
│   ├── normalize.py              # clean titles / locations / dates
│   ├── filtering.py              # keep IC-level DS roles, drop senior/lead/principal/manager
│   ├── dedupe.py                 # fuzzy cross-source de-duplication (rapidfuzz)
│   ├── db.py                     # Supabase/Postgres store + tracker + pipeline-run history
│   ├── notify.py                 # writes a digest file + optional Telegram message
│   ├── pipeline.py               # orchestrates the whole run end to end
│   ├── sources/
│   │   ├── base.py               # Source interface + FetchResult
│   │   ├── reed.py               # Reed API (free)
│   │   ├── adzuna.py             # Adzuna API (free; aggregates Indeed/Totaljobs/CV-Library/Glassdoor)
│   │   └── ats.py                # Greenhouse / Lever / Ashby job boards for bucket-list companies
│   ├── llm/
│   │   ├── client.py             # multi-provider client (Gemini primary; Groq only if you opt in)
│   │   ├── fit_score.py          # 0-100 fit score + ghost-job flag + skill gaps
│   │   ├── tailor.py             # generate tailored CV + cover letter as JSON (shape-coercion safe)
│   │   └── validator.py          # deterministic ATS / anti-AI-trace / UK-spelling checks
│   └── cv/
│       ├── base_cv.json          # YOUR real experience - the single source of truth
│       └── render_docx.py        # render an ATS-safe single-column A4 .docx CV + cover letter
├── scripts/
│   ├── init_db.py                # create the database tables
│   ├── run_pipeline.py           # run discovery → score → tailor   (--mode first | recurring)
│   ├── add_job.py                # add a custom job from the command line
│   └── smoke_test.py             # doctor: checks keys, tiny discovery, one tailor, DB connect
├── tests/test_core.py            # offline regression tests (filter, dedupe, coercion, validator)
├── .github/workflows/pipeline.yml# scheduled run, every 6 hours
├── requirements.txt              # pipeline dependencies
├── requirements-dashboard.txt    # the above + streamlit + pandas
├── .env.example                  # template for your API keys
├── pyproject.toml                # src layout + pytest config
└── conftest.py                   # puts src/ on the path for tests
```

---

## The dashboard

Run it against the same database the pipeline writes to:

```bash
pip install -r requirements-dashboard.txt
streamlit run app/dashboard.py
```

Five tabs:

- **Overview** — totals, bucket-list count, status funnel, jobs by source, fit-score distribution, top matches.
- **Pipeline & LLMs** — which model is primary, the critic on/off, request pacing, the per-run caps, a live green/grey panel of which API keys are connected, and a table + chart of recent pipeline runs.
- **Job tracker** — filter by status / source / bucket-list / fit, then edit **status** and **notes** inline and save them back to the database. This is your application tracker.
- **Tailored CVs** — every job that has a tailored CV, with download buttons for the `.docx` CV and cover letter (when run on the machine that generated them).
- **Add a job** — paste a role you found yourself; it enters the tailoring queue immediately.

**Deploy free:** push to GitHub, point [Streamlit Community Cloud](https://streamlit.io/cloud) at `app/dashboard.py`, and add `SUPABASE_DB_URL` in the app's secrets. The dashboard then works from any browser.

---

## Setup

```bash
git clone <your-private-repo> uk-jobops && cd uk-jobops
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in the keys below
python scripts/init_db.py     # create tables in Supabase
python scripts/smoke_test.py  # confirm keys, discovery, tailoring, DB all work
python scripts/run_pipeline.py --mode first   # 2-week backfill on the first run
```

### API keys (all have free tiers) — put them in `.env`

| Variable | Used for | Free? | Where |
|---|---|---|---|
| `REED_API_KEY` | Reed job search | Yes | reed.co.uk/developers |
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | Adzuna (aggregates Indeed/Totaljobs/CV-Library/Glassdoor) | Yes | developer.adzuna.com |
| `GEMINI_API_KEY` | Fit-scoring + CV tailoring (primary) | Yes | aistudio.google.com/apikey |
| `GROQ_API_KEY` | Optional second LLM (off by default) | Yes | console.groq.com |
| `SUPABASE_DB_URL` | Cloud Postgres storage | Yes | supabase.com → project → connection string |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Optional push digest | Yes | @BotFather |

Keys live only in `.env` (git-ignored) locally and in **GitHub → Settings → Secrets** for the scheduled run. They are never printed, committed, or sent anywhere except the provider's own API.

---

## Scheduling

`.github/workflows/pipeline.yml` runs `python scripts/run_pipeline.py --mode recurring` on a `0 */6 * * *` cron (every 6 hours). Add the same keys as repository secrets and enable Actions. Each run appends to the `pipeline_runs` history table, so the dashboard always shows what happened.

---

## Free-tier strategy (why it never bills you)

The LLM is the only thing with meaningful limits, so the pipeline is built to stay inside Gemini's free tier and resume gracefully if it ever hits a wall:

- **Gemini-only by default.** The critic step is off and the fallback chain contains only configured providers, so a brief Gemini rate-limit never spills over and burns Groq's small daily cap.
- **Per-run caps** (`max_score_per_run`, `max_tailor_per_run` in `settings.yaml`) bound the work each run does; the backlog clears over successive runs.
- **Pacing.** `request_delay_seconds: 4.0` keeps calls under Gemini Flash's ~15 requests/minute free limit.
- **Tailor only the strong ones.** A CV is tailored only when fit ≥ `tailor_threshold` (default 70), so tokens go to jobs worth applying to.
- **Resilient.** A `429`/quota error stops the LLM phase cleanly, records a note, and the next scheduled run picks up where it left off. Jobs are already stored, so nothing is lost.

All knobs are in `config/settings.yaml`.

---

## CV tailoring rules

Tailoring is grounded in `src/uk_jobops/cv/base_cv.json` (your true history) and `config/rulebook.md`. After the model drafts a CV, a **deterministic validator** (`llm/validator.py`) enforces the rules independently of the model: no em/en dashes, no AI-trace clichés, British spelling, real JD-keyword coverage, and a complete cover letter. Output is an ATS-safe single-column A4 `.docx`. Nothing is invented — only your genuine experience is re-emphasised to match each role. Still give each tailored CV a 30-second review; no system is flawless on creative tailoring.

---

## Storage

Everything is in one Supabase Postgres database:

- `jobs` — every discovered/added job with score, status, tailored-document paths, bucket-list flag, tracker notes.
- `pipeline_runs` — one row per run for the dashboard's history view.

Point **DBeaver** (or any SQL client) at the same `SUPABASE_DB_URL` to query it directly. No database is committed to git.

---

## Add a job manually

From the dashboard's **Add a job** tab, or the CLI:

```bash
python scripts/add_job.py --title "Data Scientist" --company "Monzo" --url "https://..." --description "paste JD"
```

It is flagged custom, enters the tailoring queue immediately, and appears in the tracker.

---

## A note on sources

This tool deliberately uses official APIs (Reed, Adzuna) and public ATS job boards (Greenhouse, Lever, Ashby). It does **not** scrape LinkedIn, Indeed, Glassdoor or Google directly — those break the sites' terms and get accounts banned. Adzuna already aggregates many of those listings legitimately. No job-search system is "100% accurate": boards repost, LLMs occasionally misjudge, and ghost jobs exist — the aim is high precision and recall with a quick human review.

---

## Tech stack

Python 3.10+ · Reed & Adzuna APIs · Greenhouse/Lever/Ashby · rapidfuzz · Supabase (Postgres) · psycopg 3 · Google Gemini (free) · python-docx · Streamlit · GitHub Actions.
