#!/usr/bin/env python3
"""SoloMatcha static site generator.

Reads content/ (guides, pages, topics.csv), site.json and affiliates.json,
renders templates/ with Jinja2 and writes a deployable site to public/.

Usage:
    python3 build.py                 # published guides only (what production runs)
    python3 build.py --drafts        # also render guides marked draft: true
    python3 build.py --out DIR       # write somewhere other than public/
    python3 build.py --today DATE    # pretend today is DATE (YYYY-MM-DD)

The build FAILS (exit 1) when a published guide has any of: a TODO( marker,
a health-claim phrase, an unknown aff: key, missing front matter, or an
unknown category. Drafts only produce warnings, so work in progress is safe.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import math
import re
import shutil
from decimal import Decimal, ROUND_HALF_UP
import sys
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent


class BuildError(Exception):
    def __init__(self, problems):
        self.problems = problems
        super().__init__("\n".join(problems))


# ---------------------------------------------------------------- loading

def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def parse_front_matter(text: str):
    """Split '---\\nyaml\\n---\\nbody' into (dict, body)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            meta = yaml.safe_load(parts[1]) or {}
            if not isinstance(meta, dict):
                raise ValueError("front matter must be a mapping")
            return meta, parts[2].lstrip("\n")
    return {}, text


def as_date(value, label):
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"{label}: bad date {value!r} (use YYYY-MM-DD)")


AFF_RE = re.compile(r'<a href="aff:([A-Za-z0-9_-]+)"')


def render_markdown(body: str, affiliates: dict, problems: list, label: str):
    """Markdown -> HTML. Returns (html, affiliate_link_count)."""
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "toc", "smarty"],
        extension_configs={"toc": {"permalink": False}},
    )
    out = md.convert(body)
    count = 0

    def repl(match):
        nonlocal count
        key = match.group(1)
        entry = affiliates.get(key)
        if not entry or not entry.get("url"):
            problems.append(f"{label}: unknown affiliate key aff:{key} (add it to affiliates.json)")
            return '<a href="#"'
        count += 1
        url = html.escape(entry["url"], quote=True)
        return f'<a href="{url}" rel="sponsored nofollow noopener" target="_blank"'

    out = AFF_RE.sub(repl, out)
    out = out.replace("<table>", '<div class="tablewrap" tabindex="0" role="region" aria-label="Table"><table>')
    out = out.replace("</table>", "</table></div>")
    return out, count


def word_count(body: str) -> int:
    return len(re.findall(r"\w+", body))


def load_guides(site, affiliates, today, include_drafts, problems, warnings):
    guides = []
    patterns = [re.compile(p, re.I) for p in site.get("health_claim_patterns", [])]
    categories = site["categories"]
    folder = ROOT / "content" / "guides"
    for path in sorted(folder.glob("*.md")):
        label = f"guides/{path.name}"
        try:
            meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        except Exception as exc:  # yaml error etc.
            problems.append(f"{label}: cannot parse front matter ({exc})")
            continue
        draft = bool(meta.get("draft", False))
        if draft and not include_drafts:
            continue
        local = warnings if draft else problems  # drafts only warn
        slug = str(meta.get("slug") or path.stem)
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            problems.append(f"{label}: slug {slug!r} must be lowercase letters, digits and hyphens")
            continue
        for field in ("title", "description", "category", "date"):
            if not meta.get(field):
                local.append(f"{label}: missing front matter field '{field}'")
        category = meta.get("category")
        if category and category not in categories:
            local.append(f"{label}: unknown category {category!r} (see site.json)")
        try:
            date = as_date(meta.get("date"), label) or today
            updated = as_date(meta.get("updated"), label)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if date > today and not draft:
            continue  # scheduled for later
        if "TODO(" in body or "TODO(" in json.dumps(meta, default=str):
            local.append(f"{label}: contains a TODO( marker - resolve it before publishing")
        for pat in patterns:
            m = pat.search(body) or pat.search(str(meta.get("description", ""))) or pat.search(str(meta.get("title", "")))
            if m:
                local.append(f"{label}: health-claim phrase {m.group(0)!r} - not allowed on this site")
        if re.search(r"^# ", body, re.M):
            local.append(f"{label}: body has a level-1 heading; the title comes from front matter, start at ##")
        desc = str(meta.get("description", ""))
        if desc and not (50 <= len(desc) <= 175):
            warnings.append(f"{label}: description is {len(desc)} chars (aim for 50-175)")
        html_body, aff_count = render_markdown(body, affiliates, local, label)
        guides.append({
            "slug": slug,
            "url": f"/guides/{slug}/",
            "title": str(meta.get("title", slug)),
            "description": desc,
            "category": category or "explainers",
            "category_label": categories.get(category, category or ""),
            "date": date,
            "updated": updated,
            "draft": draft,
            "html": html_body,
            "affiliate_count": aff_count,
            "minutes": max(1, math.ceil(word_count(body) / 200)),
        })
    guides.sort(key=lambda g: (g["date"], g["slug"]), reverse=True)
    seen = {}
    for g in guides:
        if g["slug"] in seen:
            problems.append(f"duplicate guide slug {g['slug']!r}")
        seen[g["slug"]] = True
    return guides


