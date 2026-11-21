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

# Every official package of each lab, summed, except a package that needs another counted package. Those installs already show in the
# base package, so adding them would count them twice (checked in the registries on 2026-11-19): openai-agents needs openai;
# @openai/agents needs openai; @openai/codex-sdk needs @openai/codex; @anthropic-ai/claude-agent-sdk lists @anthropic-ai/sdk as a
# peer, which npm installs too. Those four are left out. The remaining overlap is unknown, for example llama-stack needs openai.
# Every official package of each lab, summed. Agent SDKs and coding-agent CLIs count too, so no lab is judged by one package only.
# A package that depends on another one (openai-agents needs openai) adds to both counts, so these are installs, not users.
PYPI = {"anthropic": ["anthropic", "claude-agent-sdk", "claude-code-sdk"], "openai": ["openai", "openai-codex"], "xai": ["xai-sdk"],
        "meta": ["llama-api-client", "llama-models", "llama-stack-client", "llama-stack"]}
NPM = {"anthropic": ["@anthropic-ai/sdk", "@anthropic-ai/claude-code"],
       "openai": ["openai", "@openai/codex"],
       "xai": ["@xai-official/grok"],  # Grok Build CLI, published by x.ai (@ai-sdk/xai is Vercel's, not counted); xai-sdk is on PyPI
       "meta": ["llama-api-client", "llama-stack-client"]}
# Agent platforms. Two tiers, because a coding agent and a general computer agent are not the same product.
# key -> (name, lab, tier, Hacker News phrases, YouTube names, npm packages of the CLI). Meta's Muse Code has no npm or PyPI package.
# Branch-prefix pull requests are shown for claude/ and codex/ only: grok/ and muse/ branches are concentrated in a few accounts.
AGENTS = {
    "ag_claude_code": ("Claude Code", "anthropic", "coding", ["Claude Code"], ["Claude Code"], ["@anthropic-ai/claude-code"]),
    "ag_codex": ("Codex", "openai", "coding", ["Codex"], ["Codex"], ["@openai/codex"]),
    "ag_grok_build": ("Grok Build", "xai", "coding", ["Grok Build"], ["Grok Build"], ["@xai-official/grok"]),
    "ag_muse_code": ("Muse Code", "meta", "coding", ["Muse Code"], ["Muse Code"], []),
    "ag_cowork": ("Claude Cowork", "anthropic", "general", ["Claude Cowork"], ["Claude Cowork"], []),
    "ag_chatgpt_work": ("ChatGPT Work", "openai", "general", ["ChatGPT Work"], ["ChatGPT Work"], []),
    "ag_grok_bot": ("Grok Bot", "xai", "general", ["Grok Bot", "GrokBot"], ["Grok Bot", "GrokBot"], []),
    "ag_muse": ("Meta Muse agent", "meta", "general", ["Meta Muse"], ["Meta Muse"], []),
}
AGENT_PR_LAB = {"ag_claude_code": "anthropic", "ag_codex": "openai"}  # lab series whose branch prefix is this agent's own
# Default branch prefixes of cloud coding agents: (lab, prefix, metric). Meta has no verified prefix.
# Cursor (owned by SpaceXAI since Aug 2026) is a separate series, so the xAI line stays visible but labeled.
PR_SERIES = [("anthropic", "claude/", "agent_prs"), ("openai", "codex/", "agent_prs"), ("xai", "cursor/", "agent_prs_cursor")]

# GitHub stars (all time), saved daily so the weekly gain shows after a week. One repository per row, chosen by hand.
# Anthropic publishes no open weights, so it has no open-model repository. Muse Code has no public repository.
STAR_REPOS_OPEN = {"openai": "openai/gpt-oss", "xai": "xai-org/grok-1", "meta": "meta-llama/llama"}  # Grok-1 is the only xAI open-weights repo and is old
STAR_REPOS_AGENT = {"ag_claude_code": "anthropics/claude-code", "ag_codex": "openai/codex", "ag_grok_build": "xai-org/grok-build"}

# Where each coding agent is installed besides npm. VS Code Marketplace and Open VSX give all-time installs of the lab's own extension;
# Homebrew gives installs of the lab's own cask in the last 30 days. Grok Build and Muse Code have no verified entry in these.
CHANNEL_VSCODE = {"ag_claude_code": "anthropic.claude-code", "ag_codex": "openai.chatgpt"}
CHANNEL_BREW = {"ag_claude_code": "claude-code", "ag_codex": "codex"}
# Open job postings from the public job-board APIs of each lab's own hiring system. Meta has no public API of this kind.
JOBS = {"anthropic": ("greenhouse", "anthropic"), "xai": ("greenhouse", "xai"), "openai": ("ashby", "openai")}

