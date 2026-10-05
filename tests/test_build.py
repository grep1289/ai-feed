import copy
import datetime as dt
import json
import re
import shutil
from pathlib import Path

import pytest

from aifeed import build

ROOT = Path(__file__).resolve().parent.parent
CFG = build.load_config(ROOT / "sources.yaml")
TODAY = dt.date(2026, 10, 5)


# ------------------------------------------------------------ the real config
def test_config_is_well_formed():
    sources = build.load_sources(CFG)
    ids = [s["id"] for s in sources]
    assert len(ids) == len(set(ids)), "duplicate source ids"
    for s in sources:
        urls = build.source_urls(s, CFG)
        assert urls and all(u.startswith("https://") for u in urls), s["id"]
        if s["kind"] == "youtube":
            assert re.fullmatch(r"UC[\w-]{22}", s["channel_id"]), s["id"]
            assert "playlist_id=UULF" in urls[0] and "channel_id=UC" in urls[1]
        if s["kind"] == "page":
            assert s["link_pattern"]
    for pattern in CFG["filters"]["drop_title_regex"]:
        re.compile(pattern)


def test_github_feed_addresses():
    sources = {s["id"]: s for s in build.load_sources(CFG)}
    assert build.source_urls(sources["gh-aaif-goose-goose"], CFG) == [
        "https://github.com/aaif-goose/goose/releases.atom"]
    assert build.source_urls(sources["gh-agentsmd-agents.md"], CFG) == [
        "https://github.com/agentsmd/agents.md/commits/main.atom"]


# --------------------------------------------------------- mechanical filters
RSS_SRC = {"id": "x", "name": "X", "kind": "rss", "item_kind": "read", "tier": 1}

KEEP = [
    "Effective context engineering for AI agents",
    "Writing effective tools for agents",
    "Why we built the Responses API",
    "Scaling Managed Agents: Decoupling the brain from the hands",
    "How we built Claude Code auto mode: a safer way to skip permissions",
    "How we built our multi-agent research system with Claude",
    "Turn your REST APIs into MCP tools with Google Cloud API Gateway",
    "How to Build a Model Router in the Harness",
    "MCP Usage Surged as the Protocol Went Stateless",
]
DROP = [
    "Chatham scales its capital markets expertise with OpenAI",
    "How Anthropic's sales team rebuilt inbound with Claude Managed Agents",
    "Agents you can coach: how Asana builds human-agent teams with Claude",
    "Claude for Government is now generally available",
    "Partnering with Accenture on embedded evaluation",
    "Barclays scales Claude to upgrade operations and improve client experience",
    "Jev is now available in LangSmith Evals",
    "KubeCon keynote highlights",
]


@pytest.mark.parametrize("title", KEEP)
def test_title_rules_keep(title):
    assert build.drop_reason({"title": title}, RSS_SRC, CFG) is None


@pytest.mark.parametrize("title", DROP)
def test_title_rules_drop(title):
    assert build.drop_reason({"title": title}, RSS_SRC, CFG) == "title_rule"


def test_keyword_gate():
    src = {**RSS_SRC, "include_any": CFG["topics"]["agentic"]}
    assert build.drop_reason({"title": "Kubernetes v1.37: Tracking PVC usage"}, src, CFG) == "keyword"
    assert build.drop_reason({"title": "Running agents on Kubernetes with kagent"}, src, CFG) is None
    assert build.drop_reason({"title": "A quiet title", "snippet": "about MCP servers"}, src, CFG) is None
    # whole-word start only: "management" must not match "agent"
    assert build.drop_reason({"title": "Storage management improvements"}, src, CFG) == "keyword"


def test_category_gate():
    src = {**RSS_SRC, "keep_categories": ["Agents", "Claude Code"]}
    assert build.drop_reason({"title": "T", "categories": ["Enterprise AI"]}, src, CFG) == "category"
    assert build.drop_reason({"title": "T", "categories": []}, src, CFG) == "category"
    assert build.drop_reason({"title": "T", "categories": ["Agents"]}, src, CFG) is None