def load_pages(affiliates, problems, include_drafts=False):
    pages = []
    for path in sorted((ROOT / "content" / "pages").glob("*.md")):
        label = f"pages/{path.name}"
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        if meta.get("draft") and not include_drafts:
            continue
        if not meta.get("title"):
            problems.append(f"{label}: missing title")
            continue
        if "TODO(" in body and not meta.get("draft"):
            problems.append(f"{label}: contains a TODO( marker")
        html_body, _ = render_markdown(body, affiliates, problems, label)
        pages.append({
            "slug": str(meta.get("slug") or path.stem),
            "title": meta["title"],
            "description": meta.get("description") or meta["title"],
            "html": html_body,
            "footer": bool(meta.get("footer", True)),
            "no_ads": bool(meta.get("no_ads", False)),
        })
    return pages


def load_topics():
    path = ROOT / "content" / "topics.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


CURRENCY_SYMBOLS = {"USD": "$", "EUR": "\u20ac", "GBP": "\u00a3", "JPY": "\u00a5", "CAD": "CA$", "AUD": "A$"}
PRODUCT_REQUIRED = ("slug", "brand", "productName", "price", "currency", "sizeGrams", "sourceUrl", "lastUpdated")
PRODUCT_FIELDS = ("region", "cultivar", "grade", "harvestYear", "reviewScore", "availability")


def money(value, currency, places=2):
    sym = CURRENCY_SYMBOLS.get(currency, currency + "\u00a0")
    if currency == "JPY":
        places = 0 if value >= 100 else 1
    q = Decimal(str(round(value, 9))).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return f"{sym}{q:,.{places}f}"


