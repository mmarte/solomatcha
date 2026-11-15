#!/usr/bin/env python3
"""Draft the next planned guide with Claude and save it as a draft file.

    python3 pipeline/generate.py              # next eligible topic, calls the API
    python3 pipeline/generate.py --slug X     # a specific topic
    python3 pipeline/generate.py --dry-run    # print the prompt, call nothing
    python3 pipeline/generate.py --stub       # write a placeholder draft, call nothing

Needs ANTHROPIC_API_KEY (except --dry-run / --stub). The draft is written to
content/guides/<slug>.md with `draft: true`, and the topic's status in
content/topics.csv changes from planned to drafted. A human then reviews it.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOPICS = ROOT / "content" / "topics.csv"
GUIDES = ROOT / "content" / "guides"
MODEL = os.environ.get("SOLOMATCHA_MODEL", "claude-sonnet-5-5")
# Reviews need real tasting; the pipeline never drafts them.
AUTO_TYPES = {"explainer", "how-to", "buying-guide", "comparison"}


def read_topics():
    with TOPICS.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return reader.fieldnames, list(reader)


def write_topics(fields, rows):
    with TOPICS.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def pick(rows, slug=None):
    for r in rows:
        if slug and r["slug"] != slug:
            continue
        if not slug and (r["status"] != "planned" or r["type"] not in AUTO_TYPES):
            continue
        if (GUIDES / f"{r['slug']}.md").exists():
            continue
        return r
    return None


def build_prompt(topic, today):
    base = (ROOT / "pipeline" / "prompt.md").read_text(encoding="utf-8")
    facts = (ROOT / "pipeline" / "facts.md").read_text(encoding="utf-8")
    existing = sorted(p.stem for p in GUIDES.glob("*.md"))
    links = "\n".join(f"- /guides/{s}/" for s in existing) or "- (none yet)"
    return (
        f"{base}\n\n## FACTS\n{facts}\n\n## Existing guides you may link to (internal links only)\n{links}\n\n"
        f"## Assignment\nDATE: {today.isoformat()}\nSLUG: {topic['slug']}\nWORKING TITLE: {topic['title']}\n"
        f"TYPE: {topic['type']}\nCATEGORY: {topic['category']}\nBRIEF: {topic['description']}\n"
    )


def clean(text):
    text = text.strip()
    text = re.sub(r"^```(?:markdown|md)?\n", "", text)
    text = re.sub(r"\n```$", "", text)
    return text.strip() + "\n"


def stub(topic, today):
    return (
        f"---\ntitle: {topic['title']}\ndescription: {topic['description']}\n"
        f"category: {topic['category']}\ndate: {today.isoformat()}\ndraft: true\n---\n\n"
        f"## Placeholder\n\nThis is a stub draft written without calling the API.\n\n"
        f"TODO(first-hand): replace this stub.\n"
    )


def call_api(prompt):
    import anthropic  # imported late so --dry-run/--stub need no package

    client = anthropic.Anthropic()
    msg = client.messages.create(model=MODEL, max_tokens=4000,
                                 messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stub", action="store_true")
    args = ap.parse_args(argv)

    fields, rows = read_topics()
    topic = pick(rows, args.slug)
    if not topic:
        print("No eligible planned topic left. Add rows to content/topics.csv.")
        return 0
    today = dt.date.today()
    prompt = build_prompt(topic, today)
    if args.dry_run:
        print(prompt)
        return 0

    if not args.stub and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set. Add it under repo Settings > Secrets and variables > Actions.", file=sys.stderr)
        return 1
    try:
        text = stub(topic, today) if args.stub else clean(call_api(prompt))
    except Exception as exc:  # surface the real reason in the Actions log
        print(f"ERROR: the Anthropic API call failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        print(f"Model used: {MODEL}. Override with the SOLOMATCHA_MODEL environment variable.", file=sys.stderr)
        return 1
    if not text.startswith("---"):
        print("Model output has no front matter; refusing to save.", file=sys.stderr)
        return 1
    if "TODO(first-hand)" not in text:
        text += "\nTODO(first-hand): add first-hand detail (a tasting, a price paid, or a test) before publishing.\n"
    (GUIDES / f"{topic['slug']}.md").write_text(text, encoding="utf-8")
    for r in rows:
        if r["slug"] == topic["slug"]:
            r["status"] = "drafted"
    write_topics(fields, rows)
    print(f"Drafted {topic['slug']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
