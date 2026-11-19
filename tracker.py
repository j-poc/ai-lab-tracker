#!/usr/bin/env python3
"""AI Lab Tracker v2. Free public sources. Python standard library only.

Five separate panels, no weighted score: Money, Developer usage, Consumer apps, Attention, Market odds.

Run:  python3 tracker.py             collect, then write artifact.html
      python3 tracker.py --no-collect rebuild the page from stored data
Needs: GitHub token (`gh auth token` or GITHUB_TOKEN) for pull-request search.
Optional: env YOUTUBE_API_KEY (free YouTube Data API v3 key) for the YouTube attention signal.
Money data (Ramp AI Index) is hand-entered in manual.json with its source and date.
"""
import argparse
import json
import re
import os
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
DB_PATH = HERE / "data" / "history.db"
TEMPLATE = HERE / "dashboard.template.html"
OUT = HERE / "artifact.html"
MANUAL = HERE / "manual.json"

COMPANIES = {"anthropic": "Anthropic", "openai": "OpenAI", "xai": "xAI (SpaceXAI)", "meta": "Meta"}
COLORS = {"anthropic": "#D97757", "openai": "#10A37F", "xai": "#E5B93C", "meta": "#4C8DF6"}

# Every official package of each lab, summed. Agent SDKs and coding-agent CLIs count too, so no lab is judged by one package only.
# A package that depends on another one (openai-agents needs openai) adds to both counts, so these are installs, not users.
PYPI = {"anthropic": ["anthropic", "claude-agent-sdk", "claude-code-sdk"], "openai": ["openai", "openai-agents", "openai-codex"], "xai": ["xai-sdk"],
        "meta": ["llama-api-client", "llama-models", "llama-stack-client", "llama-stack"]}
NPM = {"anthropic": ["@anthropic-ai/sdk", "@anthropic-ai/claude-code", "@anthropic-ai/claude-agent-sdk"],
       "openai": ["openai", "@openai/codex", "@openai/agents", "@openai/codex-sdk"], "xai": [],  # xAI has no official npm package (@ai-sdk/xai is Vercel's)
       "meta": ["llama-api-client", "llama-stack-client"]}
# Default branch prefixes of cloud coding agents: (lab, prefix, metric). Meta has no verified prefix.
# Cursor (owned by SpaceXAI since Aug 2026) is a separate series, so the xAI line stays visible but labeled.
PR_SERIES = [("anthropic", "claude/", "agent_prs"), ("openai", "codex/", "agent_prs"), ("xai", "cursor/", "agent_prs_cursor")]

# Arena (formerly LMArena) human-preference leaderboards. Official dataset, CC BY 4.0.
ARENA_URL = "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset/resolve/main/{cfg}/latest-00000-of-00001.parquet"
ARENA_BOARDS = [  # key -> (title, dataset config, category)
    ("text", "Text, overall", "text", "overall"),
    ("coding", "Text, coding", "text", "coding"),
    ("webdev", "WebDev", "webdev", "overall"),
]
ARENA_ORGS = {"anthropic": "anthropic", "openai": "openai", "xai": "xai", "meta": "meta"}

# Cloudflare Radar: daily rank of Internet services (from 1.1.1.1 DNS data), Generative AI category.
RADAR_URL = ("https://api.cloudflare.com/client/v4/radar/ranking/internet_services/timeseries_groups"
             "?serviceCategory=Generative%20AI&dateRange=28d&limit=60&format=json")
RADAR_NAMES = {"anthropic": "Claude / Anthropic", "openai": "ChatGPT / OpenAI", "xai": "Grok / xAI", "meta": "Meta AI"}
RADAR_REF = "Google Gemini"

# OpenRouter public usage dataset (CC BY 4.0). Needs an OpenRouter API key. Model author = text before "/" in the model slug.
OPENROUTER_URL = "https://openrouter.ai/api/v1/datasets/rankings-daily?period=week&start_date={a}&end_date={b}"
OPENROUTER_AUTHORS = {"anthropic": ("anthropic",), "openai": ("openai",), "xai": ("x-ai", "xai"), "meta": ("meta-llama", "meta")}