def load_products(site, affiliates, problems):
    """data/products.json -> list of product dicts with calculated price fields."""
    data = load_json(ROOT / "data" / "products.json", {"products": []}) or {}
    cup = float(site.get("cup_grams", 2))
    out, seen = [], set()
    for i, raw in enumerate(data.get("products", [])):
        label = f"products.json[{i}] {raw.get('slug', '?')}"
        missing = [f for f in PRODUCT_REQUIRED if raw.get(f) in (None, "")]
        if missing:
            problems.append(f"{label}: missing {', '.join(missing)}")
            continue
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", raw["slug"]):
            problems.append(f"{label}: bad slug")
            continue
        if raw["slug"] in seen:
            problems.append(f"{label}: duplicate slug")
        seen.add(raw["slug"])
        if not (isinstance(raw["price"], (int, float)) and raw["price"] > 0
                and isinstance(raw["sizeGrams"], (int, float)) and raw["sizeGrams"] > 0):
            problems.append(f"{label}: price and sizeGrams must be positive numbers")
            continue
        try:
            updated = as_date(raw["lastUpdated"], label)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if raw.get("reviewScore") is not None:
            problems.append(f"{label}: reviewScore needs a published scoring method first (leave it null)")
        links = []
        for link in raw.get("affiliateLinks") or []:
            entry = affiliates.get(link.get("key", ""))
            if not entry or not entry.get("url"):
                problems.append(f"{label}: unknown affiliate key {link.get('key')!r}")
                continue
            links.append({"label": link.get("label") or entry.get("label") or "Retailer", "url": entry["url"]})
        ppg = raw["price"] / raw["sizeGrams"]
        p = {k: raw.get(k) for k in PRODUCT_FIELDS}
        p.update({
            "slug": raw["slug"], "brand": raw["brand"], "name": raw["productName"],
            "title": f"{raw['brand']} {raw['productName']}",
            "url": f"/products/{raw['slug']}/",
            "price": raw["price"], "currency": raw["currency"], "size": raw["sizeGrams"],
            "price_text": money(raw["price"], raw["currency"], 0 if raw["price"] == int(raw["price"]) else 2),
            "ppg": ppg, "ppg_text": money(ppg, raw["currency"], 2 if ppg >= 1 else 3),
            "cup": ppg * cup, "cup_text": money(ppg * cup, raw["currency"]),
            "source_url": raw["sourceUrl"], "affiliate_links": links,
            "grade_note": raw.get("gradeNote"), "notes": raw.get("notes"), "updated": updated,
        })
        out.append(p)
    out.sort(key=lambda p: (p["currency"], p["ppg"]))
    return out


def load_comparisons(products, problems):
    """data/comparisons.json: hand-picked product pairs worth a side-by-side page."""
    data = load_json(ROOT / "data" / "comparisons.json", {"comparisons": []}) or {}
    by_slug = {p["slug"]: p for p in products}
    out, seen = [], set()
    for i, c in enumerate(data.get("comparisons", [])):
        a, b = by_slug.get(c.get("a")), by_slug.get(c.get("b"))
        if not a or not b or a is b:
            problems.append(f"comparisons.json[{i}]: both 'a' and 'b' must be different slugs from products.json")
            continue
        slug = f"{a['slug']}-vs-{b['slug']}"
        if slug in seen:
            problems.append(f"comparisons.json[{i}]: duplicate pair {slug}")
        seen.add(slug)
        same = a["currency"] == b["currency"]
        diff = None
        if same:
            cheaper, dearer = sorted((a, b), key=lambda p: p["ppg"])
            diff = {"cheaper": cheaper, "pct": round((dearer["ppg"] / cheaper["ppg"] - 1) * 100)}
        out.append({"slug": slug, "url": f"/compare/{slug}/", "a": a, "b": b,
                    "title": f"{a['title']} vs {b['title']}", "same_currency": same, "diff": diff,
                    "note": c.get("note"), "updated": max(a["updated"], b["updated"])})
    return out


def load_archive(problems):
    """content/archive/YYYY/slug.md = frozen older versions of a guide."""
    items = []
    for path in sorted((ROOT / "content" / "archive").glob("*/*.md")):
        label = f"archive/{path.parent.name}/{path.name}"
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        if not meta.get("title") or not meta.get("date"):
            problems.append(f"{label}: needs title and date")
            continue
        try:
            date = as_date(meta["date"], label)
            archived = as_date(meta.get("archived"), label) or date
        except ValueError as exc:
            problems.append(str(exc))
            continue
        html_body, _ = render_markdown(body, {}, [], label)
        items.append({
            "slug": path.stem, "year": path.parent.name, "title": str(meta["title"]),
            "description": str(meta.get("description", "")), "date": date, "archived": archived,
            "current_url": f"/guides/{meta.get('current') or path.stem}/",
            "url": f"/archive/{path.parent.name}/{path.stem}/", "html": html_body,
        })
    items.sort(key=lambda a: (a["archived"], a["slug"]), reverse=True)
    return items


