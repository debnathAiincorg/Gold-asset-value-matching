# Gold Inventory Dashboard

Turns our gold purchase records into a profit/loss dashboard: it pulls the
purchase inventory from a SharePoint Excel file and today's 22K gold rate
from Tanishq's public rate page, works out what we'd gain or lose selling
everything at today's rate, and renders it all as a single self-contained
HTML file you can open in a browser.

## The three scripts

| Script | What it does |
|---|---|
| `fetch_tanishq_gold_rate.py` | Scrapes today's 22 Karat (1 gram) gold rate from Tanishq's public rate page using a stealth-configured headless Chromium (Playwright), since the site sits behind Cloudflare. Saves it to `fetch_tanishq_gold_rate.json`. If every attempt fails, it falls back to the last known rate instead of failing the pipeline (see "Stale rate fallback" below). |
| `fetch_excel_data.py` | Logs into Microsoft Graph API with an Azure app registration (service-account credentials, no human login) and downloads the `Gold Inventory.xlsx` file from SharePoint. Saves the rows as `fetch_excel_data.json`. |
| `build_gold_dashboard.py` | Reads both JSON files, computes "Our Amount" / "Today's Value" / "Profit-Loss" per item and in total, and writes the finished dashboard to `Gold_asset_value_matching.html`. |

## Running it locally

**One-time setup:**

```
pip install -r requirements.txt
python -m playwright install chromium
```

Copy `.env.example` to `.env` and fill in real values (see "Environment
variables" below) - only needed for `fetch_excel_data.py`.

**To regenerate the dashboard, run the three scripts in this order:**

```
python fetch_tanishq_gold_rate.py
python fetch_excel_data.py
python build_gold_dashboard.py
```

Then open `Gold_asset_value_matching.html` in a browser.

## Automation (GitHub Actions)

`.github/workflows/update-dashboard.yml` runs the same three steps
automatically every day at 08:00 IST (`cron: "30 2 * * *"` UTC), or
on-demand via the workflow's "Run workflow" button. If the data files or
generated HTML changed, it commits and pushes them back to `main` as
`github-actions[bot]`, then deploys `Gold_asset_value_matching.html` alone
to GitHub Pages (the JSON data files are excluded from the published
site since they contain vendor names and purchase rates).

Live Pages URL: `https://debnathaiincorg.github.io/Gold-asset-value-matching/`
**once confirmed working** - as of this writing, GitHub Pages hasn't been
enabled for this repo yet (Settings → Pages → Source needs to be set to
"GitHub Actions"), so that URL currently 404s even though the workflow's
deploy step is already wired up. Enable Pages in the repo settings and
the next scheduled/dispatched workflow run will publish it.

## Environment variables

`fetch_excel_data.py` needs three Azure app-registration values, loaded
from a local `.env` file (see `.env.example` for the exact variable
names - never commit the real `.env`, it's already gitignored):

- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID`
- `AZURE_CLIENT_SECRET`

In GitHub Actions, the same three values come from repository secrets
(Settings → Secrets and variables → Actions) instead of a `.env` file -
see the `env:` block on the "Fetch gold inventory from SharePoint" step
in the workflow.

## Stale rate fallback (`is_stale`)

`fetch_tanishq_gold_rate.py` scrapes a public webpage, not an official
API, so a fetch can occasionally fail (Cloudflare challenge, page
redesign, network blip). Rather than letting that take down the whole
daily pipeline, a failed fetch reuses the last successful rate already
sitting in `fetch_tanishq_gold_rate.json`, marks it `"is_stale": true`
with a `"stale_reason"`, and lets the rest of the pipeline continue. The
dashboard shows a warning banner whenever `is_stale` is true, so it's
obvious the profit/loss figures are based on an old rate rather than
today's. A successful fresh fetch always writes `"is_stale": false`.

## Dashboard notes

- Only the **Date** column is sortable (click the header to toggle
  ascending/descending); this is by design - the other columns are
  display-only.
- The search box and "Hide sold items" checkbox filter the table live,
  and the summary cards above it recalculate from whatever rows are
  currently visible.