@pytest.mark.parametrize("tag,expected", [
    ("v2.0.0-rc.1", True), ("v1.2.0-beta", True), ("python-hosting-a2a-1.0.0a260723", True),
    ("v2-rc.1", True), ("v1.53.0", False), ("2026-07-28", False), ("release/2025-11-28", False),
    ("@modelcontextprotocol/server@2.3.1", False), ("slim-version-v3.0.1", False),
])
def test_prerelease(tag, expected):
    src = {"id": "g", "name": "g", "kind": "github", "item_kind": "release", "tier": 1}
    got = build.drop_reason({"title": tag, "tag": tag}, src, CFG)
    assert (got == "prerelease") is expected


def test_commit_feeds_skip_prerelease_check():
    src = {"id": "g", "name": "g", "kind": "github", "item_kind": "release", "tier": 1, "feed": "commits"}
    assert build.drop_reason({"title": "Fix typo", "tag": "1.0.0a1"}, src, CFG) is None


@pytest.mark.parametrize("tag,expected", [
    ("v2.0.0", True), ("v1.0.0", True), ("v1.53.0", False), ("v2.3.1", False),
    ("v0.1.0", False), ("2026-07-28", False), ("pkg@3.0.0", True),
])
def test_is_major(tag, expected):
    assert build.is_major(tag) is expected


def test_normalize_url():
    n = build.normalize_url
    assert n("https://Example.com/a/?utm_source=x&id=3#top") == "https://example.com/a?id=3"
    assert n("https://www.youtube.com/watch?v=abc123") == "https://www.youtube.com/watch?v=abc123"
    assert n("https://example.com/post/?reference=1") == "https://example.com/post?reference=1"


