# AI lab tracker

Free Python. It uses only the standard library, plus pyarrow for the optional Arena panel. No paid service and no AI credits at run time.

- `python3 tracker.py` collects data, writes `data/history.db`, and builds `site/index.html`.
- Secret needed: `YOUTUBE_API_KEY` (free Google key). `GITHUB_TOKEN` is supplied by GitHub Actions.
- `.github/workflows/refresh.yml` runs it every day and deploys `site/` to Cloudflare (Workers static assets, `wrangler.jsonc`). Live page: https://ai-lab-tracker.fundamental-integrity-web.workers.dev
- `manual.json` holds the Ramp AI Index numbers. Update it once a month from https://ramp.com/data/ai-index.
- Names that count for each lab are in `ALIASES` in `tracker.py`. Add new product names there.