# Arena (formerly LMArena) human-preference leaderboards. Official dataset, CC BY 4.0.
ARENA_URL = "https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset/resolve/main/{cfg}/latest-00000-of-00001.parquet"
ARENA_BOARDS = [  # key -> (title, dataset config, category)
    ("text", "Text, overall", "text", "overall"),
    ("coding", "Text, coding", "text", "coding"),
    ("webdev", "WebDev", "webdev", "overall"),
    ("vision", "Vision", "vision", "overall"),
    ("image", "Image generation", "text_to_image", "overall"),
    ("video", "Video generation", "text_to_video", "overall"),
    ("search", "Search", "search", "overall"),
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
    "muse": (6760173601, "meta", "Muse from Meta"),
    "claudeduet": (6803276000, "anthropic", "Claude Duet"),
}
APP_COUNTRIES = ["gb", "de", "in", "jp", "br"]  # Apple's public top-free chart returns the top 100 only for these countries
PRODUCTS = {  # key -> (name, lab, Hacker News phrase, YouTube query, words the video title must contain)
    "grokbot": ("Grok Bot", "xai", ["Grok Bot", "GrokBot"], ["Grok Bot", "GrokBot"], ("grok bot", "grokbot")),
    "claudecode": ("Claude + Claude Code", "anthropic", ["Claude"], ["Claude AI", "Claude Code", "Claude Opus", "Claude Fable"], ()),
    # OpenAI ships ChatGPT and Codex as one product family, so they are one row here
    "codex": ("ChatGPT + Codex", "openai", ["ChatGPT", "Codex"], ["ChatGPT", "Codex"], ()),
    "metaai": ("Meta AI app", "meta", ["Meta AI"], ["Meta AI"], ("meta ai",)),
    "muse": ("Meta Muse models", "meta", ["Muse Spark", "Muse Wonder", "Muse Ember"], ["Muse Spark", "Muse Wonder"], ("muse spark", "muse wonder")),
}
# Every name a lab is known by. Same rule for all four labs: company name, flagship products, model families and
# coding agents. No people, and no generic word that means something else too. News uses a GDELT OR query; Hacker News
# and YouTube count each name and remove duplicates; Wikipedia sums the listed articles.
# Cursor (owned by SpaceXAI since Aug 2026) is NOT counted in xAI.
# Hacker News name rule, the same for every lab (tested 2026-11-19 on the last 7 days of Hacker News):
# a name of two or more words counts. A single common word counts only if, in a sample of at least 30 posts, at least 85% had AI words
# next to it. Passed: Fable 89%, Mythos 85%, Codex 89%, Astra 90%, Sol 94%, Luna 95%, Terra 96%. Failed: Muse 71%, Llama 67%, Grok 70%.
# The failed names are left out of the main count for every lab and shown as a separate "with bare names" upper bound (HN_BARE).
HN_BARE = {"xai": ["Grok"], "meta": ["Llama", "Muse"]}
ALIASES = {
    "anthropic": {"news": '(Anthropic OR "Claude Code" OR "Claude Opus" OR "Claude Sonnet" OR "Claude Haiku" OR "Claude AI" OR "Claude Fable" OR "Claude Mythos" OR "Claude Cowork" OR "Claude Duet")',
                  "hn": ["Anthropic", "Claude", "Fable", "Mythos"],
                  "yt": ["Anthropic", "Claude AI", "Claude Code", "Claude Opus", "Claude Sonnet", "Claude Fable", "Claude Mythos", "Claude Cowork", "Claude Duet", "Claude Haiku"],
                  "wiki": ["Anthropic", "Claude_(AI)", "Claude_(language_model)", "Claude_Code", "Claude_Fable", "Claude_Mythos", "Claude_Cowork"]},
    "openai": {"news": '(OpenAI OR ChatGPT OR "OpenAI Codex" OR "Codex CLI" OR "GPT-5" OR "GPT-6" OR "gpt-oss" OR "ChatGPT Work")',
               "hn": ["OpenAI", "ChatGPT", "Codex", "GPT-5", "GPT-6", "gpt-oss", "Astra", "Sol", "Luna", "Terra"],
               "yt": ["OpenAI", "ChatGPT", "Codex", "GPT-5", "GPT-6", "ChatGPT Work", "gpt-oss"],
               "wiki": ["OpenAI", "ChatGPT", "OpenAI_Codex", "GPT-5", "GPT-6", "GPT-6_Astra"]},
    "xai": {"news": '(Grok OR GrokBot OR SpaceXAI OR "SpaceX AI" OR "Grok Bot" OR "Grok Build" OR "Grok Imagine" OR Grokipedia)',
            "hn": ["SpaceXAI", "SpaceX AI", "xAI", "GrokBot", "Grok AI", "Grok chatbot", "Grokipedia", "Grok Bot", "Grok Build", "Grok Imagine", "Grok 4", "Grok 5"],
            "yt": ["Grok", "GrokBot", "SpaceX AI", "xAI", "SpaceXAI", "Grok Bot", "Grokipedia", "Grok Build", "Grok Imagine"],
            "wiki": ["SpaceXAI", "XAI_(company)", "Grok_(chatbot)", "Grokipedia", "Grok_Build"]},
    "meta": {"news": '("Meta AI" OR "Muse Spark" OR "Muse Wonder" OR "Muse Code" OR "Muse Ember" OR "Muse Glimmer" OR "Muse from Meta" OR "Muse Charm" OR "Meta Muse" OR "Meta Superintelligence" OR "Meta Llama" OR "Llama 4")',
             "hn": ["Meta AI", "Muse from Meta", "Muse Spark", "Muse Wonder", "Muse Code", "Muse Charm", "Muse Ember", "Muse Glimmer", "Meta Muse", "Meta Superintelligence", "Meta Llama", "Llama 3", "Llama 4"],
             "yt": ["Meta AI", "Muse from Meta", "Muse Spark", "Muse Wonder", "Muse Code", "Muse Charm", "Muse Ember", "Muse Glimmer", "Meta Muse", "Meta Llama", "Llama 4", "Meta Superintelligence"],
             "wiki": ["Meta_AI", "Llama_(language_model)", "Meta_Superintelligence_Labs", "Muse_(language_model)", "Muse_(AI_agent)", "Muse_Spark", "Muse_Wonder", "Muse_Code"]},
}
YT_EXCLUDE = {"Codex": ("warhammer", "40k")}  # bare "Codex" is also a Warhammer 40k book; titles with these words do not count
# Reddit communities where each lab's users gather. Activity is counted as posts per week (Arctic Shift archive).
REDDIT = {"anthropic": ["ClaudeAI", "ClaudeCode", "claude", "Anthropic"], "openai": ["OpenAI", "ChatGPT", "codex", "ChatGPTPro"],
          "xai": ["grok", "GrokBot"], "meta": ["MetaAI"]}
