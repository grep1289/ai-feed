# ai-feed

A personal daily feed on agentic AI, built from primary sources only. Once a day a GitHub Actions
workflow fetches the sources, filters them, picks at most five items, and publishes one static page.
The page ends; there is nothing to scroll.

## How it works

1. **Fetch.** `sources.yaml` lists engineering blogs, YouTube channels, GitHub repos and one page
   with no feed. Each is fetched once per run. A source that fails is reported on the page and does
   not stop the run.
2. **Filter.** Every new item is deduplicated by URL, then passed through keyword and category
   gates and the title drop rules in `sources.yaml`.
3. **Score (optional).** Candidates are scored against the rubric in `sources.yaml` by a small model
   and get a two-line note; low scorers are rejected. The model is Groq's free tier by default, using
   the `GROQ_LLM_API_KEY` repository secret. Without the secret this step is skipped. If the model
   fails, the page says so and the rules in step 2 decide alone.
4. **Pick.** Up to three items to read and two to watch, one per source, one wildcard. Fresh posts
   from core sources come first, then one item from their back catalogue, then broad sources.
   Short days stay short.
5. **Publish.** `index.html` is today's digest plus a weekly roll-up of releases. `library.html` is
   everything that passed the filter, searchable.

## Files

| Path | Purpose |
|---|---|
| `sources.yaml` | Sources, filter rules, rubric and digest caps. This is the file to edit. |
| `aifeed/build.py` | The pipeline. |
| `aifeed/page.py` | The two HTML pages. |
| `state/` | Written by the workflow: every item seen, each day's picks, source health. |
| `.github/workflows/daily.yml` | Runs daily at 06:47 India time, and on demand. |

## One-time setup

1. In the repository's **Settings → Pages**, set **Source** to **GitHub Actions**.
2. Optional: in **Settings → Secrets and variables → Actions**, add `GROQ_LLM_API_KEY` to turn on scoring.
3. In the **Actions** tab, open **Daily digest** and choose **Run workflow** for the first run.

The page is public at `https://grep1289.github.io/ai-feed/`. It asks search engines not to index it.

## Changing the scoring model

Any OpenAI-compatible service works. In `sources.yaml` under `filters.rubric`, change `base_url` and
`model`; in `.github/workflows/daily.yml`, point `LLM_API_KEY` at that service's secret. Free tiers
have small per-minute limits, so `pause_seconds` spaces the calls out and `max_calls_per_run` caps them.

## Tuning

- Too much noise from a source: add `include_any` or `keep_categories` to it, or a pattern to `drop_title_regex`.
- Wrong picks: `state/items.json` records why each item was dropped (`dropped:keyword`, `dropped:title_rule`, ...)
  or `rejected` by the model, with its score.
- Model too strict or too loose: change `keep_if_score_at_least` (scores are out of 16).
- A source keeps failing: the page lists it under the digest. Fix its address in `sources.yaml` or remove it.

## Run it locally

```
pip install -r requirements.txt
python -m pytest -q
python -m aifeed.build --check   # fetch every source and report, change nothing
python -m aifeed.build           # full run; writes state/ and _site/
```