APPS = {  # key -> (App Store id, lab, name)
    "chatgpt": (6448311069, "openai", "ChatGPT"),
    "claude": (6473753684, "anthropic", "Claude"),
    "grok": (6670324846, "xai", "Grok"),
    "grokbot": (6794501026, "xai", "Grok Bot"),
    "metaai": (1558240027, "meta", "Meta AI"),
}
PRODUCTS = {  # key -> (name, lab, Hacker News phrase, YouTube query, words the video title must contain)
    "grokbot": ("Grok Bot", "xai", ["Grok Bot", "GrokBot"], ["Grok Bot", "GrokBot"], ("grok bot", "grokbot")),
    "claudecode": ("Claude Code", "anthropic", ["Claude Code"], ["Claude Code"], ("claude code",)),
    # OpenAI ships ChatGPT and Codex as one product family, so they are one row here
    "codex": ("ChatGPT + Codex", "openai", ["ChatGPT", "OpenAI Codex", "Codex CLI"], ["ChatGPT", "OpenAI Codex", "Codex CLI"], ("chatgpt", "codex")),
    "metaai": ("Meta AI app", "meta", ["Meta AI"], ["Meta AI"], ("meta ai",)),
    "muse": ("Meta Muse models", "meta", ["Muse Spark", "Muse Wonder"], ["Muse Spark", "Muse Wonder"], ("muse spark", "muse wonder")),
}
# Every name a lab is known by. Same rule for all four labs: company name, flagship products, model families and
# coding agents. No people, and no generic word that means something else too. News uses a GDELT OR query; Hacker News
# and YouTube count each name and remove duplicates; Wikipedia sums the listed articles.
# Cursor (owned by SpaceXAI since Aug 2026) is NOT counted in xAI.
ALIASES = {
    "anthropic": {"news": '(Anthropic OR "Claude Code" OR "Claude Opus" OR "Claude Sonnet" OR "Claude Haiku" OR "Claude AI")',
                  "hn": ["Anthropic", "Claude Code", "Claude Opus", "Claude Sonnet", "Claude Haiku", "Claude AI"],
                  "yt": ["Anthropic", "Claude AI", "Claude Code", "Claude Opus"],
                  "wiki": ["Anthropic", "Claude_(AI)", "Claude_(language_model)", "Claude_Code"]},
    "openai": {"news": '(OpenAI OR ChatGPT OR "OpenAI Codex" OR "Codex CLI" OR "GPT-5" OR "gpt-oss")',
               "hn": ["OpenAI", "ChatGPT", "OpenAI Codex", "Codex CLI", "GPT-5", "gpt-oss", "ChatGPT Atlas", "Sora"],
               "yt": ["OpenAI", "ChatGPT", "OpenAI Codex", "GPT-5"],
               "wiki": ["OpenAI", "ChatGPT", "OpenAI_Codex", "GPT-5", "Sora_(text-to-video_model)"]},
    "xai": {"news": '(Grok OR SpaceXAI OR "SpaceX AI" OR "Grok Bot" OR Grokipedia)',
            "hn": ["Grok", "SpaceXAI", "SpaceX AI", "xAI", "Grokipedia"],
            "yt": ["Grok", "SpaceXAI", "Grok Bot", "Grokipedia"],
            "wiki": ["SpaceXAI", "XAI_(company)", "Grok_(chatbot)", "Grokipedia"]},
    "meta": {"news": '("Meta AI" OR "Muse Spark" OR "Muse Wonder" OR "Meta Muse" OR "Meta Superintelligence" OR "Meta Llama" OR "Llama 4")',
             "hn": ["Meta AI", "Muse Spark", "Muse Wonder", "Meta Muse", "Meta Superintelligence", "Meta Llama", "Llama 4"],
             "yt": ["Meta AI", "Muse Spark", "Muse Wonder", "Meta Llama", "Llama 4", "Meta Superintelligence"],
             "wiki": ["Meta_AI", "Llama_(language_model)", "Meta_Superintelligence_Labs", "Muse_(language_model)", "Muse_(AI_agent)", "Muse_Spark", "Muse_Wonder"]},
}
# Reddit communities where each lab's users gather. Activity is counted as posts per week (Arctic Shift archive).
REDDIT = {"anthropic": ["ClaudeAI", "ClaudeCode"], "openai": ["OpenAI", "ChatGPT"], "xai": ["grok", "GrokBot"], "meta": ["MetaAI"]}
PM_NAMES = {"anthropic": "Anthropic", "openai": "OpenAI", "xai": "SpaceXAI", "meta": "Meta"}


def log(msg):
    print(msg, flush=True)


def gh_token():
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception:
        return None


TOKEN = None


def http_json(url, headers=None, retries=4, wait429=65):
    hdrs = {"User-Agent": "ai-lab-tracker/2.0 (public research)", "Accept": "application/json"}
    hdrs.update(headers or {})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=40) as r:
                return json.load(r)
        except json.JSONDecodeError:  # some APIs answer a rate limit with plain text
            if attempt == retries - 1:
                raise
            log(f"  non-JSON answer, wait {wait429}s")
            time.sleep(wait429)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            if "googleapis.com" in url and e.code in (403, 429):  # waiting does not refill a daily quota
                raise RuntimeError(f"quota exhausted: {body[:120]}")
            if e.code in (403, 429) and attempt < retries - 1:
                log(f"  rate limited, wait {wait429}s")
                time.sleep(wait429)
                continue
            raise RuntimeError(f"HTTP {e.code} for {url.split('?')[0]}: {body}")
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(3)
    raise RuntimeError(f"no response for {url}")


