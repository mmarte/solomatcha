#!/usr/bin/env python3
"""Draft the next planned guide with Claude and save it as a draft file.

    python3 pipeline/generate.py              # next eligible topic, calls the API
    python3 pipeline/generate.py --slug X     # a specific topic
    python3 pipeline/generate.py --dry-run    # print the prompt, call nothing
    python3 pipeline/generate.py --stub       # write a placeholder draft, call nothing

Uses the free GEMINI_API_KEY if set, otherwise ANTHROPIC_API_KEY (except --dry-run / --stub).
If the provider is unavailable the run is skipped with a warning and exits 0. The draft is written to
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


class ProviderError(Exception):
    """The AI provider could not produce a draft (no key, quota, outage)."""


def call_gemini(prompt, key):
    """Google Gemini REST call using only the standard library (free tier works without a card)."""
    import json
    import time
    import urllib.error
    import urllib.request

    model = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 8192},
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "x-goog-api-key": key})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.load(resp)
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except urllib.error.HTTPError as exc:
            detail = " ".join(exc.read().decode("utf-8", "replace").split())[:600]
            last = f"HTTP {exc.code} from Gemini ({model}): {detail}"
            if exc.code in (429, 500, 503) and attempt < 2:
                time.sleep(20 * (attempt + 1))
                continue
            break
        except (KeyError, IndexError, ValueError) as exc:
            last = f"unexpected Gemini response ({type(exc).__name__}): {exc}"
            break
        except OSError as exc:
            last = f"network error talking to Gemini: {exc}"
            if attempt < 2:
                time.sleep(10)
                continue
    raise ProviderError(last or "unknown Gemini error")


def call_anthropic(prompt):
    try:
        import anthropic  # optional: only needed if you choose the paid provider
    except ImportError:
        raise ProviderError("ANTHROPIC_API_KEY is set but the 'anthropic' package is not installed")
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(model=MODEL, max_tokens=4000,
                                     messages=[{"role": "user", "content": prompt}])
    except Exception as exc:
        raise ProviderError(f"Anthropic call failed ({type(exc).__name__}): {exc}")
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def generate_text(prompt):
    """Free Gemini key first; paid Anthropic key only if Gemini is not configured."""
    gkey = os.environ.get("GEMINI_API_KEY")
    if gkey:
        return call_gemini(prompt, gkey)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return call_anthropic(prompt)
    raise ProviderError("no API key set. Add a free GEMINI_API_KEY secret (aistudio.google.com/apikey)")


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

    try:
        text = stub(topic, today) if args.stub else clean(generate_text(prompt))
    except ProviderError as exc:
        # Not a failure of the site: skip this run, keep the topic planned, try again next time.
        print(f"::warning title=Draft skipped::{' '.join(str(exc).split())}")
        print(f"Draft skipped, topic {topic['slug']} stays planned: {exc}")
        return 0
    if not text.startswith("---"):
        print("::warning title=Draft skipped::model output had no front matter; nothing saved")
        return 0
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
