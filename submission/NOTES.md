# How this thing works

Quick version: you get five downloads of a credit union's website, the pages disagree with each
other, and the bot has to work out what's actually true today.

You don't need any API keys or extra packages. It's all plain Python.

## My approach

Most of the questions are about a specific number, like "what's the 60-month auto loan rate?" And
the scorer is harsh: if the old number shows up anywhere in your answer, you get zero for that
question, even if the right number is there too.

So I didn't want a model picking numbers. Instead, the code reads the website once, turns it into
organized facts, and then just looks things up. Same question, same answer, every time, and it's
fast.

## Let's follow one question

Say someone asks: *"What's the APR for a 60-month auto loan on a 2025 model-year vehicle?"*

**First, the reading part (this happens once, at the start).**

Each download gets stored separately and nothing gets overwritten. So if someone asks about May, I
just open the May copy.

Every rate table gets turned into facts. A row like `2024-27 Model Year | 60 Months | 5.39% APR1`
becomes: auto loan, 60 months, years 2024 to 2027, 5.39%. (That little "1" at the end is a
footnote, so it gets thrown away.)

Then every page gets a trust level. `/rates` is the top. Product pages come next, and calculators
and blog posts are near the bottom. For each product, I only keep the version from the most trusted
page. That's why the old 5.34% loses: it's sitting on a calculator page nobody updated, and `/rates`
says 5.39%.

**Then, the answering part.**

1. Figure out what's being asked: auto loan, 60 months, 2025 car, wants a rate.
2. No date mentioned, so use the newest download.
3. Hand the question down a line of handlers (links, contact info, branches, rates, and so on).
   Each one either answers it or passes it along. This one lands on rates.
4. Narrow it down: auto rows, then the year band that includes 2025, then 60 months. One row left.
5. Reply with 5.39% and the page it came from.

If another page disagrees, that goes in `conflict_note`, so the old number never ends up in the
actual answer.

**A few other paths a question can take:**

- *"What was it as of May 2026?"* Same steps, but on the May copy of the site.
- *"What's your 7/1 ARM rate?"* The site mentions it but never gives a rate, so the bot says it
  doesn't know. It doesn't guess.
- *"What is Home$tart?"* There's no table for that, so keyword search finds the best page and
  quotes the most useful sentences.

## How I decide what's "current"

In this order:

1. The newest download beats older ones, unless the question gives a date.
2. Inside one download, the more trusted page wins.
3. If two pages tie, the later "Rates are effective as of" date wins.
4. For press releases and blog posts, I go by the date the post was published. Every old post gets
   re-downloaded each time, so a 2022 article can look brand new if you only check the download
   date. That's how the branch count gets settled: the newest post wins, not the most common number.

Things like 48 vs 60 months, or $10k vs $25k balances, are treated as different facts. So a table
with lots of tiers doesn't look like the site is contradicting itself.

## What's in which file

| File | What it does |
|---|---|
| `solution.py` | The front door: `ingest`, `answer`, `conflicts` |
| `dupaco_ks/tables.py` | Turns rate tables into facts |
| `dupaco_ks/pages.py` | Decides how much to trust each page |
| `dupaco_ks/store.py` | Everything learned from one download (rates, hours, branch counts, addresses) |
| `dupaco_ks/question.py` | Works out what the person is asking |
| `dupaco_ks/answers.py` | Finds the answer, or says "I don't know" |
| `dupaco_ks/search.py` | Keyword search for the non-rate questions |
| `dupaco_ks/conflicts.py` | Finds where the site contradicts itself |
| `ask.py` | A little terminal app for trying it yourself |

## Asking the bot questions

The website downloads aren't in this repo, since we were asked to leave them out. Put the kit's
`snapshots_zip` folder at the top level first. It unpacks itself the first time you run anything.

Run these from the top folder. To chat with it:

```bash
python submission/ask.py
```

You'll get a prompt where you can type questions:

```
> What's the 60-month auto loan rate?
2024-27 Model Year 60 Months: 5.39% APR. Rates effective as of 2026-08-31.
  source: https://www.dupaco.com/rates
```

A couple of fun things to type there:

- `why` shows how the last answer changed across all five downloads. I call it the time machine.
- `as of 2026-05-31: What's the 10-month jumbo certificate rate?` answers as the site stood then.
- `repairs` writes a list of every stale value, page by page, to `results/site_repairs.md`. It's
  basically a to-do list for whoever runs the website.
- `quit` gets you out.

Or ask just one question:

```bash
python submission/ask.py "What's the 30-year mortgage rate?" --why
```

To run the actual scorer:

```bash
python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl
```

## How it did

On the 40 practice questions: **0.9457**, no stale numbers, fully consistent, about 0.01 seconds per
answer. Reading all five downloads takes about 4 seconds.

It missed two, and I left both alone on purpose:

- `dup-088` wants 294 employees (from 2023), but a newer post on the site says 256 (from 2024). The
  bot says 256. I could have forced 294 to get the point, but then it'd be giving the older number.
- `dup-005` has no expected values, so the scorer gives it zero no matter what you answer.

One more oddity: the expected answer for `dup-011` says the 15-year mortgage is 6.25%, but `/rates`
says 6.75% (6.25% is the HELOC). The bot goes with the website. That part isn't scored anyway.

## What's not great yet

- I couldn't test the conflicts score, because the real answer list isn't in the kit.
- It's built for this one website. The product names and trust levels are written for Dupaco, so
  another credit union would need its own setup.
- The keyword search answers are the weakest part. Sometimes it quotes a so-so sentence.
- Branch hours only show up in old press releases, so the bot says hours vary by branch.

If I kept going, I'd try an LLM for oddly worded questions and for summarizing the search answers.
But I'd only keep it if it actually fixed misses, and I still wouldn't let it pick the numbers.