def gh_json(path):
    h = {"Accept": "application/vnd.github+json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return http_json("https://api.github.com/" + path, h)


# ---------- storage ----------
def db():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS metrics (date TEXT, company TEXT, metric TEXT, value REAL, PRIMARY KEY(date, company, metric))")
    con.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
    return con


def put(con, d, company, metric, value):
    con.execute("INSERT OR REPLACE INTO metrics VALUES (?,?,?,?)", (d, company, metric, value))


def have(con, d, company, metric):
    return con.execute("SELECT 1 FROM metrics WHERE date=? AND company=? AND metric=?", (d, company, metric)).fetchone() is not None


def kv_put(con, key, obj):
    con.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(obj)))


def kv_get(con, key, default=None):
    r = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


# ---------- windows ----------
def utc_today():
    return datetime.now(timezone.utc).date()


def week_windows(n):
    end = utc_today() - timedelta(days=1)
    return [(end - timedelta(days=7 * i + 6), end - timedelta(days=7 * i)) for i in range(n)]


# ---------- developer usage ----------
def pkg_migrate(con):
    """One time: the package lists grew from one package per lab to all official packages, so old sums are not comparable."""
    if kv_get(con, "pkg_family_v2"):
        return
    con.execute("DELETE FROM metrics WHERE metric IN ('pypi', 'npm')")
    kv_put(con, "pkg_family_v2", True)
    con.commit()
    log("package totals reset to the all-official-packages lists")


def collect_pypi(con, windows):
    pkg_migrate(con)
    for co, pkgs in PYPI.items():
        if all(have(con, w[1].isoformat(), co, "pypi") for w in windows[1:]) and have(con, windows[0][1].isoformat(), co, "pypi"):
            continue
        try:
            daily = {}
            for pkg in pkgs:
                rows = http_json(f"https://pypistats.org/api/packages/{pkg}/overall?mirrors=false")["data"]
                for r in rows:
                    if r["category"] == "without_mirrors":
                        daily[r["date"]] = daily.get(r["date"], 0) + r["downloads"]
                time.sleep(1.5)
            for s, e in windows:
                put(con, e.isoformat(), co, "pypi", sum(v for k, v in daily.items() if s.isoformat() <= k <= e.isoformat()))
            log(f"pypi {co} ok")
        except Exception as ex:
            log(f"pypi {co} FAILED: {ex}")
        time.sleep(1.5)


def collect_npm(con, windows):
    start, end = windows[-1][0], windows[0][1]
    for co, pkgs in NPM.items():
        if not pkgs:
            continue
        try:
            daily = {}
            for pkg in pkgs:
                for r in http_json(f"https://api.npmjs.org/downloads/range/{start}:{end}/{pkg}")["downloads"]:
                    daily[r["day"]] = daily.get(r["day"], 0) + r["downloads"]
                time.sleep(1)
            for s, e in windows:
                put(con, e.isoformat(), co, "npm", sum(v for k, v in daily.items() if s.isoformat() <= k <= e.isoformat()))
            log(f"npm {co} ok")
        except Exception as ex:
            log(f"npm {co} FAILED: {ex}")
        time.sleep(1)


def collect_prs(con, windows):
    """Weekly pull requests opened from each agent's default branch prefix."""
    for co, prefix, metric in PR_SERIES:
        for s, e in windows:
            d = e.isoformat()
            if have(con, d, co, metric):
                continue
            q = f"is:pr head:{prefix} created:{s}..{e}"
            try:
                n = gh_json("search/issues?per_page=1&q=" + urllib.parse.quote(q))["total_count"]
                put(con, d, co, metric, n)
                con.commit()
                log(f"{metric} {co} {d} = {n}")
            except Exception as ex:
                log(f"{metric} {co} {d} FAILED: {ex}")
            time.sleep(7)  # search API: 30 requests/min plus a secondary limit


