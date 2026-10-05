"""Renders the two static pages: today's digest and the library."""
from __future__ import annotations

import datetime as dt
from html import escape

WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"]

CSS = """
:root{--paper:#eef2f3;--ink:#16202e;--muted:#566273;--line:#c9d2d9;--read:#2a45b8;--watch:#ad1f58}
@media (prefers-color-scheme:dark){:root{--paper:#10161f;--ink:#e7ecf1;--muted:#97a4b3;--line:#2b3542;--read:#93a7ff;--watch:#ff8fb6}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:400 1.0625rem/1.55 "Atkinson Hyperlegible Next","Atkinson Hyperlegible",system-ui,sans-serif}
main{max-width:39rem;margin:0 auto;padding:clamp(2rem,8vw,5rem) 1.25rem 4rem}
h1{font-size:clamp(2.25rem,9vw,3.75rem);line-height:1.02;letter-spacing:-.025em;margin:0 0 .75rem;font-weight:800;text-wrap:balance}
.count{font-size:1.1875rem;color:var(--muted);margin:0 0 3rem;max-width:30rem}
a{color:inherit;text-decoration-thickness:.08em;text-underline-offset:.18em}
a:focus-visible,summary:focus-visible,input:focus-visible{outline:3px solid var(--read);outline-offset:3px;border-radius:2px}
.items{list-style:none;margin:0;padding:0}
.item{border-left:.3rem solid var(--kind);padding:.1rem 0 .2rem 1.1rem;margin:0 0 2.25rem}
.item.read{--kind:var(--read)}
.item.watch{--kind:var(--watch)}
.item h2{font-size:1.375rem;line-height:1.25;margin:0 0 .35rem;font-weight:700;letter-spacing:-.01em;text-wrap:balance}
.item h2 a{text-decoration:none}
.item h2 a:hover{text-decoration:underline}
.meta{margin:0 0 .5rem;color:var(--muted);font-size:.9375rem}
.meta b{color:var(--kind);font-weight:700}
.note{margin:0 0 .3rem;max-width:34rem}
.note.why{color:var(--muted)}
.end{background:var(--ink);color:var(--paper);font-size:clamp(1.5rem,6vw,2.25rem);font-weight:800;letter-spacing:-.02em;line-height:1.1;margin:3.5rem 0 3rem;padding:1.6rem 1.25rem 1.7rem}
.empty{font-size:1.1875rem;max-width:30rem;margin:0 0 1rem}
details{border-top:1px solid var(--line);padding:.9rem 0}
details:last-of-type{border-bottom:1px solid var(--line)}
summary{cursor:pointer;font-weight:700}
details ul{margin:.8rem 0 .3rem;padding-left:1.1rem}
details li{margin:0 0 .45rem}
details .sub{color:var(--muted)}
footer{margin-top:1.75rem;color:var(--muted);font-size:.9375rem}
label{display:block;font-weight:700;margin:0 0 .4rem}
input[type=search]{width:100%;font:inherit;color:inherit;background:transparent;border:2px solid var(--ink);border-radius:0;padding:.6rem .75rem;margin:0 0 .6rem}
.shown{color:var(--muted);font-size:.9375rem;margin:0 0 2rem}
.lib{list-style:none;margin:0;padding:0}
.lib li{border-top:1px solid var(--line);padding:.8rem 0}
.lib a{font-weight:700;text-decoration:none}
.lib a:hover{text-decoration:underline}
.lib .meta{margin:.15rem 0 0}
.lib .read{--kind:var(--read)}
.lib .watch{--kind:var(--watch)}
"""

HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible+Next:wght@400;700;800&display=swap" rel="stylesheet">
<style>{css}</style>
</head>
<body>
<main>
"""

FOOT = "</main>\n</body>\n</html>\n"


def long_date(d: dt.date, year: bool = False) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')}" + (f" {d.year}" if year else "")


def short_date(iso: str | None) -> str:
    if not iso:
        return ""
    d = dt.date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B %Y')}"


def count_sentence(items: list[dict]) -> str:
    reads = sum(1 for i in items if kind_of(i) == "read")
    watches = len(items) - reads
    parts = []
    if reads:
        parts.append(f"{WORDS[reads] if reads < len(WORDS) else reads} to read")
    if watches:
        word = WORDS[watches] if watches < len(WORDS) else str(watches)
        parts.append(f"{word if not parts else word.lower()} to watch")
    return ", ".join(parts) + ". Then you're done."


def kind_of(item: dict) -> str:
    return "watch" if item["kind"] == "watch" else "read"


def meta_line(item: dict) -> str:
    kind = kind_of(item)
    bits = [f"<b>{kind.capitalize()}</b>", escape(item["source_name"])]
    date = short_date(item.get("published"))
    if date:
        bits.append(date)
    if item.get("minutes"):
        bits.append(f"about {int(item['minutes'])} min")
    return ", ".join(bits)


def render_item(item: dict) -> str:
    notes = ""
    if item.get("what"):
        notes += f'<p class="note">{escape(item["what"])}</p>'
        if item.get("why"):
            notes += f'<p class="note why">{escape(item["why"])}</p>'
    elif item.get("snippet"):
        notes += f'<p class="note why">{escape(item["snippet"])}</p>'
    return (
        f'<li class="item {kind_of(item)}">'
        f'<h2><a href="{escape(item["url"], quote=True)}">{escape(item["title"])}</a></h2>'
        f'<p class="meta">{meta_line(item)}</p>{notes}</li>\n'
    )


def render_index(today: dt.date, items: list[dict], rollup: dict | None, health: list[dict]) -> str:
    out = [HEAD.format(title=f"Feed, {long_date(today)}", css=CSS)]
    out.append(f"<h1>{long_date(today)}</h1>\n")
    if items:
        out.append(f'<p class="count">{count_sentence(items)}</p>\n<ul class="items">\n')
        out.extend(render_item(i) for i in items)
        out.append("</ul>\n")
        out.append('<p class="end">That\'s all for today.</p>\n')
    else:
        out.append('<p class="empty">Nothing new passed the filter today.</p>\n')
        out.append('<p class="count">The library holds earlier items if you are building something.</p>\n')

    if rollup and rollup.get("items"):
        until = long_date(dt.date.fromisoformat(rollup["date"]))
        n = len(rollup["items"])
        out.append(f"<details><summary>Tooling changes, week to {until} ({n} project{'s' if n != 1 else ''})</summary>\n<ul>\n")
        for r in rollup["items"]:
            more = f', {r["count"]} releases this week' if r["count"] > 1 else ""
            out.append(
                f'<li><a href="{escape(r["url"], quote=True)}">{escape(r["name"])}</a> '
                f'<span class="sub">{escape(r["title"])}{more}</span></li>\n'
            )
        out.append("</ul>\n</details>\n")

    failing = [h for h in health if not h["ok"]]
    ok = len(health) - len(failing)
    if failing:
        out.append(f"<details><summary>{ok} of {len(health)} sources fetched, {len(failing)} failing</summary>\n<ul>\n")
        for h in failing:
            out.append(f'<li>{escape(h["name"])} <span class="sub">{escape(str(h["error"]))}</span></li>\n')
        out.append("</ul>\n</details>\n")
    out.append('<footer><a href="library.html">Library</a>')
    if not failing:
        out.append(f", all {len(health)} sources fetched")
    out.append("</footer>\n")
    out.append(FOOT)
    return "".join(out)


def render_library(items: list[dict], today: dt.date) -> str:
    items = sorted(items, key=lambda r: (r.get("published") or r["first_seen"], r["url"]), reverse=True)
    out = [HEAD.format(title="Feed library", css=CSS)]
    out.append("<h1>Library</h1>\n")
    out.append('<p class="count">Everything that passed the filter. Nothing here is pushed to you.</p>\n')
    out.append('<label for="q">Filter by title or source</label>\n')
    out.append('<input type="search" id="q" autocomplete="off">\n')
    out.append(f'<p class="shown" id="shown" aria-live="polite">{len(items)} items</p>\n<ul class="lib" id="lib">\n')
    for i in items:
        shown = f', in the digest on {short_date(i["shown"])}' if i.get("shown") else ""
        out.append(
            f'<li class="{kind_of(i)}"><a href="{escape(i["url"], quote=True)}">{escape(i["title"])}</a>'
            f'<p class="meta">{meta_line(i)}{shown}</p></li>\n'
        )
    out.append("</ul>\n")
    out.append(f'<footer><a href="index.html">Today</a>, built {long_date(today, year=True)}</footer>\n')
    out.append("""<script>
const q=document.getElementById('q'),rows=[...document.querySelectorAll('#lib li')],shown=document.getElementById('shown');
q.addEventListener('input',()=>{const t=q.value.trim().toLowerCase();let n=0;
for(const r of rows){const hit=!t||r.textContent.toLowerCase().includes(t);r.hidden=!hit;n+=hit}
shown.textContent=t?`${n} of ${rows.length} items`:`${rows.length} items`});
</script>
""")
    out.append(FOOT)
    return "".join(out)
