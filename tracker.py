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

PYPI = {"anthropic": "anthropic", "openai": "openai", "xai": "xai-sdk", "meta": "llama-stack-client"}
NPM = {"anthropic": "@anthropic-ai/sdk", "openai": "openai", "xai": "@ai-sdk/xai", "meta": "llama-stack-client"}
# Default branch prefixes of cloud coding agents. Only Claude and Codex have a verified one.
PR_PREFIX = {"anthropic": "claude/", "openai": "codex/"}

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
    "codex": ("OpenAI Codex", "openai", ["OpenAI Codex", "Codex CLI"], ["OpenAI Codex", "Codex CLI"], ("codex",)),
    "chatgpt": ("ChatGPT", "openai", ["ChatGPT"], ["ChatGPT"], ("chatgpt",)),
    "metaai": ("Meta AI app", "meta", ["Meta AI"], ["Meta AI"], ("meta ai",)),
    "muse": ("Meta Muse models", "meta", ["Muse Spark", "Muse Wonder"], None, ()),
}
# Every name a lab is known by. News uses a GDELT OR query; Hacker News and YouTube count each name and
# remove duplicates; Wikipedia sums the listed articles. Cursor (owned by SpaceXAI since Aug 2026) is NOT counted in xAI.
ALIASES = {
    "anthropic": {"news": '(Anthropic OR "Claude Code" OR "Claude Opus" OR "Claude Sonnet")',
                  "hn": ["Anthropic", "Claude Code", "Claude Opus", "Claude Sonnet"],
                  "yt": ["Anthropic", "Claude Code", "Claude Opus"],
                  "wiki": ["Anthropic", "Claude_(AI)"]},
    "openai": {"news": '(OpenAI OR ChatGPT OR "OpenAI Codex" OR "Sam Altman")',
               "hn": ["OpenAI", "ChatGPT", "OpenAI Codex", "Sam Altman"],
               "yt": ["OpenAI", "ChatGPT", "OpenAI Codex"],
               "wiki": ["OpenAI", "ChatGPT", "OpenAI_Codex"]},
    "xai": {"news": '(Grok OR SpaceXAI OR "SpaceX AI" OR "Grok Bot")',
            "hn": ["Grok", "SpaceXAI", "SpaceX AI", "xAI"],
            "yt": ["Grok", "SpaceXAI", "Grok Bot"],
            "wiki": ["SpaceXAI", "XAI_(company)", "Grok_(chatbot)"]},
    "meta": {"news": '("Meta AI" OR "Muse Spark" OR "Muse Wonder" OR "Meta Superintelligence" OR "Meta Llama")',
             "hn": ["Meta AI", "Muse Spark", "Muse Wonder", "Meta Superintelligence", "Meta Llama"],
             "yt": ["Meta AI", "Muse Spark", "Llama 4", "Meta Superintelligence"],
             "wiki": ["Meta_AI", "Llama_(language_model)", "Meta_Superintelligence_Labs", "Muse_Spark"]},
}
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
            if e.code == 403 and "quota" in body.lower():
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
def collect_pypi(con, windows):
    for co, pkg in PYPI.items():
        if all(have(con, w[1].isoformat(), co, "pypi") for w in windows[1:]) and have(con, windows[0][1].isoformat(), co, "pypi"):
            continue
        try:
            rows = http_json(f"https://pypistats.org/api/packages/{pkg}/overall?mirrors=false")["data"]
            daily = {r["date"]: r["downloads"] for r in rows if r["category"] == "without_mirrors"}
            for s, e in windows:
                put(con, e.isoformat(), co, "pypi", sum(v for k, v in daily.items() if s.isoformat() <= k <= e.isoformat()))
            log(f"pypi {co} ok")
        except Exception as ex:
            log(f"pypi {co} FAILED: {ex}")
        time.sleep(1.5)


def collect_npm(con, windows):
    start, end = windows[-1][0], windows[0][1]
    for co, pkg in NPM.items():
        try:
            rows = http_json(f"https://api.npmjs.org/downloads/range/{start}:{end}/{pkg}")["downloads"]
            daily = {r["day"]: r["downloads"] for r in rows}
            for s, e in windows:
                put(con, e.isoformat(), co, "npm", sum(v for k, v in daily.items() if s.isoformat() <= k <= e.isoformat()))
            log(f"npm {co} ok")
        except Exception as ex:
            log(f"npm {co} FAILED: {ex}")
        time.sleep(1)


def collect_prs(con, windows):
    """Weekly pull requests opened from each agent's default branch prefix."""
    for co, prefix in PR_PREFIX.items():
        for s, e in windows:
            d = e.isoformat()
            if have(con, d, co, "agent_prs"):
                continue
            q = f"is:pr head:{prefix} created:{s}..{e}"
            try:
                n = gh_json("search/issues?per_page=1&q=" + urllib.parse.quote(q))["total_count"]
                put(con, d, co, "agent_prs", n)
                con.commit()
                log(f"agent_prs {co} {d} = {n}")
            except Exception as ex:
                log(f"agent_prs {co} {d} FAILED: {ex}")
            time.sleep(7)  # search API: 30 requests/min plus a secondary limit


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