# -------------------------------------------------------------------- parsing
RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Post &amp; one</title><link>https://ex.com/one/</link>
<pubDate>Fri, 02 Oct 2026 10:00:00 +0000</pubDate><category>Agents</category>
<description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</description></item>
<item><title>No date</title><link>https://ex.com/two</link></item>
<item><title></title><link>https://ex.com/three</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Releases</title>
<entry><id>1</id><title>v2.0.0</title><updated>2026-10-03T08:00:00Z</updated>
<link rel="alternate" href="https://github.com/o/r/releases/tag/v2.0.0"/><content type="html">notes</content></entry>
<entry><id>2</id><title>v2.0.0-rc.1</title><updated>2026-10-01T08:00:00Z</updated>
<link rel="alternate" href="https://github.com/o/r/releases/tag/v2.0.0-rc.1"/></entry>
</feed>"""

PAGE = b"""<html><body><nav><a href="/blog">Blog</a><a href="/blog/">Blog</a></nav>
<a href="/blog/a2a-joins-aaif"><img alt=""><h3>A2A joins AAIF's open agentic stack</h3><p>August 17</p></a>
<a href="https://aaif.io/blog/aaif-sandbox-phase#x">Catch Them Early</a>
<a href="/blog/aaif-sandbox-phase">Read</a>
<a href="/projects/agent2agent">A2A</a></body></html>"""


def test_parse_feed():
    items = build.parse_feed(RSS)
    assert [i["url"] for i in items] == ["https://ex.com/one", "https://ex.com/two"]
    assert items[0]["title"] == "Post & one"
    assert items[0]["snippet"] == "Hello world"
    assert items[0]["published"] == dt.date(2026, 10, 2)
    assert items[0]["categories"] == ["Agents"]
    assert items[1]["published"] is None


def test_parse_feed_rejects_html():
    with pytest.raises(build.FetchError):
        build.parse_feed(b"<html><body>Not found</body></html>")


def test_parse_page():
    pattern = r"^https://aaif\.io/blog/[a-z0-9-]+$"
    items = {i["url"]: i["title"] for i in build.parse_page(PAGE, "https://aaif.io/blog", pattern)}
    assert items == {
        "https://aaif.io/blog/a2a-joins-aaif": "A2A joins AAIF's open agentic stack",
        "https://aaif.io/blog/aaif-sandbox-phase": "Catch Them Early",
    }


def test_youtube_falls_back_when_first_feed_fails():
    src = next(s for s in build.load_sources(CFG) if s["id"] == "yt-aaif")
    calls = []

    def get(url):
        calls.append(url)
        if "playlist_id" in url:
            raise build.FetchError("HTTP 404")
        return RSS

    items, err = build.fetch_source(src, CFG, get)
    assert err is None and len(items) == 2 and len(calls) == 2


# ------------------------------------------------------------------ selection
def rec(source, kind="read", published="2026-10-04", **extra):
    return {"title": "t", "source": source, "kind": kind, "published": published,
            "first_seen": "2026-10-05", "status": "passed", **extra}


SOURCES = {
    "a": {"id": "a", "name": "A", "tier": 1}, "b": {"id": "b", "name": "B", "tier": 1},
    "c": {"id": "c", "name": "C", "tier": 2}, "d": {"id": "d", "name": "D", "tier": 2},
    "e": {"id": "e", "name": "E", "tier": 1},
    "w": {"id": "w", "name": "W", "tier": "wildcard"},
    "v1": {"id": "v1", "name": "V1", "tier": 1}, "v2": {"id": "v2", "name": "V2", "tier": 2},
    "v3": {"id": "v3", "name": "V3", "tier": 2, "max_per_week": 1},
    "g": {"id": "g", "name": "G", "tier": 1},
}


def test_select_respects_mix_and_caps():
    items = {
        "a1": rec("a"), "a2": rec("a"), "b1": rec("b"), "c1": rec("c"), "d1": rec("d"), "e1": rec("e"),
        "w1": rec("w"),
        "v1a": rec("v1", "watch"), "v2a": rec("v2", "watch"), "v3a": rec("v3", "watch"),
    }
    picks = build.select(items, SOURCES, CFG, TODAY)
    kinds = [items[u]["kind"] for u in picks]
    assert len(picks) == 5 and kinds == ["read"] * 3 + ["watch"] * 2
    assert "w1" in picks, "the wildcard slot is used when a wildcard item exists"
    sources = [items[u]["source"] for u in picks]
    assert len(sources) == len(set(sources)), "one item per source per day"
    assert not {"c1", "d1"} & set(picks), "tier 1 fills before tier 2"


def test_select_does_not_pad_one_kind_with_the_other():
    items = {f"r{i}": rec(s) for i, s in enumerate("abcde")}
    picks = build.select(items, SOURCES, CFG, TODAY)
    assert len(picks) == 3, "no videos available, so only the three read slots fill"


def test_backfill_is_one_per_day_and_stale_tier2_is_excluded():
    items = {
        "old1": rec("a", published="2026-03-01"), "old2": rec("b", published="2026-05-25"),
        "old3": rec("c", published="2026-05-01"),
    }
    picks = build.select(items, SOURCES, CFG, TODAY)
    assert picks == ["old2"], "newest tier-1 back-catalogue item, and only one"


def test_max_per_week():
    items = {"v3a": rec("v3", "watch"), "v3b": rec("v3", "watch", shown="2026-10-03")}
    assert build.select(items, SOURCES, CFG, TODAY) == []


def test_releases_only_enter_the_digest_when_promoted():
    items = {"r1": rec("g", "release"), "r2": rec("g", "release", promote=True)}
    assert build.select(items, SOURCES, CFG, TODAY) == ["r2"]


def test_scored_items_rank_above_unscored_within_a_tier():
    items = {"lo": rec("a", score=10), "hi": rec("b", score=15), "c1": rec("c", score=16)}
    picks = build.select(items, SOURCES, CFG, TODAY)
    assert picks == ["hi", "lo", "c1"]


def test_rollup_keeps_newest_per_source():
    items = {
        "u1": {**rec("g", "release", published="2026-10-01"), "title": "v1.1.0"},
        "u2": {**rec("g", "release", published="2026-10-03"), "title": "v1.2.0"},
        "u3": {**rec("g", "release", published="2026-09-01"), "title": "v1.0.0"},
    }
    roll = build.build_rollup(items, SOURCES, TODAY)
    assert roll["items"] == [{"name": "G", "title": "v1.2.0", "url": "u2", "count": 2}]


# -------------------------------------------------------------------- scoring
GOOD = ('{"keep": true, "scores": {"primary": 4, "buildable": 3, "substance": 3, "open": 3},'
        ' "kind": "read", "minutes": 12, "what": "A guide.", "why": "Apply it.", "tags": ["mcp"]}')
BAD = GOOD.replace('"keep": true', '"keep": false')


class FakeResponse:
    def __init__(self, text="", status=200, headers=None, api="openai"):
        self.status_code, self.headers = status, headers or {}
        self._data = ({"choices": [{"message": {"role": "assistant", "content": text}}]}
                      if api == "openai" else {"content": [{"type": "text", "text": text}]})

    def json(self):
        return self._data


class FakePost:
    """Records requests and replays a list of responses (the last one repeats)."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "body": json})
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    naps = []
    monkeypatch.setattr(build, "_sleep", naps.append)
    return naps


