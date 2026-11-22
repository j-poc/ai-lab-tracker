# AI lab tracker

Free Python. It uses only the standard library, plus pyarrow for the optional Arena panel. No paid service and no AI credits at run time.

- `python3 tracker.py` collects data, writes `data/history.db`, and builds `site/index.html`.
- Secret needed: `YOUTUBE_API_KEY` (free Google key). `GITHUB_TOKEN` is supplied by GitHub Actions.
- `.github/workflows/refresh.yml` runs it every day and deploys `site/` to Cloudflare (Workers static assets, `wrangler.jsonc`). Live page: https://evoa-ai-lab-tracker.fundamental-integrity-web.workers.dev
- `manual.json` holds the Ramp AI Index numbers. Update it once a month from https://ramp.com/data/ai-index.
- Names that count for each lab are in `ALIASES` in `tracker.py`. Add new product names there.

## Public launch

Keep all four labs, the source dates, and the limits for each measure. Show the current YouTube counts while the tracker collects the matching week. The first match can appear on November 28, 2026.

Build with the saved history. Run the refresh from main. Check that it saves history and deploys. Open the public page and check the date, agent counts, and source links.

Keep the leader cards at the top of the page. Keep the source units, dates, and limits.
Show only current YouTube counts until a match is available. Keep coding measures with coding agents. Check both YouTube states and the narrow layout before deployment.