def yt_union(queries, musts, a, b, key, _unused=None):
    """Most-viewed videos published in the window that have any name in the title.
    One OR query per length class (medium = 4-20 min, long = 20+ min), so Shorts are never counted.
    Returns (videos, total views, top video). Costs 2 searches (200 quota units) per call."""
    q = "|".join(chr(34) + x + chr(34) for x in queries)
    seen = {}
    for length in ("medium", "long"):
        s = http_json("https://www.googleapis.com/youtube/v3/search?part=id&type=video&maxResults=50&order=viewCount"
                      f"&videoDuration={length}&q={urllib.parse.quote(q)}&publishedAfter={iso(a)}&publishedBefore={iso(b)}&key={key}")
        ids = [x["id"]["videoId"] for x in s.get("items", []) if x["id"]["videoId"] not in seen]
        if not ids:
            continue
        v = http_json(f"https://www.googleapis.com/youtube/v3/videos?part=statistics,snippet&id={','.join(ids)}&key={key}")["items"]
        for x in v:
            t = x["snippet"]["title"]
            if any(m in t.lower() for m in musts):
                seen[x["id"]] = (int(x["statistics"].get("viewCount", 0)), t, x["snippet"]["channelTitle"])
    rows = sorted(seen.values(), reverse=True)
    top = {"views": rows[0][0], "title": rows[0][1], "channel": rows[0][2]} if rows else None
    return len(rows), sum(r[0] for r in rows), top


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


def wiki_views(title, a, b):
    u = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
         f"{urllib.parse.quote(title, safe='')}/daily/{a:%Y%m%d}/{b:%Y%m%d}")
    return sum(i["views"] for i in http_json(u)["items"])


def gdelt_share(query, end):
    """Mean daily share of monitored news volume. Returns (last 7 days, prior 7 days)."""
    u = "https://api.gdeltproject.org/api/v2/doc/doc?mode=timelinevol&format=json&timespan=16d&query=" + urllib.parse.quote(query)
    pts = http_json(u, retries=6, wait429=75)["timeline"][0]["data"]
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
    parts = set(os.environ.get("ATT_PARTS", "hn,yt,wiki,news").split(","))  # re-collect only some parts
    prev = kv_get(con, "attention") or {}
    if not key:
        log("NOTE: YOUTUBE_API_KEY not set, skipping YouTube")
    def yt_pair(queries, musts):
        n1, v1, top1 = yt_union(queries, musts, cur_a, cur_b, key)
        n0, v0, _ = yt_union(queries, musts, pri_a, pri_b, key)
        return {"videos": [n1, n0], "views": [v1, v0], "top": top1}

    def part(row, old, part_name, field, fn):
        """Collect one field, or keep the stored value when this part is not being re-collected."""
        if part_name in parts:
            try:
                row[field] = fn()
            except Exception as ex:
                log(f"{part_name} {field} FAILED: {str(ex)[:100]}")
        elif field in old:
            row[field] = old[field]

    for pk, (name, lab, hn_q, yt_q, must) in PRODUCTS.items():
        row, old = {"name": name, "lab": lab}, (prev.get("products") or {}).get(pk, {})
        part(row, old, "hn", "hn", lambda: [hn_union(hn_q, cur_a, cur_b), hn_union(hn_q, pri_a, pri_b)])
        if key and yt_q:
            part(row, old, "yt", "yt", lambda: yt_pair(yt_q, must))
        out["products"][pk] = row
    for lab, al in ALIASES.items():
        row, old = {}, (prev.get("labs") or {}).get(lab, {})
        part(row, old, "wiki", "wiki", lambda: [sum(wiki_views(t, end - timedelta(days=6), end) for t in al["wiki"]),
                                                 sum(wiki_views(t, end - timedelta(days=13), end - timedelta(days=7)) for t in al["wiki"])])
        part(row, old, "hn", "hn", lambda: [hn_union(al["hn"], cur_a, cur_b), hn_union(al["hn"], pri_a, pri_b)])
        if key:
            part(row, old, "yt", "yt", lambda: yt_pair(al["yt"], [m.lower() for m in al["yt"]]))
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
    for m in ("pypi", "npm", "agent_prs"):
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
        "attention": kv_get(con, "attention"), "polymarket": kv_get(con, "polymarket"),
        "pm_names": PM_NAMES,
    }


def main():
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=12)
    ap.add_argument("--no-collect", action="store_true")
    ap.add_argument("--only", help="comma list: dev,apps,attention,market")
    args = ap.parse_args()
    TOKEN = gh_token()
    con = db()
    if not args.no_collect:
        only = set(args.only.split(",")) if args.only else {"dev", "apps", "attention", "market"}
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