def archive_guide(slug: str, today: dt.date = None) -> Path:
    """Freeze the current version of a guide into content/archive/YYYY/ before you rewrite it."""
    today = today or dt.date.today()
    src = ROOT / "content" / "guides" / f"{slug}.md"
    meta, body = parse_front_matter(src.read_text(encoding="utf-8"))
    year = str(as_date(meta.get("updated") or meta.get("date"), slug).year)
    dest = ROOT / "content" / "archive" / year / f"{slug}.md"
    if dest.exists():
        n = 2
        while (dest.parent / f"{slug}-v{n}.md").exists():
            n += 1
        dest = dest.parent / f"{slug}-v{n}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta = {k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in meta.items()}
    meta["archived"] = today.isoformat()
    meta["current"] = slug
    dest.write_text("---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n\n" + body, encoding="utf-8")
    return dest


# ---------------------------------------------------------------- output

def jsonld_for(site, guide):
    data = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": guide["title"],
        "description": guide["description"],
        "datePublished": guide["date"].isoformat(),
        "dateModified": (guide["updated"] or guide["date"]).isoformat(),
        "author": {"@type": "Organization", "name": site["name"], "url": site["url"]},
        "publisher": {"@type": "Organization", "name": site["name"], "url": site["url"]},
        "mainEntityOfPage": site["url"] + guide["url"],
        "image": site["url"] + "/assets/og.png",
    }
    crumbs = {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Guides", "item": site["url"] + "/guides/"},
            {"@type": "ListItem", "position": 2, "name": guide["category_label"],
             "item": f"{site['url']}/guides/topic/{guide['category']}/"},
            {"@type": "ListItem", "position": 3, "name": guide["title"], "item": site["url"] + guide["url"]},
        ],
    }
    graph = {"@context": "https://schema.org", "@graph": [{k: v for k, v in data.items() if k != "@context"}, crumbs]}
    return json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")


def jsonld_for_home(site):
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "name": site["name"], "url": site["url"] + "/",
             "description": site["description"], "inLanguage": site["language"]},
            {"@type": "Organization", "name": site["name"], "url": site["url"] + "/",
             "logo": site["url"] + "/favicon.svg"},
        ],
    }
    return json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")


def month_groups(items):
    groups, current = [], None
    for g in items:
        label = g["date"].strftime("%B %Y")
        if current is None or current["label"] != label:
            current = {"label": label, "entries": []}
            groups.append(current)
        current["entries"].append(g)
    return groups


def dashboard(products, guides, today):
    stamps = [p["updated"] for p in products]
    return {
        "products": len(products),
        "guides": len(guides),
        "brands": len({p["brand"] for p in products}),
        "data_updated": max(stamps) if stamps else None,
    }


def top_by_currency(products):
    groups = {}
    for p in products:
        groups.setdefault(p["currency"], []).append(p)
    return [{"currency": c, "entries": sorted(v, key=lambda p: p["ppg"])} for c, v in sorted(groups.items())]


def ranking_list(products):
    """One ranking per currency; indexed only when it ranks at least two tins."""
    out = []
    for g in top_by_currency(products):
        cur = g["currency"]
        out.append({"slug": f"price-per-gram-{cur.lower()}", "url": f"/rankings/price-per-gram-{cur.lower()}/",
                    "currency": cur, "entries": g["entries"],
                    "title": f"Matcha price per gram ranking ({cur})",
                    "description": f"{len(g['entries'])} matcha tin{'' if len(g['entries']) == 1 else 's'} priced in {cur}, ranked from lowest to highest price per gram.",
                    "indexable": len(g["entries"]) >= 2})
    return out