RUBRIC = CFG["filters"]["rubric"]


def test_config_points_at_an_openai_compatible_service():
    assert RUBRIC["api"] == "openai" and RUBRIC["base_url"].startswith("https://") and RUBRIC["model"]


def test_call_model_openai_request_shape():
    post = FakePost(FakeResponse("hello"))
    assert build.call_model("Return JSON", RUBRIC, "secret", post) == "hello"
    call = post.calls[0]
    assert call["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer secret"
    assert call["body"]["model"] == RUBRIC["model"]
    assert call["body"]["messages"] == [{"role": "user", "content": "Return JSON"}]
    fmt = call["body"]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert set(schema["required"]) == set(schema["properties"]) and schema["additionalProperties"] is False
    assert schema["properties"]["scores"]["required"] == build.CRITERIA
    assert call["body"]["reasoning_effort"] == "low"


def test_call_model_steps_down_through_output_formats_after_400s():
    bad = FakeResponse(status=400)
    post = FakePost(bad, bad, bad, FakeResponse("ok"))
    assert build.call_model("p", RUBRIC, "k", post) == "ok"
    formats = [c["body"].get("response_format", {}).get("type") for c in post.calls]
    assert formats == ["json_schema", "json_object", None, None]
    assert "reasoning_effort" in post.calls[2]["body"]
    assert set(post.calls[3]["body"]) == {"model", "messages"}


def test_call_model_honours_a_looser_configured_format():
    post = FakePost(FakeResponse("ok"))
    build.call_model("p", {**RUBRIC, "output": "json_object"}, "k", post)
    assert post.calls[0]["body"]["response_format"] == {"type": "json_object"}


def test_call_model_waits_and_retries_when_rate_limited(no_sleep):
    post = FakePost(FakeResponse(status=429, headers={"retry-after": "7"}), FakeResponse("ok"))
    assert build.call_model("p", RUBRIC, "k", post) == "ok"
    assert no_sleep == [7.0] and len(post.calls) == 2


def test_call_model_reports_a_bad_key():
    with pytest.raises(build.FetchError, match="401"):
        build.call_model("p", RUBRIC, "k", FakePost(FakeResponse(status=401)))


def test_call_model_anthropic_request_shape():
    rubric = {**RUBRIC, "api": "anthropic", "model": "some-model"}
    post = FakePost(FakeResponse("hi", api="anthropic"))
    assert build.call_model("p", rubric, "secret", post) == "hi"
    call = post.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == "secret" and "response_format" not in call["body"]


def test_score_item_keeps_and_rejects():
    src = {"id": "a", "name": "A", "tier": 2}
    r = rec("a")
    build.score_item("https://ex.com/1", r, src, CFG, "key", FakePost(FakeResponse("Sure:\n" + GOOD)))
    assert r["status"] == "passed" and r["score"] == 13 and r["minutes"] == 12 and r["what"] == "A guide."

    low = GOOD.replace('"primary": 4', '"primary": 1')   # 10, below the tier-2 threshold of 12
    r = rec("a")
    build.score_item("https://ex.com/1", r, src, CFG, "key", FakePost(FakeResponse(low)))
    assert r["status"] == "rejected"

    r = rec("a")
    build.score_item("https://ex.com/1", r, src, CFG, "key", FakePost(FakeResponse(BAD)))
    assert r["status"] == "rejected", "the model's keep=false wins even with a passing score"


@pytest.mark.parametrize("reply", ["", "I cannot help with that.", '{"keep": true}', '{"scores": []}', "[1]"])
def test_score_item_leaves_the_item_alone_on_an_unusable_reply(reply):
    r = rec("a")
    with pytest.raises(ValueError) as err:
        build.score_item("u", r, {"id": "a", "name": "A", "tier": 1}, CFG, "k", FakePost(FakeResponse(reply)))
    assert r["status"] == "passed" and "score" not in r
    assert "the model said" in str(err.value), "the log must show what the model replied"


def test_score_item_reads_scores_written_at_the_top_level():
    flat = '{"keep": true, "primary": 4, "buildable": 4, "substance": 3, "open": 2, "what": "W", "why": "Y"}'
    r = rec("a")
    build.score_item("u", r, {"id": "a", "name": "A", "tier": 1}, CFG, "k", FakePost(FakeResponse(flat)))
    assert r["score"] == 13 and r["status"] == "passed"


def test_score_item_treats_a_bare_rejection_as_a_rejection():
    r = rec("a")
    build.score_item("u", r, {"id": "a", "name": "A", "tier": 1}, CFG, "k",
                     FakePost(FakeResponse('{"keep": false, "reason": "event promotion"}')))
    assert r["status"] == "rejected" and r["score"] == 0


def test_unreadable_replies_do_not_stop_the_run():
    items = {f"u{i}": rec(s) for i, s in enumerate("abecd")}
    post = FakePost(*[FakeResponse("nonsense")] * 4, FakeResponse(GOOD))
    stats = build.score_candidates(items, SOURCES, CFG, TODAY, "k", post)
    assert stats["calls"] == 5 and stats["failed"] == 4 and len(stats["error"]) <= 200


def test_score_item_tolerates_sloppy_values_from_small_models():
    sloppy = ('{"keep": "true", "scores": {"primary": "4", "buildable": 9, "substance": 3.0, "open": null},'
              ' "minutes": "soon", "what": null, "tags": null}')
    r = rec("a")
    build.score_item("u", r, {"id": "a", "name": "A", "tier": 1}, CFG, "k", FakePost(FakeResponse(sloppy)))
    assert r["score"] == 4 + 4 + 3 + 0 and r["minutes"] is None and r["what"] == "" and r["status"] == "passed"


def test_scoring_paces_calls_and_stops_after_repeated_failures(no_sleep):
    items = {f"u{i}": rec(s) for i, s in enumerate("abe")}
    stats = build.score_candidates(items, SOURCES, CFG, TODAY, "k", FakePost(FakeResponse(GOOD)))
    assert stats == {"calls": 3, "failed": 0, "rejected": 0, "error": None}
    assert no_sleep == [RUBRIC["pause_seconds"]] * 2, "a pause between calls, none before the first"

    items = {f"u{i}": rec(s) for i, s in enumerate("abecd")}
    post = FakePost(FakeResponse(status=401))
    stats = build.score_candidates(items, SOURCES, CFG, TODAY, "k", post)
    assert stats["calls"] == 3 and stats["failed"] == 3 and stats["error"] == "model HTTP 401"
    assert all(r["status"] == "passed" for r in items.values()), "a dead key changes nothing"


def test_scoring_order_and_budget():
    items = {"t2": rec("c"), "t1": rec("a"), "old": rec("b", published="2026-01-01"), "wild": rec("w")}
    post = FakePost(FakeResponse(GOOD))
    build.score_candidates(items, SOURCES, CFG, TODAY, "k", post)
    order = [c["body"]["messages"][0]["content"].split("URL: ")[1].split("\n")[0] for c in post.calls]
    assert order == ["t1", "old", "wild", "t2"]


def test_scoring_budget_keeps_room_for_back_catalogue_and_wildcard():
    cfg = copy.deepcopy(CFG)
    cfg["filters"]["rubric"]["max_calls_per_run"] = 4
    items = {f"t{i}": rec("a") for i in range(6)}
    items.update({"old": rec("b", published="2026-01-01"), "wild": rec("w")})
    post = FakePost(FakeResponse(GOOD))
    build.score_candidates(items, SOURCES, cfg, TODAY, "k", post)
    assert len(post.calls) == 4
    assert "score" in items["old"] and "score" in items["wild"]


def test_unscored_wildcards_are_skipped_once_scoring_is_in_use():
    items = {"a1": rec("a", score=14), "w1": rec("w")}
    assert build.select(items, SOURCES, CFG, TODAY) == ["a1"]
    items["w1"]["score"] = 13
    assert set(build.select(items, SOURCES, CFG, TODAY)) == {"a1", "w1"}


# ----------------------------------------------------------------- end to end
@pytest.fixture
def root(tmp_path):
    shutil.copy(ROOT / "sources.yaml", tmp_path / "sources.yaml")
    return tmp_path


def fake_get(url):
    if "feed_openai_developer" in url:
        return RSS
    if url == "https://aaif.io/blog":
        return PAGE
    if url.endswith("o/r/releases.atom") or "modelcontextprotocol/modelcontextprotocol/releases.atom" in url:
        return ATOM
    raise build.FetchError("HTTP 403")


def test_run_end_to_end(root):
    summary = build.run(root, TODAY, get=fake_get)
    items = json.loads((root / "state/items.json").read_text())
    digests = json.loads((root / "state/digests.json").read_text())
    health = json.loads((root / "state/health.json").read_text())

    assert summary["sources_ok"] == 3 and summary["sources"] == len(build.load_sources(CFG))
    assert items["https://github.com/o/r/releases/tag/v2.0.0-rc.1"]["status"] == "dropped:prerelease"
    assert items["https://github.com/o/r/releases/tag/v2.0.0"]["promote"] is True
    assert {h["id"] for h in health if h["ok"]} == {
        "openai-developer", "aaif-blog", "gh-modelcontextprotocol-modelcontextprotocol"}

    picks = digests["days"]["2026-10-05"]
    assert picks and all(items[u]["shown"] == "2026-10-05" for u in picks)
    sources = [items[u]["source"] for u in picks]
    assert len(sources) == len(set(sources))
    assert digests["rollup"]["items"][0]["title"] == "v2.0.0"

    index = (root / "_site/index.html").read_text()
    assert "Monday 5 October" in index and "That's all for today." in index
    assert "sources fetched" in index and 'name="robots" content="noindex' in index
    assert (root / "_site/library.html").exists() and (root / "_site/.nojekyll").exists()

    # Same day again: nothing new, same digest.
    again = build.run(root, TODAY, get=fake_get)
    assert json.loads((root / "state/digests.json").read_text())["days"]["2026-10-05"] == picks
    assert again["items_known"] == summary["items_known"]

    # Next day: yesterday's items are not repeated.
    build.run(root, TODAY + dt.timedelta(days=1), get=fake_get)
    days = json.loads((root / "state/digests.json").read_text())["days"]
    assert not set(days["2026-10-06"]) & set(picks)


def test_force_rebuilds_today(root):
    build.run(root, TODAY, get=fake_get)
    first = json.loads((root / "state/digests.json").read_text())["days"]["2026-10-05"]
    build.run(root, TODAY, force=True, get=fake_get)
    assert json.loads((root / "state/digests.json").read_text())["days"]["2026-10-05"] == first


def test_empty_day_renders(root):
    build.run(root, TODAY, get=lambda url: (_ for _ in ()).throw(build.FetchError("HTTP 403")))
    index = (root / "_site/index.html").read_text()
    assert "Nothing new passed the filter today." in index and "That's all for today." not in index


def test_run_with_scoring(root, monkeypatch):
    replies = FakePost(FakeResponse(GOOD), FakeResponse(BAD), FakeResponse(GOOD))
    summary = build.run(root, TODAY, get=fake_get, post=replies, api_key="k")
    items = json.loads((root / "state/items.json").read_text())
    digests = json.loads((root / "state/digests.json").read_text())
    assert summary["scoring"]["calls"] == len(replies.calls) >= 3
    assert summary["scoring"]["rejected"] == 1 and summary["scoring"]["failed"] == 0
    rejected = [u for u, r in items.items() if r["status"] == "rejected"]
    assert len(rejected) == 1 and rejected[0] not in digests["days"]["2026-10-05"]
    index = (root / "_site/index.html").read_text()
    assert "A guide." in index and "Apply it." in index and "about 12 min" in index
    assert "scored, 1 rejected" in index


def test_run_shows_when_scoring_fails(root):
    build.run(root, TODAY, get=fake_get, post=FakePost(FakeResponse(status=401)), api_key="bad")
    index = (root / "_site/index.html").read_text()
    assert "Scoring failed for 3 of 3 items" in index and "model HTTP 401" in index
    assert "That\'s all for today." in index or "That's all for today." in index


def test_run_reads_the_key_from_the_environment(root, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "from-env")
    post = FakePost(FakeResponse(GOOD))
    build.run(root, TODAY, get=fake_get, post=post)
    assert post.calls and post.calls[0]["headers"]["Authorization"] == "Bearer from-env"
