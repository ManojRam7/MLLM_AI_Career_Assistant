# GitHub Actions setup

The workflow (`.github/workflows/pipeline.yml`) runs the pipeline every 6 hours.
It reads these **exact** secret names — they must match character for character.

## 1. Fix your secrets (two of yours are wrong)

You currently have: `ADZUNA_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `REED_API_KEY`, `SUPABASE_API_KEY`.

| Your secret | Status | Action |
|---|---|---|
| `REED_API_KEY` | ✅ correct | keep |
| `GEMINI_API_KEY` | ✅ correct | keep |
| `GROQ_API_KEY` | ✅ correct | keep |
| `ADZUNA_API_KEY` | ❌ wrong | **delete it.** Adzuna gives you *two* values — add `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` |
| `SUPABASE_API_KEY` | ❌ wrong | **delete it.** Add `SUPABASE_DB_URL` = the Postgres **connection string**, not an API key |

### Adzuna — two values, not one
At https://developer.adzuna.com your app shows an **Application ID** (short) and an
**Application Key** (long hex). Add both as separate secrets:
- `ADZUNA_APP_ID` = the Application ID
- `ADZUNA_APP_KEY` = the Application Key

### Supabase — the database URL, not the API key
`SUPABASE_API_KEY` (anon/service_role) is for the REST API and **will not work** with this
tool, which connects directly with Postgres. Get the right value:
Supabase → your project → **Project Settings → Database → Connection string → URI**, and
copy the **Transaction pooler** URI (it ends with `:6543/postgres`, works from GitHub's
network). Replace `[YOUR-PASSWORD]` with your database password. It looks like:

```
postgresql://postgres.abcdefgh:YOURPASSWORD@aws-0-eu-west-2.pooler.supabase.com:6543/postgres
```

Save that as `SUPABASE_DB_URL`. (Use this same value in your local `.env`.)

### Final secret list (Settings → Secrets and variables → Actions)
```
REED_API_KEY
ADZUNA_APP_ID
ADZUNA_APP_KEY
SUPABASE_DB_URL
GEMINI_API_KEY
GROQ_API_KEY
TELEGRAM_BOT_TOKEN   (optional)
TELEGRAM_CHAT_ID     (optional)
```

## 2. Push the code

Already pushed to `MLLM_AI_Career_Assistant`. For future updates:

```bash
cd ~/Documents/GitHub/MLLM_AI_Career_Assistant
git add -A
git commit -m "update"
git push
```

`.env`, `output/` and `site/` are git-ignored, so your keys and generated files never leave your machine.

## 3. Enable and test the schedule

1. Repo → **Actions** tab → enable workflows if prompted.
2. Open **jobops-pipeline** → **Run workflow** → choose `first` for the initial backfill → Run.
3. Watch the run; when green, open it and download the **jobops-output** artifact (digest + any tailored CVs).
4. After that, it runs automatically every 6 hours. New jobs and run history appear in the dashboard.

## 4. Deploy the dashboard (Streamlit Community Cloud)

The interactive dashboard — editable tracker, add-job, CV/cover-letter downloads, run-now —
deploys free and reads the same Supabase database the pipeline writes to:

1. Go to https://share.streamlit.io and sign in with GitHub.
2. **Create app → Deploy a public app from GitHub** → repo `MLLM_AI_Career_Assistant`,
   branch `main`, **Main file path** `app/dashboard.py`.
3. **Advanced settings → Secrets** → paste your database URL:

   ```
   SUPABASE_DB_URL = "postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres"
   ```

4. **Deploy.** You get a `https://<app-name>.streamlit.app` URL with everything working —
   editable tracker, CV downloads, add-job and the Run-pipeline button.

Notes: the app is public (anyone with the link) and may sleep after long inactivity, waking on
the next visit. Dependencies install from `requirements.txt` (now includes streamlit + pandas).
To run it locally instead: `python -m streamlit run app/dashboard.py`.