def build_platform_pages(write, site, guides, products, archive, today, comparisons=()):
    """Section hubs, product pages, tools, newsletter, archive versions. Returns [(url, date)] for the sitemap."""
    extra = []
    name = site["name"]
    groups = top_by_currency(products)
    for sec in site.get("sections", []):
        slug, kind = sec["slug"], sec["kind"]
        items = [g for g in guides if g["category"] in sec.get("categories", [])] if kind == "guides" else []
        rank_pages = ranking_list(products) if kind == "rankings" else []
        cmp_pages = comparisons if slug == "comparisons" else []
        empty = (kind == "guides" and not items and not cmp_pages) or (kind in ("rankings", "data") and not products)
        write(f"{slug}/index.html", "section.html",
              page_title=f"{sec['label']} | {name}", page_description=sec["blurb"],
              section=sec, items=items, groups=groups, products=products, empty=empty, noindex=empty,
              rank_pages=rank_pages, cmp_pages=cmp_pages)
        if not empty:
            dates = [g["date"] for g in items] + [p["updated"] for p in products] + [c["updated"] for c in cmp_pages]
            extra.append((f"/{slug}/", max(dates) if dates else today))
    for p in products:
        write(f"products/{p['slug']}/index.html", "product.html",
              page_title=f"{p['title']}: price per gram | {name}",
              page_description=f"{p['title']} costs {p['ppg_text']} per gram ({p['price_text']} for {p['size']:g} g), checked {p['updated']:%B} {p['updated'].day}, {p['updated'].year}.",
              product=p, jsonld=jsonld_for_product(site, p))
        extra.append((p["url"], p["updated"]))
    for r in ranking_list(products):
        write(f"rankings/{r['slug']}/index.html", "ranking.html",
              page_title=f"{r['title']} | {name}", page_description=r["description"], ranking=r,
              jsonld=jsonld_for_ranking(site, r), noindex=not r["indexable"])
        if r["indexable"]:
            extra.append((r["url"], max(p["updated"] for p in r["entries"])))
    for c in comparisons:
        write(f"compare/{c['slug']}/index.html", "compare.html",
              page_title=f"{c['title']}: price and details | {name}",
              page_description=f"{c['title']} side by side: size, price, price per gram and what each seller states, with the date checked.",
              cmp=c)
        extra.append((c["url"], c["updated"]))
    write("search/index.html", "search.html", page_title=f"Search | {name}",
          page_description=f"Search {name} guides, products and tools.", noindex=True)
    if archive:
        write("archive/index.html", "archive_index.html", page_title=f"Archive of earlier versions | {name}",
              page_description="Earlier versions of SoloMatcha guides, kept for reference.", archive=archive)
        extra.append(("/archive/", archive[0]["archived"]))
        for a in archive:
            current_exists = any(g["url"] == a["current_url"] for g in guides)
            write(f"archive/{a['year']}/{a['slug']}/index.html", "archive_version.html",
                  page_title=f"{a['title']} (archived {a['archived'].year}) | {name}",
                  page_description=a["description"] or a["title"], entry=a, current_exists=current_exists,
                  canonical=site["url"] + a["current_url"] if current_exists else site["url"] + a["url"])
    return extra


def jsonld_for_ranking(site, r):
    graph = {"@context": "https://schema.org", "@type": "ItemList", "name": r["title"],
             "itemListOrder": "https://schema.org/ItemListOrderAscending",
             "itemListElement": [{"@type": "ListItem", "position": i, "name": p["title"], "url": site["url"] + p["url"]}
                                 for i, p in enumerate(r["entries"], 1)]}
    return json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")


def jsonld_for_product(site, p):
    graph = {"@context": "https://schema.org", "@type": "Product", "name": p["title"],
             "brand": {"@type": "Brand", "name": p["brand"]}, "category": "Matcha",
             "offers": {"@type": "Offer", "price": f"{p['price']:g}", "priceCurrency": p["currency"],
                        "url": p["source_url"]}}
    return json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")


