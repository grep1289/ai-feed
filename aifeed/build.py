"""Daily pipeline: fetch sources, filter, pick a capped digest, render the site.

Run:  python -m aifeed.build            # normal daily run
      python -m aifeed.build --check    # fetch every source and print its health, change nothing
      python -m aifeed.build --force    # rebuild today's digest even if it already exists
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

from . import page

ROOT = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (compatible; ai-feed/1.0; +https://github.com/grep1289/ai-feed)"
PRERELEASE = re.compile(
    r"(?i)(^|[-._/@\d])(rc|alpha|beta|dev|nightly|canary|pre|preview)([-._\d]|$)|\d+\.\d+(\.\d+)?(a|b|rc)\d+$"
)
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
BACKFILL, WILD = 1, 3   # candidate groups; see candidates()
_sleep = time.sleep      # replaced in tests
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class FetchError(Exception):
    pass


# --------------------------------------------------------------------- config
def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def load_sources(cfg: dict) -> list[dict]:
    """Flatten the four sections of sources.yaml into one list of source dicts."""
    out = []
    for f in cfg.get("feeds", []):
        out.append({
            **f,
            "kind": "page" if f.get("status") == "page_watch" else "rss",
            "item_kind": "release" if f.get("treat_as") == "release" else "read",
        })
    for y in cfg.get("youtube", []):
        out.append({**y, "kind": "youtube", "item_kind": "watch"})
    for group, repos in cfg.get("github", {}).items():
        for r in repos:
            out.append({
                **r,
                "id": "gh-" + r["repo"].replace("/", "-").lower(),
                "name": r["repo"],
                "kind": "github",
                "item_kind": "release",
                "tier": 1,
                "group": group,
            })
    for w in cfg.get("wildcard", []):
        out.append({**w, "kind": "rss", "item_kind": "read", "tier": "wildcard"})
    return out


def source_urls(src: dict, cfg: dict) -> list[str]:
    """Addresses to try for a source, in order."""
    if src["kind"] == "youtube":
        yt = cfg["filters"]["youtube"]
        cid = src["channel_id"]
        return [
            yt["feed_template"].format(uploads_playlist="UULF" + cid[2:]),
            yt["fallback_template"].format(channel_id=cid),
        ]
    if src["kind"] == "github":
        if src.get("feed") == "commits":
            return [f"https://github.com/{src['repo']}/commits/{src.get('branch', 'main')}.atom"]
        return [f"https://github.com/{src['repo']}/releases.atom"]
    return [src["url"]]


# ---------------------------------------------------------------------- fetch
def http_get(url: str, tries: int = 3, timeout: int = 25) -> bytes:
    last = "no attempt"
    for attempt in range(tries):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
            if r.status_code == 200:
                return r.content
            last = f"HTTP {r.status_code}"
            if r.status_code < 500 and r.status_code != 429:
                break  # a 404 will not get better by retrying
        except requests.RequestException as e:
            last = type(e).__name__
        time.sleep(2 * (attempt + 1))
    raise FetchError(last)


def normalize_url(url: str) -> str:
    """Stable key for dedupe: no fragment, no tracking parameters, no trailing slash."""
    p = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(p.query) if not (k.lower().startswith("utm_") or k.lower() == "ref")]
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(query), ""))


def clean_text(html: str, limit: int = 400) -> str:
    text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def parse_feed(content: bytes) -> list[dict]:
    parsed = feedparser.parse(content)
    if not parsed.entries and (parsed.bozo or not parsed.get("version")):
        raise FetchError("not a feed")  # e.g. a blog that now redirects its feed address to a web page
    items = []
    for e in parsed.entries:
        link = e.get("link")
        title = clean_text(e.get("title", ""), 300)
        if not link or not title:
            continue
        stamp = e.get("published_parsed") or e.get("updated_parsed")
        published = dt.datetime(*stamp[:6], tzinfo=dt.timezone.utc).date() if stamp else None
        items.append({
            "url": normalize_url(link),
            "title": title,
            "snippet": clean_text(e.get("summary", "")),
            "published": published,
            "categories": [t.get("term") for t in e.get("tags", []) if t.get("term")],
        })
    return items


def parse_page(content: bytes, base: str, link_pattern: str) -> list[dict]:
    """For sites with no feed: collect links on a listing page that look like posts."""
    soup = BeautifulSoup(content, "html.parser")
    rx = re.compile(link_pattern)
    found: dict[str, tuple[bool, str]] = {}
    for a in soup.find_all("a", href=True):
        url = normalize_url(urljoin(base, a["href"]))
        if not rx.match(url):
            continue
        heading = a.find(["h1", "h2", "h3", "h4"])
        title = clean_text(str(heading) if heading else str(a), 200)
        # Prefer a heading inside the link; otherwise the longest link text.
        rank = (bool(heading), title)
        if title and (url not in found or (rank[0], len(title)) > (found[url][0], len(found[url][1]))):
            found[url] = rank
    return [{"url": u, "title": t, "snippet": "", "published": None, "categories": []}
            for u, (_, t) in found.items()]


def fetch_source(src: dict, cfg: dict, get=http_get) -> tuple[list[dict], str | None]:
    """Return (items, error). A failed source is reported, never fatal."""
    error = None
    for url in source_urls(src, cfg):
        try:
            content = get(url)
            if src["kind"] == "page":
                items = parse_page(content, url, src["link_pattern"])
            else:
                items = parse_feed(content)
            if not items and src["kind"] == "youtube":
                error = "empty feed"
                continue  # try the fallback address
            for it in items:
                it["source"] = src["id"]
                if src["kind"] == "github":
                    it["tag"] = it["url"].rsplit("/", 1)[-1]
            return items, None
        except FetchError as e:
            error = str(e)
    return [], error


# --------------------------------------------------------------------- filter
def keyword_regex(words: list[str]) -> re.Pattern:
    return re.compile("|".join(r"\b" + re.escape(w) for w in words), re.I)


def is_major(tag: str) -> bool:
    m = VERSION.findall(tag or "")
    if not m:
        return False
    major, minor, patch = (int(x) for x in m[-1])
    return major >= 1 and minor == 0 and patch == 0


def drop_reason(item: dict, src: dict, cfg: dict) -> str | None:
    """Step 1 of filtering: mechanical rules. Returns why an item is dropped, or None."""
    f = cfg["filters"]
    keep = src.get("keep_categories")
    if keep and not set(item.get("categories", [])) & set(keep):
        return "category"
    words = src.get("include_any")
    if words and not keyword_regex(words).search(item["title"] + " " + item.get("snippet", "")):
        return "keyword"
    for pattern in f.get("drop_title_regex", []):
        if re.search(pattern, item["title"], re.I):
            return "title_rule"
    if src["item_kind"] == "release" and src.get("feed") != "commits" and f.get("drop_prereleases"):
        if PRERELEASE.search(item.get("tag") or item["title"]):
            return "prerelease"
    return None


# ---------------------------------------------------------------------- score
def extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON in reply")
    return json.loads(text[start:end + 1])


def call_model(prompt: str, rubric: dict, api_key: str, post=requests.post) -> str:
    """Send one prompt to the configured model and return its reply text.

    api "openai" works with any OpenAI-compatible service (Groq, OpenRouter, Gemini, a local server);
    api "anthropic" uses the Anthropic Messages API.
    """
    messages = [{"role": "user", "content": prompt}]
    if rubric.get("api", "openai") == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        bodies = [{"model": rubric["model"], "max_tokens": 400, "messages": messages}]
        read = lambda data: data["content"][0]["text"]
    else:
        url = rubric["base_url"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        base = {"model": rubric["model"], "messages": messages}
        extra = rubric.get("request_extra") or {}
        # If the service rejects the optional settings, fall back to the bare request.
        bodies = [{**base, **extra}, base] if extra else [base]
        read = lambda data: data["choices"][0]["message"]["content"] or ""
    headers["content-type"] = "application/json"

    r = None
    for body in bodies:
        r = post(url, headers=headers, json=body, timeout=90)
        if r.status_code == 429:   # rate limited: wait as told, then try once more
            _sleep(min(float(r.headers.get("retry-after") or 20), 60))
            r = post(url, headers=headers, json=body, timeout=90)
        if r.status_code != 400:
            break
    if r.status_code != 200:
        raise FetchError(f"model HTTP {r.status_code}")
    return read(r.json())


def score_item(url: str, rec: dict, src: dict, cfg: dict, api_key: str, post=requests.post) -> None:
    """Step 2 of filtering: ask the model to score one item against the rubric."""
    rubric = cfg["filters"]["rubric"]
    priority = "\n".join("- " + t for t in cfg.get("topics", {}).get("priority", []))
    prompt = (
        f"{rubric['prompt']}\nPriority topics:\n{priority}\n\n"
        f"Item\nSource: {src['name']}\nTitle: {rec['title']}\nURL: {url}\n"
        f"Description: {rec.get('snippet') or '(none provided)'}"
    )
    out = extract_json(call_model(prompt, rubric, api_key, post))
    scores = out.get("scores")
    if not isinstance(scores, dict) or not scores:
        raise ValueError("reply has no scores")
    total = sum(clamp_score(v) for v in scores.values())
    tier_key = "wildcard" if src["tier"] == "wildcard" else f"tier_{src['tier']}"
    threshold = rubric["keep_if_score_at_least"][tier_key]
    minutes = out.get("minutes")
    rec.update({
        "score": total,
        "what": str(out.get("what") or "")[:300],
        "why": str(out.get("why") or "")[:300],
        "minutes": int(minutes) if isinstance(minutes, (int, float)) and 0 < minutes < 600 else None,
        "tags": [str(t) for t in (out.get("tags") or [])][:5],
    })
    if out.get("keep") not in (True, "true", "True") or total < threshold:
        rec["status"] = "rejected"


def clamp_score(value) -> int:
    try:
        return max(0, min(4, int(float(value))))
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------- select
def age_days(rec: dict, today: dt.date) -> int:
    return (today - dt.date.fromisoformat(rec.get("published") or rec["first_seen"])).days


def candidates(items: dict, sources: dict, cfg: dict, today: dt.date) -> list[dict]:
    """Passed, unshown items that may take a digest slot, best first."""
    d = cfg["digest"]
    out = []
    for url, rec in items.items():
        src = sources.get(rec["source"])
        if not src or rec["status"] != "passed" or rec.get("shown"):
            continue
        kind = rec["kind"]
        if kind == "release":
            if not rec.get("promote"):
                continue
            kind = "read"
        fresh = age_days(rec, today) <= d["max_age_days"]
        tier = src["tier"]
        # Order of preference: fresh tier 1, tier-1 back catalogue, fresh tier 2. Wildcards are separate.
        if fresh:
            group = WILD if tier == "wildcard" else (0 if tier == 1 else 2)
        elif d.get("backfill", {}).get("enabled") and tier == d["backfill"]["from_tier"] \
                and rec["kind"] != "release":
            group = BACKFILL
        else:
            continue
        out.append({**rec, "url": url, "slot_kind": kind, "group": group})
    out.sort(key=lambda r: (r["group"], -(r.get("score") or 0),
                            -dt.date.fromisoformat(r.get("published") or r["first_seen"]).toordinal(),
                            r["url"]))
    return out


def wildcard_order(cands: list[dict], today: dt.date) -> list[dict]:
    """Wildcard candidates in a random order that is fixed for the day."""
    wild = [c for c in cands if c["group"] == WILD]
    random.Random(today.isoformat()).shuffle(wild)
    return wild


def select(items: dict, sources: dict, cfg: dict, today: dt.date) -> list[str]:
    """Apply the digest caps. Returns the chosen URLs in display order."""
    d = cfg["digest"]
    quota = dict(d["mix"])
    wildcard_left = d.get("wildcard_slots", 0)
    backfill_left = d.get("backfill", {}).get("per_day", 0)
    week_ago = (today - dt.timedelta(days=7)).isoformat()
    shown_week: dict[str, int] = {}
    for rec in items.values():
        if rec.get("shown") and rec["shown"] > week_ago:
            shown_week[rec["source"]] = shown_week.get(rec["source"], 0) + 1

    cands = candidates(items, sources, cfg, today)
    core = [c for c in cands if c["group"] != WILD]
    wild = wildcard_order(cands, today)
    if any("score" in c for c in cands):
        # Once the scoring model is in use, a wildcard must have been judged relevant by it.
        wild = [c for c in wild if "score" in c]
    picks: list[dict] = []
    per_source: dict[str, int] = {}

    def take(c: dict) -> None:
        picks.append(c)
        quota[c["slot_kind"]] -= 1
        per_source[c["source"]] = per_source.get(c["source"], 0) + 1

    def allowed(c: dict) -> bool:
        src = sources[c["source"]]
        if quota.get(c["slot_kind"], 0) <= 0 or len(picks) >= d["max_items"]:
            return False
        if per_source.get(c["source"], 0) >= d["max_per_source_per_day"]:
            return False
        cap = src.get("max_per_week")
        return not (cap and shown_week.get(c["source"], 0) + per_source.get(c["source"], 0) >= cap)

    # Hold back the wildcard slot while core items fill the rest.
    def fill_core(reserve: int) -> None:
        nonlocal backfill_left
        for c in core:
            if c in picks or (c["slot_kind"] == "read" and quota["read"] <= reserve):
                continue
            if c["group"] == BACKFILL and backfill_left <= 0:
                continue
            if allowed(c):
                backfill_left -= c["group"] == BACKFILL
                take(c)

    fill_core(reserve=min(wildcard_left, 1 if wild else 0))
    for c in wild:
        if wildcard_left > 0 and allowed(c):
            wildcard_left -= 1
            take(c)
    fill_core(reserve=0)   # use the wildcard slot for a core item if no wildcard took it

    order = {"read": 0, "watch": 1}
    picks.sort(key=lambda c: order[c["slot_kind"]])
    return [c["url"] for c in picks]


def build_rollup(items: dict, sources: dict, today: dt.date) -> dict:
    """Newest release per source over the last 7 days, with a count of the rest."""
    by_source: dict[str, list[dict]] = {}
    for url, rec in items.items():
        if rec["kind"] == "release" and rec["status"] == "passed" and age_days(rec, today) <= 7:
            by_source.setdefault(rec["source"], []).append({**rec, "url": url})
    rows = []
    for sid, recs in by_source.items():
        if sid not in sources:
            continue
        recs.sort(key=lambda r: (r.get("published") or r["first_seen"], r["url"]), reverse=True)
        rows.append({"name": sources[sid]["name"], "title": recs[0]["title"],
                     "url": recs[0]["url"], "count": len(recs)})
    rows.sort(key=lambda r: r["name"].lower())
    return {"date": today.isoformat(), "items": rows}


# ------------------------------------------------------------------------ run
def read_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


def fetch_all(source_list: list[dict], cfg: dict, get=http_get) -> list[tuple[dict, list, str | None]]:
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(lambda s: fetch_source(s, cfg, get), source_list)
        return [(s, items, err) for s, (items, err) in zip(source_list, results)]


def run(root: Path = ROOT, today: dt.date | None = None, force: bool = False,
        get=http_get, post=requests.post, api_key: str | None = None) -> dict:
    cfg = load_config(root / "sources.yaml")
    today = today or dt.datetime.now(ZoneInfo(cfg["digest"]["timezone"])).date()
    source_list = load_sources(cfg)
    sources = {s["id"]: s for s in source_list}
    items: dict = read_json(root / "state/items.json", {})
    digests: dict = read_json(root / "state/digests.json", {"days": {}, "rollup": None})

    # 1. Fetch, dedupe by URL, apply the mechanical rules.
    health = []
    for src, fetched, err in fetch_all(source_list, cfg, get):
        new = 0
        for it in fetched:
            if it["url"] in items:
                continue
            new += 1
            reason = drop_reason(it, src, cfg)
            rec = {
                "title": it["title"], "source": src["id"], "kind": src["item_kind"],
                "published": it["published"].isoformat() if it["published"] else None,
                "first_seen": today.isoformat(),
                "status": f"dropped:{reason}" if reason else "passed",
            }
            if not reason:
                rec["snippet"] = it.get("snippet", "")
                if src["item_kind"] == "release" and src["kind"] == "github":
                    promote = cfg["digest"]["releases"].get("promote_to_daily")
                    if src.get("promote") == "always" or (promote == "major_only" and is_major(it.get("tag", ""))):
                        rec["promote"] = True
            items[it["url"]] = rec
        health.append({"id": src["id"], "name": src["name"], "ok": err is None,
                       "error": err, "fetched": len(fetched), "new": new})

    # 2. Today's digest. A rerun on the same day keeps the same picks unless forced.
    key = today.isoformat()
    if force and key in digests["days"]:
        for url in digests["days"].pop(key):
            items.get(url, {}).pop("shown", None)
    if key not in digests["days"]:
        api_key = api_key or os.environ.get("LLM_API_KEY")
        if api_key:
            digests["scoring"] = {"date": key, **score_candidates(items, sources, cfg, today, api_key, post)}
        else:
            digests.pop("scoring", None)
        picks = select(items, sources, cfg, today)
        for url in picks:
            items[url]["shown"] = key
        digests["days"][key] = picks

    # 3. Weekly release roll-up.
    rel = cfg["digest"]["releases"]
    due = WEEKDAYS[today.weekday()] == rel.get("day", "monday")
    if digests.get("rollup") is None or (due and digests["rollup"]["date"] != key):
        digests["rollup"] = build_rollup(items, sources, today)

    write_json(root / "state/items.json", items)
    write_json(root / "state/digests.json", digests)
    write_json(root / "state/health.json", health)

    # 4. Render.
    site = root / "_site"
    site.mkdir(exist_ok=True)
    name = lambda sid: sources[sid]["name"] if sid in sources else sid
    today_items = [{**items[u], "url": u, "source_name": name(items[u]["source"])}
                   for u in digests["days"][key] if u in items]
    library = [{**r, "url": u, "source_name": name(r["source"])}
               for u, r in items.items() if r["status"] == "passed" and r["kind"] != "release"]
    scoring = digests.get("scoring") if digests.get("scoring", {}).get("date") == key else None
    (site / "index.html").write_text(
        page.render_index(today, today_items, digests["rollup"], health, scoring))
    (site / "library.html").write_text(page.render_library(library, today))
    (site / ".nojekyll").write_text("")
    return {"date": key, "picked": len(today_items), "scoring": scoring,
            "sources_ok": sum(h["ok"] for h in health), "sources": len(health),
            "items_known": len(items)}


def score_candidates(items: dict, sources: dict, cfg: dict, today: dt.date,
                     api_key: str, post) -> dict:
    """Score unscored candidates within a per-run call budget.

    Order: fresh core items, a few back-catalogue items, a few wildcards (in the order selection
    will consider them), then fresh items from broad sources until the budget runs out.
    """
    rubric = cfg["filters"]["rubric"]
    budget = rubric.get("max_calls_per_run", 40)
    pause = rubric.get("pause_seconds", 0)
    backfill_n = cfg["digest"].get("backfill", {}).get("per_day", 0) * 3
    wildcard_n = cfg["digest"].get("wildcard_slots", 0) * 6

    cands = [c for c in candidates(items, sources, cfg, today) if "score" not in c]
    core = [c for c in cands if c["group"] == 0]
    backfill = [c for c in cands if c["group"] == BACKFILL][:backfill_n]
    wild = wildcard_order(cands, today)[:wildcard_n]
    # A backlog of fresh core items must not starve the back catalogue and the wildcard.
    first = max(budget - len(backfill) - len(wild), 0)
    queue = (core[:first] + backfill + wild + core[first:]
             + [c for c in cands if c["group"] == 2])[:budget]

    stats = {"calls": 0, "failed": 0, "rejected": 0, "error": None}
    streak = 0
    for i, c in enumerate(queue):
        if streak >= 3:
            break   # the service is down or the key is wrong; stop spending time on it
        if i and pause:
            _sleep(pause)
        rec = items[c["url"]]
        stats["calls"] += 1
        try:
            score_item(c["url"], rec, sources[c["source"]], cfg, api_key, post)
            streak = 0
            stats["rejected"] += rec["status"] == "rejected"
        except (FetchError, ValueError, KeyError, IndexError, TypeError, requests.RequestException) as e:
            streak += 1
            stats["failed"] += 1
            stats["error"] = str(e) or type(e).__name__
            print(f"scoring failed for {c['url']}: {stats['error']}", file=sys.stderr)
    return stats


def check(root: Path = ROOT) -> int:
    cfg = load_config(root / "sources.yaml")
    failed = 0
    for src, fetched, err in fetch_all(load_sources(cfg), cfg):
        failed += err is not None
        print(f"{'ok  ' if err is None else 'FAIL'} {len(fetched):4d}  {src['id']:<44} {err or ''}")
    print(f"\n{failed} source(s) failing")
    return failed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fetch every source and report, change nothing")
    ap.add_argument("--force", action="store_true", help="rebuild today's digest")
    args = ap.parse_args()
    if args.check:
        check()
        return
    summary = run(force=args.force)
    print(json.dumps(summary, indent=1))
    if summary["sources_ok"] == 0:
        sys.exit("every source failed; check the network")


if __name__ == "__main__":
    main()