PM_NAMES = {"anthropic": "Anthropic", "openai": "OpenAI", "xai": "SpaceXAI", "meta": "Meta"}
PM_ALIAS = {"xAI": "SpaceXAI"}  # Polymarket events name the same company two ways


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
    if kv_get(con, "pkg_family_v3"):
        return
    con.execute("DELETE FROM metrics WHERE metric IN ('pypi', 'npm')")
    kv_put(con, "pkg_family_v3", True)
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
    allpk = dict(NPM)
    allpk.update({k: v[5] for k, v in AGENTS.items()})  # each coding-agent CLI on its own, so the agent table is not a lab total
    for co, pkgs in allpk.items():
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


# Open-weight models on Hugging Face, by model family. Downloads over the last 30 days and likes, summed over the models of each
# lab's own organisation whose name has the family word. A tag filter is not used: it dropped Grok 2, which has no tag.
# Not counted, for every lab alike: copies and quantised versions uploaded by other people, fine-tunes, and the lab's non-language
# models (OpenAI's CLIP and Whisper). Anthropic publishes no open weights, so it has no number (n/a, not zero).
HF_FAMILIES = {"openai": ("openai", "gpt-oss"), "xai": ("xai-org", "grok"), "meta": ("meta-llama", "llama")}


def collect_hf(con, today):
    try:
        if not kv_get(con, "hf_family_v2"):  # the older rows used another rule (text-generation tag), so they cannot be compared
            con.execute("DELETE FROM metrics WHERE metric IN ('hf_downloads','hf_likes')")
            kv_put(con, "hf_family_v2", 1)
        for lab, (org, word) in HF_FAMILIES.items():
            ms = http_json(f"https://huggingface.co/api/models?author={org}&sort=downloads&direction=-1&limit=100")
            ms = [m for m in ms if word in m["id"].split("/", 1)[-1].lower()]
            put(con, today, lab, "hf_downloads", sum(int(m.get("downloads") or 0) for m in ms))
            put(con, today, lab, "hf_likes", sum(int(m.get("likes") or 0) for m in ms))
        log("hf ok")
    except Exception as ex:
        log(f"hf FAILED: {ex}")