def write_search_index(out_dir, site, guides, products, pages, comparisons=()):
    rows = [{"t": g["title"], "u": g["url"], "d": g["description"], "k": "Guide"} for g in guides if not g["draft"]]
    rows += [{"t": p["title"], "u": p["url"], "d": f"{p['ppg_text']} per gram, {p['price_text']} for {p['size']:g} g", "k": "Product"} for p in products]
    rows += [{"t": c["title"], "u": c["url"], "d": "Side-by-side comparison", "k": "Comparison"} for c in comparisons]
    rows += [{"t": r["title"], "u": r["url"], "d": r["description"], "k": "Ranking"} for r in ranking_list(products)]
    rows += [{"t": s["label"], "u": f"/{s['slug']}/", "d": s["blurb"], "k": "Section"} for s in site.get("sections", [])]
    (out_dir / "search.json").write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def build(out_dir: Path = None, include_drafts: bool = False, today: dt.date = None):
    out_dir = Path(out_dir or ROOT / "public")
    today = today or dt.date.today()
    problems, warnings = [], []

    site = load_json(ROOT / "site.json")
    affiliates = {k: v for k, v in (load_json(ROOT / "affiliates.json", {}) or {}).items() if not k.startswith("_")}
    guides = load_guides(site, affiliates, today, include_drafts, problems, warnings)
    pages = load_pages(affiliates, problems, include_drafts)
    topics = load_topics()
    products = load_products(site, affiliates, problems)
    archive = load_archive(problems)
    comparisons = load_comparisons(products, problems)

    if problems:
        raise BuildError(problems)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    static = ROOT / "static"
    if static.exists():
        shutil.copytree(static, out_dir, dirs_exist_ok=True)

    css_path = static / "assets" / "site.css"
    css_version = hashlib.sha1(css_path.read_bytes()).hexdigest()[:8] if css_path.exists() else "0"

    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")),
                      autoescape=select_autoescape(["html"]))
    env.filters["datefmt"] = lambda d: f"{d.strftime('%B')} {d.day}, {d.year}"

    base_ctx = {
        "site": site,
        "css_version": css_version,
        "page_slugs": [p["slug"] for p in pages],
        "footer_pages": [p for p in pages if p["footer"]],
        "total_guides": len(guides),
        "total_products": len(products),
        "nav_sections": site.get("sections", []),
        "noindex": False,
    }

    def write(rel: str, template: str, **ctx):
        data = {**base_ctx, **ctx}
        data.setdefault("canonical", site["url"] + "/" + rel.replace("index.html", "") if rel != "404.html" else site["url"] + "/")
        text = env.get_template(template).render(**data)
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    # Category counts (only categories that have guides)
    cat_counts = {}
    for g in guides:
        cat_counts[g["category"]] = cat_counts.get(g["category"], 0) + 1
    categories = [{"slug": s, "label": l, "count": cat_counts[s]}
                  for s, l in site["categories"].items() if s in cat_counts]

    # Planned topics that do not have a guide yet
    have = {g["slug"] for g in guides}
    upcoming = [t for t in topics if t.get("status") == "planned" and t["slug"] not in have][:6]

    # Home
    write("index.html", "home.html",
          page_title=f"{site['name']}: {site['tagline']}",
          page_description=site["description"],
          latest=guides[:3], upcoming=upcoming, jsonld=jsonld_for_home(site),
          products=products, dash=dashboard(products, guides, today),
          top_by_currency=top_by_currency(products))

    extra_urls = build_platform_pages(write, site, guides, products, archive, today, comparisons)

    # Archive + category pages
    write("guides/index.html", "archive.html",
          page_title=f"All guides | {site['name']}",
          page_description=f"Every {site['name']} guide on buying, grading and preparing pure matcha, newest first.",
          groups=month_groups(guides), categories=categories, current_category=None, total=len(guides))
    for c in categories:
        items = [g for g in guides if g["category"] == c["slug"]]
        write(f"guides/topic/{c['slug']}/index.html", "archive.html",
              page_title=f"{c['label']} | {site['name']}",
              page_description=f"{c['label']} guides from {site['name']}, newest first.",
              groups=month_groups(items), categories=categories, current_category=c, total=len(items))

    # Guides
    for i, g in enumerate(guides):
        newer = guides[i - 1] if i > 0 else None
        older = guides[i + 1] if i + 1 < len(guides) else None
        same = [x for x in guides if x is not g and x["category"] == g["category"]]
        rest = [x for x in guides if x is not g and x not in same]
        related = (same + rest)[:3]
        write(f"guides/{g['slug']}/index.html", "guide.html",
              page_title=f"{g['title']} | {site['name']}",
              page_description=g["description"], og_type="article",
              guide=g, older=older, newer=newer, related=related, jsonld=jsonld_for(site, g),
              noindex=g["draft"])

    # Static pages
    for p in pages:
        write(f"{p['slug']}/index.html", "page.html",
              page_title=f"{p['title']} | {site['name']}", page_description=p["description"], page=p, no_ads=p["no_ads"])

    # 404
    write("404.html", "404.html", page_title=f"Page not found | {site['name']}",
          page_description="Page not found.", noindex=True, canonical=site["url"] + "/")

    # sitemap (never lists drafts)
    live = [g for g in guides if not g["draft"]]
    urls = [(site["url"] + "/", today), (site["url"] + "/guides/", live[0]["date"] if live else today)]
    urls += [(f"{site['url']}/guides/topic/{c['slug']}/", today) for c in categories]
    urls += [(site["url"] + g["url"], g["updated"] or g["date"]) for g in live]
    urls += [(f"{site['url']}/{p['slug']}/", today) for p in pages]
    urls += [(site["url"] + u, d) for u, d in extra_urls]
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, last in urls:
        sm.append(f"  <url><loc>{xml_escape(loc)}</loc><lastmod>{last.isoformat()}</lastmod></url>")
    sm.append("</urlset>")
    (out_dir / "sitemap.xml").write_text("\n".join(sm) + "\n", encoding="utf-8")

    # RSS feed (latest 20 published)
    items = []
    for g in live[:20]:
        pub = dt.datetime(g["date"].year, g["date"].month, g["date"].day, 12, 0, tzinfo=dt.timezone.utc)
        link = xml_escape(site["url"] + g["url"])
        items.append(
            f"    <item><title>{xml_escape(g['title'])}</title><link>{link}</link>"
            f"<guid>{link}</guid><pubDate>{pub.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
            f"<description>{xml_escape(g['description'])}</description></item>")
    feed = ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>\n'
            f"    <title>{xml_escape(site['name'])}</title><link>{xml_escape(site['url'])}/</link>\n"
            f"    <description>{xml_escape(site['description'])}</description>\n"
            f"    <language>{site['language']}</language>\n" + "\n".join(items) + "\n</channel></rss>\n")
    (out_dir / "feed.xml").write_text(feed, encoding="utf-8")

    write_search_index(out_dir, site, guides, products, pages, comparisons)

    return {"guides": len(guides), "products": len(products), "drafts": sum(1 for g in guides if g["draft"]),
            "pages": len(pages), "warnings": warnings, "out": str(out_dir)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the SoloMatcha site")
    ap.add_argument("--drafts", action="store_true", help="include draft guides (marked noindex)")
    ap.add_argument("--out", default=None, help="output directory (default: public)")
    ap.add_argument("--today", default=None, help="override today's date, YYYY-MM-DD")
    ap.add_argument("--archive", metavar="SLUG", default=None,
                    help="copy guide SLUG into content/archive/YYYY/ before you rewrite it, then exit")
    args = ap.parse_args(argv)
    if args.archive:
        print(f"Archived to {archive_guide(args.archive)}")
        return 0
    today = dt.date.fromisoformat(args.today) if args.today else None
    try:
        summary = build(args.out, args.drafts, today)
    except BuildError as exc:
        print("BUILD FAILED - fix these before publishing:", file=sys.stderr)
        for p in exc.problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    for w in summary["warnings"]:
        print(f"warning: {w}")
    print(f"Built {summary['guides']} guide(s) ({summary['drafts']} draft), "
          f"{summary['pages']} page(s) -> {summary['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