# ---------- model quality (Arena) ----------
def collect_arena(con):
    """Best model of each lab on three Arena boards. Needs pyarrow to read the parquet files; skips if missing."""
    try:
        import io
        import pyarrow.parquet as pq
    except ImportError:
        log("arena skipped: pyarrow is not installed")
        return
    out, asof = {}, None
    cache = {}
    for key, title, cfg, cat in ARENA_BOARDS:
        try:
            if cfg not in cache:
                req = urllib.request.Request(ARENA_URL.format(cfg=cfg), headers={"User-Agent": "ai-lab-tracker"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    cache[cfg] = pq.read_table(io.BytesIO(r.read())).to_pylist()
            rows = [r for r in cache[cfg] if r["category"] == cat and r["rating"] is not None]
            if not rows:
                raise RuntimeError("no rows")
            date = max(r["leaderboard_publish_date"] for r in rows)
            rows = [r for r in rows if r["leaderboard_publish_date"] == date]
            top = max(r["rating"] for r in rows)
            labs = {}
            for lab, org in ARENA_ORGS.items():
                mine = [r for r in rows if r["organization"] == org]
                if mine:
                    b = max(mine, key=lambda r: r["rating"])
                    labs[lab] = {"model": b["model_name"], "rating": round(b["rating"]), "rank": int(b["rank"]), "votes": int(b["vote_count"] or 0),
                                 "behind": round(top - b["rating"])}
                    put(con, date, lab, "arena_" + key, round(b["rating"]))
            leader = max(rows, key=lambda r: r["rating"])
            out[key] = {"title": title, "date": date, "models": len(rows), "top": {"model": leader["model_name"], "org": leader["organization"], "rating": round(top)}, "labs": labs}
            asof = max(asof or date, date)
            log(f"arena {key} ok ({date}, {len(rows)} models)")
        except Exception as ex:
            log(f"arena {key} FAILED: {ex}")
    if out:
        old = kv_get(con, "arena", {}) or {}
        old.update(out)  # a failed board keeps its old values
        kv_put(con, "arena", old)


def collect_radar(con):
    """Rank of each lab's chat site among Generative AI services on Cloudflare Radar, last 28 days."""
    tok = os.environ.get("CLOUDFLARE_RADAR_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN")
    if not tok:
        log("radar skipped: no Cloudflare token")
        return
    try:
        s = http_json(RADAR_URL, {"Authorization": "Bearer " + tok}, retries=1)["result"]["serie_0"]
        today = utc_today().isoformat()
        keep = [i for i, t in enumerate(s["timestamps"]) if t[:10] < today]  # today is a partial day
        if not keep:
            raise RuntimeError("no finished days")
        dates = [s["timestamps"][i][:10] for i in keep]
        pick = lambda name: [s[name][i] for i in keep] if name in s else None
        series = {lab: pick(name) for lab, name in RADAR_NAMES.items()}
        if not any(series.values()):
            raise RuntimeError("none of the services found")
        for lab, ranks in series.items():
            for d, r in zip(dates, ranks or []):
                if r is not None:
                    put(con, d, lab, "radar_rank", r)
        kv_put(con, "radar", {"dates": dates, "series": series, "reference": {"name": RADAR_REF, "ranks": pick(RADAR_REF)},
                              "services": len([k for k in s if k != "timestamps"]), "as_of": dates[-1]})
        log(f"radar ok ({dates[0]} to {dates[-1]})")
    except Exception as ex:
        log(f"radar FAILED: {ex}")  # the old values stay


def collect_openrouter(con):
    """Share of OpenRouter tokens by model author, weekly. Top 50 models per week only, so small labs are understated."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        log("openrouter skipped: no OPENROUTER_API_KEY")
        return
    try:
        end = utc_today() - timedelta(days=1)
        res = http_json(OPENROUTER_URL.format(a=(end - timedelta(days=63)).isoformat(), b=end.isoformat()), {"Authorization": "Bearer " + key}, retries=1)
        by_week = {}
        for r in res["data"]:
            by_week.setdefault(r["date"], {})[r["model_permaslug"]] = int(r["total_tokens"])
        weeks = sorted(by_week)[-8:]
        if len(weeks) < 2:
            raise RuntimeError("fewer than 2 weeks")
        owner = {a: lab for lab, names in OPENROUTER_AUTHORS.items() for a in names}
        tokens = {lab: [] for lab in OPENROUTER_AUTHORS}
        totals, top = [], {}
        for w in weeks:
            tot, authors = 0, {}
            for slug, n in by_week[w].items():
                tot += n
                if slug != "other":
                    authors[slug.split("/")[0]] = authors.get(slug.split("/")[0], 0) + n
            totals.append(tot)
            for lab in tokens:
                tokens[lab].append(sum(n for a, n in authors.items() if owner.get(a) == lab))
            top[w] = sorted(authors.items(), key=lambda x: -x[1])[:5]
        share = {lab: [round(100 * t / tot, 2) if tot else None for t, tot in zip(v, totals)] for lab, v in tokens.items()}
        for lab, v in share.items():
            for w, x in zip(weeks, v):
                if x is not None:
                    put(con, w, lab, "openrouter_share", x)
        latest = weeks[-1]
        kv_put(con, "openrouter", {"weeks": weeks, "share": share, "tokens": tokens, "total": totals,
                                   "top_authors": [[a, round(100 * n / totals[-1], 1)] for a, n in top[latest]], "as_of": res["meta"].get("as_of", latest)[:10]})
        log(f"openrouter ok ({weeks[0]} to {latest})")
    except Exception as ex:
        log(f"openrouter FAILED: {ex}")  # the old values stay


# ---------- consumer apps ----------
def recent_reviews(tid, pages=10):
    """Written reviews per day over the newest 500 reviews, and their average stars."""
    stamps, stars = [], []
    for p in range(1, pages + 1):
        feed = http_json(f"https://itunes.apple.com/us/rss/customerreviews/page={p}/id={tid}/sortby=mostrecent/json")["feed"]
        entries = feed.get("entry", [])
        entries = [entries] if isinstance(entries, dict) else entries
        got = [e for e in entries if "im:rating" in e]
        if not got:
            break
        stamps += [datetime.fromisoformat(e["updated"]["label"]) for e in got]
        stars += [int(e["im:rating"]["label"]) for e in got]
        time.sleep(0.3)
    if len(stamps) < 2:
        return 0.0, 0.0
    span = max((max(stamps) - min(stamps)).total_seconds() / 86400, 1 / 24)
    return len(stamps) / span, sum(stars) / len(stars)


def collect_apps(con, today):
    try:
        ids = ",".join(str(a[0]) for a in APPS.values())
        info = {str(r["trackId"]): r for r in http_json(f"https://itunes.apple.com/lookup?id={ids}&country=us")["results"]}
        overall = http_json("https://itunes.apple.com/us/rss/topfreeapplications/limit=200/json")["feed"]["entry"]
        prod = http_json("https://itunes.apple.com/us/rss/topfreeapplications/limit=200/genre=6007/json")["feed"]["entry"]
        r_all = {e["id"]["attributes"]["im:id"]: i for i, e in enumerate(overall, 1)}
        r_prod = {e["id"]["attributes"]["im:id"]: i for i, e in enumerate(prod, 1)}
        for key, (tid, _lab, _name) in APPS.items():
            r = info.get(str(tid), {})
            put(con, today, key, "app_ratings", r.get("userRatingCount", 0))
            put(con, today, key, "app_stars", r.get("averageUserRating", 0))
            put(con, today, key, "app_rank", r_all.get(str(tid), 0))
            put(con, today, key, "app_prodrank", r_prod.get(str(tid), 0))
            try:
                rate, stars = recent_reviews(tid)
                put(con, today, key, "app_review_rate", rate)
                put(con, today, key, "app_review_stars", stars)
            except Exception as ex:
                log(f"reviews {key} FAILED: {ex}")
        log("apps ok")
    except Exception as ex:
        log(f"apps FAILED: {ex}")


# ---------- attention ----------
def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def yt_unit(queries, musts, win_end, length, key):
    """One YouTube search for one 7-day window (ending on win_end, UTC) and one length class.
    Returns the videos whose title has a name: [[id, views, title, channel], ...]. Costs 1 search + 1 cheap lookup.
    medium = 4-20 min, long = 20+ min, so Shorts are never counted."""
    a = datetime.combine(win_end - timedelta(days=6), datetime.min.time(), timezone.utc)
    b = datetime.combine(win_end + timedelta(days=1), datetime.min.time(), timezone.utc)
    q = "|".join(chr(34) + x + chr(34) for x in queries)
    s = http_json("https://www.googleapis.com/youtube/v3/search?part=id&type=video&maxResults=50&order=viewCount"
                  f"&videoDuration={length}&q={urllib.parse.quote(q)}&publishedAfter={iso(a)}&publishedBefore={iso(b)}&key={key}")
    ids = [x["id"]["videoId"] for x in s.get("items", [])]
    if not ids:
        return []
    v = http_json(f"https://www.googleapis.com/youtube/v3/videos?part=statistics,snippet&id={','.join(ids)}&key={key}")["items"]
    return [[x["id"], int(x["statistics"].get("viewCount", 0)), x["snippet"]["title"], x["snippet"]["channelTitle"]]
            for x in v if any(m in x["snippet"]["title"].lower().replace("grokking", "") for m in musts)]  # "grokking" is not Grok


def yt_collect(con, key, end, targets):
    """Resumable YouTube collection. The Google project has a small daily search cap, so each search result is
    saved on its own. A run stops at the first quota error and the next run continues. Order: this week for every
    target first, then the week before. A week that was collected earlier is reused, never searched twice."""
    cache = kv_get(con, "yt_units", {}) or {}
    todo = [(tid, w) for w in (end, end - timedelta(days=7)) for tid in targets]
    done = tried = 0
    for tid, w in todo:
        queries, musts = targets[tid]
        for length in ("medium", "long"):
            k = f"{tid}|{w.isoformat()}|{length}"
            if k in cache:
                continue
            tried += 1
            try:
                cache[k] = yt_unit(queries, musts, w, length, key)
                kv_put(con, "yt_units", cache)  # save after every search, so a stop loses nothing
                con.commit()
                done += 1
            except RuntimeError as ex:
                if "quota exhausted" in str(ex):
                    log(f"yt: quota used up after {done} new searches; the next run continues")
                    return cache
                log(f"yt {k} FAILED: {str(ex)[:100]}")
    for k in [k for k in cache if k.split("|")[0] not in targets or datetime.fromisoformat(k.split("|")[1]).date() < end - timedelta(days=28)]:
        del cache[k]  # keep four weeks of saved results
    kv_put(con, "yt_units", cache)
    log(f"yt: {done} new searches, {len(todo) * 2 - tried} already saved")
    return cache


def yt_row(cache, tids, end):
    """Build the dashboard row for one target from saved searches. Weeks are fixed (Monday to Sunday), so last week is reused."""
    def week(w):
        tl = [tids] if isinstance(tids, str) else tids
        ks = [f"{t}|{w.isoformat()}|{ln}" for t in tl for ln in ("medium", "long")]
        if not all(k in cache for k in ks):
            return None
        seen = {}
        for k in ks:
            for vid, views, title, ch in cache[k]:
                seen[vid] = (views, title, ch)
        return sorted(seen.values(), reverse=True)
    for i in (0, 7):  # this week, or last week when this week is not finished yet
        w = end - timedelta(days=i)
        cur = week(w)
        if cur is None:
            continue
        pri = week(w - timedelta(days=7))
        top = {"views": cur[0][0], "title": cur[0][1], "channel": cur[0][2]} if cur else None
        return {"videos": [len(cur), len(pri) if pri is not None else None], "views": [sum(r[0] for r in cur), sum(r[0] for r in pri) if pri is not None else None],
                "top": top, "asof": w.isoformat()}
    return None


def hn_union(phrases, a, b):
    """Unique Hacker News stories and comments that contain any phrase, counted day by day."""
    total, day = 0, a
    while day < b:
        nxt = min(day + timedelta(days=1), b)
        ids = set()
        for p in phrases:
            u = (f"https://hn.algolia.com/api/v1/search?tags=(story,comment)&hitsPerPage=1000&advancedSyntax=true&attributesToRetrieve=objectID"
                 f"&query={urllib.parse.quote(chr(34) + p + chr(34))}"
                 f"&numericFilters=created_at_i>={int(day.timestamp())},created_at_i<{int(nxt.timestamp())}")
            ids.update(h["objectID"] for h in http_json(u)["hits"])
        total += len(ids)
        day = nxt
    return total


def reddit_posts(sub, first, last):
    """Posts in a subreddit from `first` to `last` (dates, both included), from the free Arctic Shift archive.
    Big subreddits time out on long windows, so ask two days at a time."""
    total, day = 0, first
    while day <= last:
        stop = min(day + timedelta(days=2), last + timedelta(days=1))
        u = ("https://arctic-shift.photon-reddit.com/api/posts/search/aggregate?aggregate=created_utc&frequency=day"
             f"&subreddit={sub}&after={day.isoformat()}&before={stop.isoformat()}")
        for attempt in range(5):
            try:
                d = http_json(u)
            except RuntimeError as ex:
                if "Timeout" not in str(ex):  # the archive reports a slow query as a Timeout error (HTTP 422 or 200)
                    raise
                d = {"data": None}
            if d.get("data") is not None:
                total += sum(int(x["count"]) for x in d["data"])
                break
            time.sleep(8)
        else:
            raise RuntimeError(f"reddit {sub} timed out")
        day = stop
    return total


def wiki_views(title, a, b):
    u = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
         f"{urllib.parse.quote(title, safe='')}/daily/{a:%Y%m%d}/{b:%Y%m%d}")
    return sum(i["views"] for i in http_json(u)["items"])


def gdelt_share(query, end):
    """Mean daily share of monitored news volume. Returns (last 7 days, prior 7 days)."""
    u = "https://api.gdeltproject.org/api/v2/doc/doc?mode=timelinevol&format=json&timespan=16d&query=" + urllib.parse.quote(query)
    pts = http_json(u, retries=3, wait429=30)["timeline"][0]["data"]
    by_day = {}
    for p in pts:
        by_day.setdefault(p["date"][:8], []).append(p["value"])
    days = {d: sum(v) / len(v) for d, v in by_day.items()}
    last = [days.get((end - timedelta(days=i)).strftime("%Y%m%d")) for i in range(7)]
    prior = [days.get((end - timedelta(days=i)).strftime("%Y%m%d")) for i in range(7, 14)]
    last, prior = [x for x in last if x is not None], [x for x in prior if x is not None]
    if len(last) < 5 or len(prior) < 5:
        raise RuntimeError("not enough days")
    return sum(last) / len(last), sum(prior) / len(prior)


def collect_attention(con):
    end = utc_today() - timedelta(days=1)
    cur_a = datetime.combine(end - timedelta(days=6), datetime.min.time(), timezone.utc)
    cur_b = datetime.combine(end + timedelta(days=1), datetime.min.time(), timezone.utc)
    pri_a, pri_b = cur_a - timedelta(days=7), cur_a
    out = {"as_of": end.isoformat(), "window": f"{end - timedelta(days=6)} to {end}", "products": {}, "labs": {}}
    key = os.environ.get("YOUTUBE_API_KEY")
    parts = set(os.environ.get("ATT_PARTS", "hn,yt,wiki,news,reddit").split(","))  # re-collect only some parts
    prev = kv_get(con, "attention") or {}
    if not key:
        log("NOTE: YOUTUBE_API_KEY not set, skipping YouTube")
    yt_end = end - timedelta(days=(end.weekday() - 6) % 7)  # newest finished Sunday: YouTube uses fixed weeks so searches are reused
    out["yt_window"] = f"{yt_end - timedelta(days=6)} to {yt_end}"
    yt_cache = {}
    if key and "yt" in parts:
        # One search per name, shared by the lab rows and the product rows. One OR search returns the top 50 matches
        # of all names together, and most of them do not have a name in the title, so totals would be far too low.
        names = {n for al in ALIASES.values() for n in al["yt"]} | {n for pv in PRODUCTS.values() if pv[3] for n in pv[3]}
        targets = {f"n:{n}": ([n], [n.lower()]) for n in sorted(names)}
        yt_cache = yt_collect(con, key, yt_end, targets)

    def part(row, old, part_name, field, fn):
        """Collect one field, or keep the stored value when this part is not being re-collected."""
        if part_name in parts:
            try:
                val = fn()
                row[field] = old[field] if val is None and field in old else val  # nothing new: keep the last value
            except Exception as ex:
                log(f"{part_name} {field} FAILED: {str(ex)[:100]}")
        elif field in old:
            row[field] = old[field]

    for pk, (name, lab, hn_q, yt_q, must) in PRODUCTS.items():
        row, old = {"name": name, "lab": lab}, (prev.get("products") or {}).get(pk, {})
        part(row, old, "hn", "hn", lambda: [hn_union(hn_q, cur_a, cur_b), hn_union(hn_q, pri_a, pri_b)])
        if key and yt_q:
            part(row, old, "yt", "yt", lambda: yt_row(yt_cache, [f"n:{n}" for n in yt_q], yt_end))
        out["products"][pk] = row
    for lab, al in ALIASES.items():
        row, old = {}, (prev.get("labs") or {}).get(lab, {})
        part(row, old, "wiki", "wiki", lambda: [sum(wiki_views(t, end - timedelta(days=6), end) for t in al["wiki"]),
                                                 sum(wiki_views(t, end - timedelta(days=13), end - timedelta(days=7)) for t in al["wiki"])])
        part(row, old, "hn", "hn", lambda: [hn_union(al["hn"], cur_a, cur_b), hn_union(al["hn"], pri_a, pri_b)])
        if key:
            part(row, old, "yt", "yt", lambda: yt_row(yt_cache, [f"n:{n}" for n in al["yt"]], yt_end))
        def reddit():
            subs = {r: [reddit_posts(r, end - timedelta(days=6), end), reddit_posts(r, end - timedelta(days=13), end - timedelta(days=7))] for r in REDDIT[lab]}
            row["reddit_subs"] = subs
            return [sum(v[0] for v in subs.values()), sum(v[1] for v in subs.values())]
        if "reddit" in parts:
            try:
                row["reddit"] = reddit()
            except Exception as ex:
                log(f"reddit {lab} FAILED: {str(ex)[:100]}")
        elif "reddit" in old:
            row["reddit"], row["reddit_subs"] = old["reddit"], old.get("reddit_subs")
        if "news" in parts:
            try:
                row["news"] = list(gdelt_share(al["news"], end))
            except Exception as ex:
                log(f"gdelt {lab} FAILED: {str(ex)[:100]}")
                if old.get("news"):  # GDELT limits request rates; keep the last good value and say so
                    row["news"] = old["news"]
                    row["news_asof"] = old.get("news_asof") or prev.get("as_of")
            time.sleep(12)
        elif "news" in old:
            row["news"] = old["news"]
            if "news_asof" in old:
                row["news_asof"] = old["news_asof"]
        out["labs"][lab] = row
    kv_put(con, "attention", out)
    log("attention ok")


# ---------- market odds ----------
def collect_polymarket(con):
    try:
        d = http_json("https://gamma-api.polymarket.com/public-search?q=best%20ai%20model&limit_per_type=10&events_status=active")
        now = datetime.now(timezone.utc).isoformat()
        evs = [e for e in d.get("events", []) if e.get("slug", "").startswith("which-company-has") and e.get("endDate", "") > now]
        evs.sort(key=lambda e: e["endDate"])
        out = []
        for ev in (evs[0] if evs else None, next((e for e in evs if "end-of-20" in e["slug"]), None)):
            if not ev:
                continue
            odds = {}
            for m in ev["markets"]:
                name = m.get("groupItemTitle", "")
                if name in ("Other",) or name.startswith("Company "):
                    continue
                odds[name] = float(json.loads(m["outcomePrices"])[0]) * 100
            out.append({"title": ev["title"], "end": ev["endDate"][:10], "odds": odds})
        kv_put(con, "polymarket", {"as_of": utc_today().isoformat(), "events": out})
        log("polymarket ok")
    except Exception as ex:
        log(f"polymarket FAILED: {ex}")


# ---------- build ----------
def load(con):
    data = {}
    for d, co, m, v in con.execute("SELECT date, company, metric, value FROM metrics"):
        data.setdefault(m, {}).setdefault(d, {})[co] = v
    return data


def last_date(series):
    return max(series) if series else None


def build(con):
    data = load(con)
    labs = list(COMPANIES)

    dev = {}
    for m in ("pypi", "npm", "agent_prs", "agent_prs_cursor"):
        ser = data.get(m, {})
        dates = sorted(ser)
        if not dates:
            continue
        latest, prev = dates[-1], dates[-2] if len(dates) > 1 else None
        rows = {"dates": dates, "series": {co: [ser[d].get(co) for d in dates] for co in labs}, "latest": ser[latest], "as_of": latest}
        rows["wow"] = {co: (100 * (ser[latest][co] - ser[prev][co]) / ser[prev][co] if prev and ser[latest].get(co) is not None and ser[prev].get(co) else None) for co in labs}
        ds = sorted(ser, reverse=True)
        if len(ds) >= 8:
            lastn = {co: sum(ser[d].get(co, 0) for d in ds[:4]) for co in labs}
            prior = {co: sum(ser[d].get(co, 0) for d in ds[4:8]) for co in labs}
            rows["growth"] = {co: (100 * (lastn[co] - prior[co]) / prior[co] if prior[co] and co in (data[m][ds[0]] or {}) else None) for co in labs}
        dev[m] = rows

    apps = []
    rs = data.get("app_ratings", {})
    for key, (_tid, lab, name) in APPS.items():
        dates = sorted(d for d in rs if key in rs[d])
        if not dates:
            continue
        now = dates[-1]
        old = [d for d in dates if (datetime.fromisoformat(now) - datetime.fromisoformat(d)).days >= 6]
        gained = None
        if old:
            days = max(1, (datetime.fromisoformat(now) - datetime.fromisoformat(old[-1])).days)
            gained = (rs[now][key] - rs[old[-1]][key]) * 7 / days

        def g(metric):
            return data.get(metric, {}).get(now, {}).get(key) or None
        apps.append({"name": name, "lab": lab, "as_of": now, "ratings": rs[now][key], "gained_7d": gained, "stars": g("app_stars"),
                     "rank": g("app_rank"), "prod_rank": g("app_prodrank"), "review_rate": g("app_review_rate"), "review_stars": g("app_review_stars")})

    manual = json.loads(MANUAL.read_text()) if MANUAL.exists() else {}
    return {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "companies": COMPANIES, "colors": COLORS, "products": {k: {"name": v[0], "lab": v[1]} for k, v in PRODUCTS.items()},
        "ramp": manual.get("ramp_ai_index"), "dev": dev, "apps": apps,
        "attention": kv_get(con, "attention"), "arena": kv_get(con, "arena"), "polymarket": kv_get(con, "polymarket"),
        "radar": kv_get(con, "radar"), "openrouter": kv_get(con, "openrouter"),
        "pm_names": PM_NAMES,
    }


def main():
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=12)
    ap.add_argument("--no-collect", action="store_true")
    ap.add_argument("--only", help="comma list: dev,apps,attention,market,arena,radar,openrouter")
    args = ap.parse_args()
    TOKEN = gh_token()
    con = db()
    if not args.no_collect:
        only = set(args.only.split(",")) if args.only else {"dev", "apps", "attention", "market", "arena", "radar", "openrouter"}
        windows, today = week_windows(args.weeks), utc_today().isoformat()
        if "dev" in only:
            if not TOKEN:
                log("WARNING: no GitHub token, pull-request search will fail")
            collect_pypi(con, windows)
            collect_npm(con, windows)
            con.commit()
            collect_prs(con, windows)
        if "apps" in only:
            collect_apps(con, today)
        if "attention" in only:
            collect_attention(con)
        if "market" in only:
            collect_polymarket(con)
        if "arena" in only:
            collect_arena(con)
        if "radar" in only:
            collect_radar(con)
        if "openrouter" in only:
            collect_openrouter(con)
        con.commit()
    payload = build(con)
    page = TEMPLATE.read_text().replace("__DATA__", json.dumps(payload))
    OUT.write_text(page)
    site = OUT.parent / "site"
    site.mkdir(exist_ok=True)
    (site / "index.html").write_text('<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1">' + page)
    log(f"wrote {OUT} and {site / 'index.html'}")


if __name__ == "__main__":
    main()