def collect_stars(con, today):
    """GitHub stars of each lab's open-model repository and each coding agent's repository."""
    meta = kv_get(con, "repo_meta", {}) or {}
    for metric, repos in (("gh_stars_open", STAR_REPOS_OPEN), ("gh_stars", STAR_REPOS_AGENT)):
        for key, repo in repos.items():
            try:
                d = gh_json(f"repos/{repo}")
                put(con, today, key, metric, int(d["stargazers_count"]))
                meta[key] = {"repo": repo, "created": (d.get("created_at") or "")[:10], "pushed": (d.get("pushed_at") or "")[:10]}
            except Exception as ex:
                log(f"stars {repo} FAILED: {ex}")
    kv_put(con, "repo_meta", meta)
    log("stars done")


def post_json(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"User-Agent": "ai-lab-tracker", "Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def collect_channels(con, today):
    """Installs of each coding agent outside npm: VS Code Marketplace, Open VSX (all time) and Homebrew (30 days)."""
    for key, ext in CHANNEL_VSCODE.items():
        try:
            d = post_json("https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery",
                          {"filters": [{"criteria": [{"filterType": 7, "value": ext}]}], "flags": 914},
                          {"Accept": "application/json;api-version=7.1-preview.1"})
            e = d["results"][0]["extensions"][0]
            if f'{e["publisher"]["publisherName"]}.{e["extensionName"]}'.lower() != ext:
                raise RuntimeError("extension id mismatch")
            put(con, today, key, "vscode_installs", int(next(s["value"] for s in e["statistics"] if s["statisticName"] == "install")))
        except Exception as ex:
            log(f"vscode {ext} FAILED: {ex}")
        try:
            ns, name = ext.split(".")
            put(con, today, key, "openvsx_downloads", int(http_json(f"https://open-vsx.org/api/{ns}/{name}")["downloadCount"]))
        except Exception as ex:
            log(f"openvsx {ext} FAILED: {ex}")
    try:
        items = {i["cask"]: int(str(i["count"]).replace(",", "")) for i in http_json("https://formulae.brew.sh/api/analytics/cask-install/30d.json")["items"]}
        for key, cask in CHANNEL_BREW.items():
            if cask in items:
                put(con, today, key, "brew_30d", items[cask])
    except Exception as ex:
        log(f"homebrew FAILED: {ex}")
    log("channels done")


def collect_jobs(con, today):
    for lab, (kind, slug) in JOBS.items():
        try:
            if kind == "greenhouse":
                n = len(http_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")["jobs"])
            else:
                n = len(http_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")["jobs"])
            put(con, today, lab, "jobs_open", n)
        except Exception as ex:
            log(f"jobs {lab} FAILED: {ex}")
    log("jobs done")


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
        # a week is listed by its Monday; a week that is not over yet would show a drop that is not real
        weeks = [w for w in sorted(by_week) if datetime.fromisoformat(w).date() + timedelta(days=6) <= end][-8:]
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
        share = {lab: [round(100 * t / tot, 2) if tot and t else None for t, tot in zip(v, totals)] for lab, v in tokens.items()}  # None = below the top-50 cutoff, not zero use
        for lab, v in share.items():
            for w, x in zip(weeks, v):
                if x is not None:
                    put(con, w, lab, "openrouter_share", x)
        latest = weeks[-1]
        # most-used model of each lab in the newest week, with its list price (US dollars per 1M tokens)
        favs = {}
        for slug, n in by_week[latest].items():
            lab = owner.get(slug.split("/")[0]) if slug != "other" else None
            if lab and n > favs.get(lab, ("", 0))[1]:
                favs[lab] = (slug, n)
        models = {}
        try:
            for m in http_json("https://openrouter.ai/api/v1/models", retries=1)["data"]:
                models[m.get("canonical_slug")] = m
        except Exception as ex:
            log(f"openrouter model list FAILED: {ex}")
        fav_out = {}
        for lab, (slug, n) in favs.items():
            m = models.get(slug) or {}
            pr = m.get("pricing") or {}
            def per_m(x):
                try:
                    return round(float(x) * 1e6, 3)
                except (TypeError, ValueError):
                    return None
            fav_out[lab] = {"slug": slug, "name": m.get("name") or slug, "share": round(100 * n / totals[-1], 2),
                            "in_price": per_m(pr.get("prompt")), "out_price": per_m(pr.get("completion"))}
        kv_put(con, "openrouter", {"top_models": fav_out, "weeks": weeks, "share": share, "tokens": tokens, "total": totals,
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
        raise RuntimeError("the review feed returned no reviews")
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
        country_ranks = {}
        for cc in APP_COUNTRIES:
            try:
                feed = http_json(f"https://itunes.apple.com/{cc}/rss/topfreeapplications/limit=200/json")["feed"]["entry"]
                country_ranks[cc] = {e["id"]["attributes"]["im:id"]: i for i, e in enumerate(feed, 1)}
            except Exception as ex:
                log(f"apps chart {cc} FAILED: {ex}")
        for key, (tid, _lab, _name) in APPS.items():
            r = info.get(str(tid), {})
            for cc, rk in country_ranks.items():
                put(con, today, key, "app_rank_" + cc, rk.get(str(tid), 0))
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


YT_MAX_SEARCHES = 85  # per run. YouTube allows about 100 searches a day, and the title and view lookups need some quota too


def yt_search_week(name, end, length, key):
    """Get the top 50 search results for one name and duration in seven complete UTC days."""
    start = datetime.combine(end - timedelta(days=6), datetime.min.time(), timezone.utc)
    stop = start + timedelta(days=7)
    exclude = YT_EXCLUDE.get(name, ())
    query = f'"{name}"' + "".join(" -" + word for word in exclude)
    url = ("https://www.googleapis.com/youtube/v3/search?part=id&type=video&maxResults=50&order=viewCount"
           f"&videoDuration={length}&q={urllib.parse.quote(query)}&publishedAfter={iso(start)}&publishedBefore={iso(stop)}&key={key}")
    items = http_json(url)["items"]
    ids = [item["id"]["videoId"] for item in items]
    if not ids:
        return []
    items = http_json(f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics&id={','.join(ids)}&key={key}")["items"]
    videos = []
    for item in items:
        title = item["snippet"]["title"]
        text = title.lower().replace("grokking", "")
        if name.lower() in text and not any(word in text for word in exclude):
            count = item["statistics"].get("viewCount")
            if count is not None:
                videos.append([item["id"], int(count), title, item["snippet"]["channelTitle"]])
    return videos


def yt_collect(con, key, end, names):
    state = kv_get(con, "yt_weekly", {}) or {}
    if state.get("end") != end.isoformat():
        state = {"end": end.isoformat(), "results": {}}
    results = state["results"]
    missing = [(name, length) for name in names for length in ("medium", "long")
               if f"{name}|{length}" not in results]
    for name, length in missing[:YT_MAX_SEARCHES]:
        try:
            results[f"{name}|{length}"] = yt_search_week(name, end, length, key)
        except RuntimeError as ex:
            log(f"YouTube search failed for {name}: {ex}")
            if "quota exhausted" in str(ex):
                break
            continue
        kv_put(con, "yt_weekly", state)
        con.commit()
    state["ready"] = all(f"{name}|{length}" in results for name in names for length in ("medium", "long"))
    return state


def yt_row(st, hist, rowkey, names, today):
    if not st.get("ready"):
        return None
    end = datetime.fromisoformat(st["end"]).date()
    seen = {}
    for name in names:
        for length in ("medium", "long"):
            for video, views, title, channel in st["results"][f"{name}|{length}"]:
                seen[video] = (views, title, channel)
    cur = sorted(seen.values(), reverse=True)
    h = hist.setdefault(rowkey, {})
    h[today.isoformat()] = [len(cur), sum(r[0] for r in cur)]
    for d in [d for d in h if (today - datetime.fromisoformat(d).date()).days > 40]:
        del h[d]
    prior = next((h[d] for d in ((today - timedelta(days=b)).isoformat() for b in (7, 6, 8)) if d in h), None)
    top = {"views": cur[0][0], "title": cur[0][1], "channel": cur[0][2]} if cur else None
    return {"videos": [len(cur), prior[0] if prior else None], "views": [sum(r[0] for r in cur), prior[1] if prior else None],
            "top": top, "asof": end.isoformat()}


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
    """Mean daily share of monitored news volume. Returns (last 7 days, prior 7 days, last day used).
    GDELT sometimes lags by a day or two, and today's day is partial, so the windows end on the newest full day it has (at most 3 days late)."""
    u = "https://api.gdeltproject.org/api/v2/doc/doc?mode=timelinevol&format=json&timespan=20d&query=" + urllib.parse.quote(query)
    pts = http_json(u, retries=3, wait429=30)["timeline"][0]["data"]
    by_day = {}
    for p in pts:
        by_day.setdefault(p["date"][:8], []).append(p["value"])
    days = {datetime.strptime(d, "%Y%m%d").date(): sum(v) / len(v) for d, v in by_day.items()}
    days.pop(utc_today(), None)  # today is partial
    if not days:
        raise RuntimeError("no days")
    last_day = min(end, max(days))
    if (end - last_day).days > 3:
        raise RuntimeError(f"newest day is {last_day}")
    last = [days.get(last_day - timedelta(days=i)) for i in range(7)]
    prior = [days.get(last_day - timedelta(days=i)) for i in range(7, 14)]
    last, prior = [x for x in last if x is not None], [x for x in prior if x is not None]
    if len(last) < 5 or len(prior) < 5:
        raise RuntimeError("not enough days")
    return sum(last) / len(last), sum(prior) / len(prior), last_day


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
        parts.discard("yt")
        log("NOTE: YOUTUBE_API_KEY not set, skipping YouTube")
    today = utc_today()
    out["yt_window"] = prev.get("yt_window")  # replaced below when the new rolling window is complete
    yt_state, yt_hist = {}, kv_get(con, "yt_weekly_hist", {}) or {}
    if key and "yt" in parts:
        # Reuse each weekly search across all lab, product, and agent rows.
        names = sorted({n for al in ALIASES.values() for n in al["yt"]} | {n for pv in PRODUCTS.values() if pv[3] for n in pv[3]}
                       | {n for ag in AGENTS.values() for n in ag[4]})
        yt_state = yt_collect(con, key, end, names)
        if yt_state.get("ready"):
            out["yt_window"] = f"{end - timedelta(days=6)} to {end}"

    def part(row, old, part_name, field, fn):
        """Collect one field, or keep the stored value when this part is not being re-collected."""
        if part_name in parts:
            try:
                val = fn()
                row[field] = old[field] if val is None and field in old else val  # nothing new: keep the last value
            except Exception as ex:
                log(f"{part_name} {field} FAILED: {str(ex)[:100]}")
                if field in old:  # keep the last good value, and say it is old
                    row[field] = old[field]
                    row.setdefault("stale", {})[field] = (old.get("stale") or {}).get(field) or prev.get("as_of")
        elif field in old:
            row[field] = old[field]
            if field in (old.get("stale") or {}):
                row.setdefault("stale", {})[field] = old["stale"][field]

    for pk, (name, lab, hn_q, yt_q, must) in PRODUCTS.items():
        row, old = {"name": name, "lab": lab}, (prev.get("products") or {}).get(pk, {})
        part(row, old, "hn", "hn", lambda: [hn_union(hn_q, cur_a, cur_b), hn_union(hn_q, pri_a, pri_b)])
        if yt_q:
            part(row, old, "yt", "yt", lambda: yt_row(yt_state, yt_hist, f"prod:{pk}", yt_q, today))
        out["products"][pk] = row
    out["agents"] = {}
    for ak, (aname, alab, tier, ahn, ayt, _pk) in AGENTS.items():
        row, old = {"name": aname, "lab": alab, "tier": tier}, (prev.get("agents") or {}).get(ak, {})
        part(row, old, "hn", "hn", lambda: [hn_union(ahn, cur_a, cur_b), hn_union(ahn, pri_a, pri_b)])
        part(row, old, "yt", "yt", lambda: yt_row(yt_state, yt_hist, f"ag:{ak}", ayt, today))
        out["agents"][ak] = row
    for lab, al in ALIASES.items():
        row, old = {}, (prev.get("labs") or {}).get(lab, {})
        part(row, old, "wiki", "wiki", lambda: [sum(wiki_views(t, end - timedelta(days=6), end) for t in al["wiki"]),
                                                 sum(wiki_views(t, end - timedelta(days=13), end - timedelta(days=7)) for t in al["wiki"])])
        part(row, old, "hn", "hn", lambda: [hn_union(al["hn"], cur_a, cur_b), hn_union(al["hn"], pri_a, pri_b)])
        if lab in HN_BARE:
            both = al["hn"] + HN_BARE[lab]
            part(row, old, "hn", "hn_bare", lambda: [hn_union(both, cur_a, cur_b), hn_union(both, pri_a, pri_b)])
        part(row, old, "yt", "yt", lambda: yt_row(yt_state, yt_hist, f"lab:{lab}", al["yt"], today))
        def reddit():
            subs = {r: [reddit_posts(r, end - timedelta(days=6), end), reddit_posts(r, end - timedelta(days=13), end - timedelta(days=7))] for r in REDDIT[lab]}
            row["reddit_subs"] = subs
            return [sum(v[0] for v in subs.values()), sum(v[1] for v in subs.values())]
        if "reddit" in parts:
            try:
                row["reddit"] = reddit()
            except Exception as ex:
                log(f"reddit {lab} FAILED: {str(ex)[:100]}")
                if "reddit" in old:
                    row["reddit"], row["reddit_subs"] = old["reddit"], old.get("reddit_subs")
                    row.setdefault("stale", {})["reddit"] = (old.get("stale") or {}).get("reddit") or prev.get("as_of")
        elif "reddit" in old:
            row["reddit"], row["reddit_subs"] = old["reddit"], old.get("reddit_subs")
        if "news" in parts:
            try:
                nl, npr, nday = gdelt_share(al["news"], end)
                row["news"] = [nl, npr]
                if nday != end:
                    row["news_asof"] = nday.isoformat()  # GDELT is a day or two behind
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
    kv_put(con, "yt_weekly_hist", yt_hist)
    log("attention ok")


# ---------- market odds ----------
def collect_polymarket(con):
    try:
        d = http_json("https://gamma-api.polymarket.com/public-search?q=best%20ai%20model&limit_per_type=10&events_status=active")
        now = datetime.now(timezone.utc).isoformat()
        evs = [e for e in d.get("events", []) if e.get("slug", "").startswith("which-company-has") and "livebench" not in e["slug"] and e.get("endDate", "") > now]
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
                odds[PM_ALIAS.get(name, name)] = float(json.loads(m["outcomePrices"])[0]) * 100
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


def name_lists():
    """The names each panel counts, written from the same lists the collectors use, so the page text cannot drift from the code."""
    def join(f):
        return " | ".join(f"{COMPANIES[lab]}: " + ", ".join(f(aliases)) for lab, aliases in ALIASES.items())
    news = lambda al: [x.strip().strip('"') for x in re.sub(r"[()]", "", al["news"]).split(" OR ")]
    return {"yt": join(lambda al: al["yt"]), "hn": join(lambda al: al["hn"]), "news": join(news),
            "wiki": join(lambda al: [w.replace("_", " ") for w in al["wiki"]]),
            "agents": " | ".join(a[0] if a[3] == [a[0]] else f"{a[0]} ({', '.join(a[3])})" for a in AGENTS.values()),
            "reddit": " | ".join(", ".join("r/" + r for r in REDDIT[lab]) for lab in ALIASES)}


def build(con):
    data = load(con)
    labs = list(COMPANIES)

    dev = {}
    for m in ("pypi", "npm", "hf_downloads", "hf_likes", "gh_stars_open", "jobs_open", "agent_prs", "agent_prs_cursor"):
        ser = data.get(m, {})
        if not ser:
            continue
        latest = max(ser)
        ld = datetime.fromisoformat(latest).date()
        # Weekly points only: the date 7, 14, 21 ... days before the newest one. Older runs saved windows that end on
        # other days, and comparing those would mix overlapping weeks (one day of change shown as a week of change).
        dates = sorted(d for d in ser if (ld - datetime.fromisoformat(d).date()).days % 7 == 0)
        prev = (ld - timedelta(days=7)).isoformat()
        rows = {"dates": dates, "series": {co: [ser[d].get(co) for d in dates] for co in labs}, "latest": ser[latest], "as_of": latest}
        rows["wow"] = {co: (100 * (ser[latest][co] - ser[prev][co]) / ser[prev][co]
                            if prev in ser and ser[latest].get(co) is not None and ser[prev].get(co) else None) for co in labs}
        week = lambda k: (ld - timedelta(days=7 * k)).isoformat()
        if all(week(k) in ser for k in range(8)):
            lastn = {co: sum(ser[week(k)].get(co, 0) for k in range(4)) for co in labs}
            prior = {co: sum(ser[week(k)].get(co, 0) for k in range(4, 8)) for co in labs}
            rows["growth"] = {co: (100 * (lastn[co] - prior[co]) / prior[co] if prior[co] and co in ser[latest] else None) for co in labs}
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
        rr = [d for d in dates if (data.get("app_review_rate", {}).get(d, {}).get(key) or 0) > 0 and (datetime.fromisoformat(now) - datetime.fromisoformat(d)).days <= 14]
        rr_day = rr[-1] if rr else None  # newest good review reading, when today's feed came back empty
        apps.append({"name": name, "lab": lab, "as_of": now, "ratings": rs[now][key], "gained_7d": gained, "stars": g("app_stars"),
                     "rank": g("app_rank"), "prod_rank": g("app_prodrank"),
                     "countries": {cc: data.get("app_rank_" + cc, {}).get(now, {}).get(key) for cc in APP_COUNTRIES},
                     "review_rate": data["app_review_rate"][rr_day][key] if rr_day else None,
                     "review_stars": data["app_review_stars"][rr_day][key] if rr_day else None, "review_asof": rr_day})

    # Agent platforms: the attention rows (Hacker News, YouTube) plus each coding agent's own npm downloads and, for claude/ and codex/,
    # branch-prefix pull requests, both with the exact week before.
    att = kv_get(con, "attention") or {}
    agents = {}
    for k, (aname, alab, tier, _h, _y, pk) in AGENTS.items():
        r = dict((att.get("agents") or {}).get(k, {}))
        r.update({"name": aname, "lab": alab, "tier": tier, "has_npm": bool(pk)})
        npm = data.get("npm", {})
        if pk and npm:
            nl = max(npm)
            np_ = (datetime.fromisoformat(nl).date() - timedelta(days=7)).isoformat()
            r["npm"] = [npm[nl].get(k), npm.get(np_, {}).get(k)]
            r["npm_asof"] = nl
        def pair7(metric):
            ser = data.get(metric, {})
            ds = sorted(d for d in ser if k in ser[d])
            if not ds:
                return None
            prev_d = (datetime.fromisoformat(ds[-1]).date() - timedelta(days=7)).isoformat()
            return [ser[ds[-1]][k], ser.get(prev_d, {}).get(k)]
        for fld, metric in (("vscode", "vscode_installs"), ("openvsx", "openvsx_downloads"), ("brew", "brew_30d")):
            if pair7(metric):
                r[fld] = pair7(metric)
        st = data.get("gh_stars", {})
        if k in STAR_REPOS_AGENT and st:
            sl = max(d for d in st if k in st[d])
            sp = (datetime.fromisoformat(sl).date() - timedelta(days=7)).isoformat()
            r["stars"] = [st[sl].get(k), st.get(sp, {}).get(k)]
            r["stars_asof"] = sl
        pl = AGENT_PR_LAB.get(k)
        if pl and data.get("agent_prs"):
            pr = data["agent_prs"]
            pn = max(pr)
            pp = (datetime.fromisoformat(pn).date() - timedelta(days=7)).isoformat()
            r["prs"] = [pr[pn].get(pl), pr.get(pp, {}).get(pl)]
            r["prs_asof"] = pn
        agents[k] = r

    manual = json.loads(MANUAL.read_text()) if MANUAL.exists() else {}
    return {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "companies": COMPANIES, "colors": COLORS, "products": {k: {"name": v[0], "lab": v[1]} for k, v in PRODUCTS.items()},
        "ramp": manual.get("ramp_ai_index"), "dev": dev, "apps": apps,
        "attention": kv_get(con, "attention"), "arena": kv_get(con, "arena"), "polymarket": kv_get(con, "polymarket"),
        "radar": kv_get(con, "radar"), "repo_meta": kv_get(con, "repo_meta"), "openrouter": kv_get(con, "openrouter"),
        "pm_names": PM_NAMES, "names": name_lists(), "agents": agents,
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
            collect_hf(con, today)
            collect_stars(con, today)
            collect_channels(con, today)
            collect_jobs(con, today)
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
