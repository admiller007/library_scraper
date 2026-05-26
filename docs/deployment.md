# Deployment

The system has three independently deployed pieces:

1. **Supabase** — Postgres database that stores events and scrape runs.
2. **GitHub Actions** — scheduled daily scraper writing into Supabase.
3. **Vercel** — Next.js frontend reading from Supabase.

Follow the sections below in order; each is a few minutes.

---

## 1. Supabase

1. Sign in at https://supabase.com/dashboard and create a new project. Pick a region close to Chicago (US-East-1 is fine).
2. Once the project is provisioned, open **SQL editor → New query**, paste the contents of [`supabase/schema.sql`](../supabase/schema.sql), and run it. Confirm the `events` and `scrape_runs` tables exist in the **Table editor**.
3. Open **Project Settings → API** and copy the following — you will use them in the next two sections:
   - `Project URL` (e.g. `https://abcd1234.supabase.co`)
   - `anon` public key (used by the frontend, read-only via RLS)
   - `service_role` secret key (used by the scraper, bypasses RLS)

---

## 2. Vercel

1. Push this branch to GitHub.
2. In Vercel, **Add New → Project**, import the repo.
3. In **Configure Project**, set **Root Directory** to `web`. Framework auto-detects as Next.js.
4. Add these environment variables for **Production** and **Preview**:

   | Name | Value |
   | --- | --- |
   | `NEXT_PUBLIC_SUPABASE_URL` | Supabase Project URL |
   | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon public key |
   | `REVALIDATE_SECRET` | Any long random string (e.g. `openssl rand -hex 32`) |

5. Click **Deploy**. After the first deploy completes, copy the production URL (e.g. `https://library-events.vercel.app`).

The site will render with an empty list until you seed Supabase with a scrape (Section 3).

---

## 3. GitHub Actions

In the GitHub repository, open **Settings → Secrets and variables → Actions → New repository secret** and add:

| Name | Value |
| --- | --- |
| `FIRECRAWL_API_KEY` | Existing Firecrawl API key |
| `SUPABASE_URL` | Same as `NEXT_PUBLIC_SUPABASE_URL` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service_role secret key |
| `VERCEL_REVALIDATE_URL` | `https://<your-vercel-url>/api/revalidate` |
| `REVALIDATE_SECRET` | Same value as the one set in Vercel |

Then:

1. Go to **Actions** tab → **Daily scrape** → **Run workflow** to trigger the first run.
2. Watch the log. The run inserts a row into `scrape_runs` (status `running`), upserts events, marks the run `success`, then POSTs `/api/revalidate` so the Vercel page picks up the new data.
3. Refresh the Vercel URL — events should now appear.

After this, the scheduled run fires daily at 12:00 UTC (06:00 America/Chicago during CST, 07:00 during CDT).

---

## Local development

Install Python + Node, then:

```bash
# Python (scraper, no UI)
python -m venv library_env
source library_env/bin/activate
pip install -r requirements.txt

# Run the scraper to write directly into your Supabase project:
SUPABASE_URL=...                # service_role context
SUPABASE_SERVICE_ROLE_KEY=...
FIRECRAWL_API_KEY=...
python scripts/scrape_to_supabase.py --days 3

# Next.js (frontend)
cd web
cp .env.local.example .env.local   # fill in NEXT_PUBLIC_* + REVALIDATE_SECRET
npm install
npm run dev                        # http://localhost:3000
```

To test the revalidate route locally:

```bash
curl -X POST http://localhost:3000/api/revalidate \
  -H "Authorization: Bearer $REVALIDATE_SECRET"
# → {"revalidated":true}
```

---

## Troubleshooting

- **Site shows "Refresh pending" forever.** No `scrape_runs` rows yet. Trigger the GitHub Action manually.
- **Events appear in Supabase but not the site.** Vercel cache hasn't been busted. Confirm `VERCEL_REVALIDATE_URL` and `REVALIDATE_SECRET` are set on both sides and match.
- **GitHub Action fails with 401.** Either the service_role key is wrong or RLS is unexpectedly applied. Service-role bypasses RLS, so check the key first.
- **Daylight saving time drift.** The cron is UTC. To keep 6 AM Central year-round, add a second entry `'0 11 * * *'` and rely on the natural-key UPSERT for dedup.
