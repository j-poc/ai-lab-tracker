Which AI vendor do businesses actually pay for?

I built a free daily scoreboard to check. It uses public data only.

Ramp customers' token spend, week to 15 Nov 2026: Anthropic 58.7%. OpenAI 37.2%. xAI 2.4%. By share of customers who pay (October), it is closer: Anthropic 44.8%, OpenAI 40.6%, xAI 6.3%. (Ramp's own customers only, not all businesses. I copied the numbers from Ramp's public page and have not checked them by hand.)

Quality depends on the board. On Arena (blind human votes, 18 Nov 2026), Anthropic's best model is #4 on text and #1 on web development. OpenAI's is #33 and #3. Ranks are not comparable across boards. Arena: https://lmarena.ai

My rule: pick the board that matches your task. Then test two models on your own work.

It runs by itself: a free daily job pulls more than 15 public sources and rebuilds the page. No server to pay for. The same method works for a business question: swap the sources for competitor prices, job postings or app ranks. Ramp's source: https://ramp.com/data/ai-index

One thing that went wrong while building it: my first version counted fewer names for some labs. It also counted some packages twice. I fixed both, recalculated 12 weeks of data and wrote the counting rule on the page. Bare words like "Grok" and "Llama" are left out for every lab, because in my sample about 3 in 10 posts using them had no AI words near the name, and my bar is 85%.

Every source and limit is on the page:
https://evoa-ai-lab-tracker.fundamental-integrity-web.workers.dev
