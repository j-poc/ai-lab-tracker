# Which AI vendor are businesses actually paying for? I built a free daily scoreboard to find out.

If you must pick an AI vendor for your team, most advice is opinion. I wanted numbers. So I built a tracker for Anthropic, OpenAI, xAI and Meta. It uses only free, public data. It runs every day. It costs nothing to run.

Live page: https://evoa-ai-lab-tracker.fundamental-integrity-web.workers.dev

## The answer on money

Ramp is a business card and spend tool. It sees what its own customers pay for. This is not all businesses, only Ramp's.

- In October, 44.8% of Ramp customers paid for Anthropic. 40.6% paid for OpenAI. 6.3% paid for xAI. Ramp does not report Meta.
- In token spend (week to 15 November), Anthropic had 58.7% and OpenAI had 37.2%. xAI had 2.4%.
- A month earlier (week to 18 October) it was 56.9% and 38.5%. That is two data points, so I will not call it a trend yet.

Source: Ramp's public AI Index (https://ramp.com/data/ai-index). I copy its numbers from the page. I have not checked them by hand.

## The answer on quality: it depends on your task

Arena (https://lmarena.ai) ranks models by human votes in blind tests. The leader changes by board (snapshots from 17 and 18 November):

| Board | Anthropic best | OpenAI best | Meta best | xAI best |
|---|---|---|---|---|
| Text | #4 | #33 | #7 | #52 |
| Web development | #1 | #3 | #12 | #28 |
| Images | not listed | #1 | #13 | #9 |

Google's Gemini 4 Carbon holds the top text spot. The table shows each lab's best model that is listed on that board. Ranks are among all models on that board, and they are not comparable across boards.

**A rule you can copy:** do not pick a vendor from one overall rank. Pick the board that matches your task, then test two models on your own work.

## For the technical reader

- **Pull requests.** Agents name their branches, and I count the names. Branches that start with `claude/` made 820,554 pull requests in the 7 days to 18 November. `codex/` made 280,575. Over the last 4 weeks together, against the 4 before, `claude/` is up 35% and `codex/` is up 4%. But `codex/` is falling: this week is below its late-October level of about 327,000. Caution: the `claude/` line nearly doubled in about a week from 23 September. That is right after the date in the Claude Opus 5.5 model ID (21 September), so a model release is the likely cause, but I have not verified it. Read it as a one-time jump, not steady growth.
- **Downloads.** Weekly downloads of official packages. Python: OpenAI 92.4 million, Anthropic 61.0 million. npm: Anthropic 94.4 million, OpenAI 88.8 million. Codex had 27.3 million npm downloads and Claude Code 20.2 million. Codex leads Homebrew. Claude Code leads VS Code installs. xAI and Meta are far lower, and their numbers undercount, because many of their users do not use a package. A package that depends on another counted package is left out, so nothing is counted twice.
The page also shows Hacker News mentions, open-model downloads, app charts, Polymarket odds, OpenRouter usage and job postings, each with its source and limits.

## How I built it, and what went wrong

A small Python script collects data from more than 15 public sources each morning, saves the numbers in a database and rebuilds the page. A free GitHub job runs it. A free Cloudflare site shows it. There is no server to pay for, and nothing to run by hand. The same kind of pipeline works for a business question. Swap the sources: for example, competitor prices, job postings and app ranks, collected each morning into one page with dates and limits. I have not built that exact one; this tracker is the proof that the method works.

One rule for all four labs: I count the company name, its products, its model families and its coding agents. I do not count people. A common single word counts only if, in a sample, AI words are nearly always near it. My bar is 85% of a sample. "Codex" (89%) and "Fable" (89%) pass. "Llama" (67%), "Grok" (70%) and "Muse" (71%) do not. So they are left out for every lab, and shown as an upper limit. The test is rough: it checks the words near the name.

My first version was not fair. It counted fewer names for some labs, which made them look quieter. A review then found packages counted twice, and a "same rule" claim that did not hold for single words. I fixed both, recalculated 12 weeks of package data, and wrote the rule on the page. If you think a name is missing, tell me.

## What it does not show

- X is not measured. There is no free data.
- Downloads undercount xAI and Meta. Many of their users do not use a package.
- Branch names show only agents that name their branches.
- App ratings are US iPhone only. No Android, web or Mac.
- Some sources can lag when a free service limits requests. The page marks old values.

Every panel on the page lists its source, date and limits.

Which AI vendor decision are you facing? Reply and tell me. I will say which of these numbers matter for it, and which do not.
