#!/usr/bin/env python3
"""Talk to the knowledge system from the terminal. Not needed by the scorer.

    python submission/ask.py                          interactive
    python submission/ask.py "60 month auto rate?"    one question
    python submission/ask.py --repairs                write the website repair list

Inside the prompt:
    why                 time machine: how the last answer's facts moved across every crawl
    as of 2026-05-31: <question>
    repairs             write the website repair list
    quit
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_eval import load_snapshots  # noqa: E402

import solution  # noqa: E402
from dupaco_ks.pages import path_of  # noqa: E402


def load():
    print("Reading five crawls of the website...", flush=True)
    ks = solution.build()
    for snap in load_snapshots(ROOT / "snapshots"):
        ks.ingest(snap)
    print(f"Ready. I found {len(ks.conflicts())} contradictions while reading.\n")
    return ks


def show(ks, question, as_of=None):
    a = ks.answer(question, as_of=as_of)
    print(("\n" if a.confident else "\n[I don't know] ") + a.text)
    for s in a.sources[:3]:
        print(f"  source: {s.url}")
    if a.conflict_note:
        print(f"  heads up: {a.conflict_note}")
    print()


def why(ks, question, as_of=None):
    lines = ks.timeline(question, as_of)
    if not lines:
        print("\nThat answer came from a passage rather than a rate table, so there is no timeline.\n")
        return
    print()
    for row, history in lines:
        name = row.label if row.label != row.product else row.product
        print(f"  {name}")
        previous = None
        for date, value in history:
            marker = "" if value == previous or previous is None else "   <-- changed"
            print(f"    {date}   {value or '(not listed)'}{marker}")
            previous = value
    print()


def repairs(ks):
    """Turn contradictions into the to-do list a website owner would actually want."""
    by_page = defaultdict(set)
    newest = ks.views[-1].id
    for f in ks.findings():
        if f.kind not in ("stale_page", "same_page"):
            continue
        for url, snapshot_id, value in f.evidence:
            # Only what is wrong today: an official page is not "stale" because its rate once differed.
            if snapshot_id == newest and value not in f.winner:
                by_page[path_of(url)].add((f.topic, value, f.winner))
    out = ["# Website repair list", "",
           f"{sum(len(v) for v in by_page.values())} stale values across {len(by_page)} pages, worst first.", ""]
    for page, items in sorted(by_page.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        out.append(f"## {page} ({len(items)})")
        out.extend(f"- {topic}: shows **{value}**, should be **{winner}**" for topic, value, winner in sorted(set(items)))
        out.append("")
    target = ROOT / "results" / "site_repairs.md"
    target.parent.mkdir(exist_ok=True)
    target.write_text("\n".join(out), encoding="utf-8")
    print(f"\nWrote {target}\n")
    print("\n".join(out[:18]))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    ks = load()
    if args == ["--repairs"]:
        return repairs(ks)
    if args:
        question = " ".join(a for a in args if a != "--why")
        show(ks, question)
        if "--why" in args:
            why(ks, question)
        return
    print("Ask about rates, branches or membership. 'why' after an answer shows its history. 'quit' to leave.\n")
    last, last_date = None, None
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line.lower() in ("quit", "exit"):
            break
        if line.lower() == "repairs":
            repairs(ks)
            continue
        if line.lower() == "why":
            why(ks, last, last_date) if last else print("Ask something first.\n")
            continue
        m = re.match(r"as of (\d{4}-\d{2}-\d{2}):\s*(.+)", line, re.I)
        last, last_date = (m.group(2), m.group(1)) if m else (line, None)
        show(ks, last, last_date)


if __name__ == "__main__":
    main()
