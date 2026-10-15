# Blaze Prospector

A rough v1 prospecting pipeline for [Blaze](https://blaze.money), the AI CFO for property managers. It finds property management companies, reads their public homepages, scores how likely they are to still run their finances by hand, and drafts short personalized emails for the best leads. It also tracks which message angle gets replies.

**It never sends email.** It only drafts. A person reviews every draft before anything goes out.

```
find ─► enrich ─► score ─► draft ─► (human review + send) ─► track
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then add ANTHROPIC_API_KEY (not needed for --mock)
```

Requires Python 3.11+.

## The 1-command demo

```bash
python prospector.py run --mock
```

This runs end to end on `fixtures/mock_companies.json` (10 fake companies) with no API keys and no network. Then open `out/dashboard.html` in a browser and read `out/report.md`.

Real run on the bundled list of 20 Raleigh / Durham / Chapel Hill property managers:

```bash
python prospector.py run --seed leads/seed_raleigh_durham.csv --top 20
```

With a Google Places key in `.env` you can search a city instead: `python prospector.py run --city "Raleigh, NC"`.

### Individual steps

| Command | What it does | Writes |
|---|---|---|
| `find --seed leads.csv` / `--city` / `--mock` | Source leads from the Google Places API (Text Search) and/or a seed CSV | `data/leads.csv` |
| `enrich` | Fetch each homepage (robots.txt, 1 req/s, 10s timeout), extract visible text (8k chars), detect regex signals | `data/enriched.jsonl` |
| `score` | LLM scores 1–5 with a verbatim evidence quote; code verifies the quote | `data/scored.jsonl` |
| `draft --top 20` | Draft emails for the top N with angles A/B/C round-robin; code validates every draft | `data/drafts.csv`, `data/draft_failures.jsonl` |
| `track` | Reply and meeting rate per angle from `data/replies.csv` | stdout |
| `dashboard` | Regenerate the dashboard from `data/` | `out/dashboard.html` |
| `run` | Steps 1–4, then the report and dashboard | `out/report.md`, `out/dashboard.html` |

## Design principle: the LLM reasons, code enforces

The model does the judgment calls. Deterministic code holds it to hard constraints and **fails closed**:

**Scoring (`blaze_prospector/score.py`)**
- The model returns JSON: `{manual_finance_likelihood: 1-5, reason, evidence_quote, portfolio_size_hint}`.
- Code checks that the JSON parses, that it matches the pydantic schema, that the score is 1–5, and that `evidence_quote` appears **verbatim** in the page text (after normalizing whitespace, case, and curly quotes). Quotes shorter than 3 words are rejected.
- If any check fails, the lead is marked `unscored` with the reason. No quote, no score.
- Priority = LLM likelihood + small deterministic bonuses: +1 hiring a bookkeeper/accountant, +1 manages 100+ doors, +0.5 accepts checks or money orders.

**Drafting (`blaze_prospector/draft.py`, `validators.py`)**
- Every draft is checked in code: 120 words or fewer, reuses at least 4 consecutive words from the evidence quote, no placeholders (`[First Name]`, `{company}`, …), mentions Blaze, and contains **no number that isn't in the company's own page/data or Blaze's description** (a proxy for "no invented facts").
- On failure the model gets one retry with the failure reasons. If it still fails, the draft is logged to `data/draft_failures.jsonl` with the reason and shown in the report. It's never silently kept.

**Draft-only.** There is no send code anywhere. `data/drafts.csv` is the hand-off to a person.

The mock fixture deliberately includes cases that trip these checks (a paraphrased quote, a JavaScript-only page, a fetch timeout, a draft with a `[First Name]` placeholder that passes on retry, and a draft with an invented "20 hours" claim that fails twice), so the demo shows the guardrails working.

## The experiment

Angles are assigned round-robin down the priority list so each gets a similar mix of leads:

- **A: time saved.** Hours spent on bills and owner payouts each month.
- **B: fewer errors.** Missed or late payments, reconciliation headaches.
- **C: owner experience.** Faster, cleaner owner distributions.

After sending (by hand), log outcomes in `data/replies.csv`:

```csv
company,sent_date,replied,meeting
Acorn + Oak Property Management,2026-10-20,y,n
```

`python prospector.py track` prints sends, reply rate and meeting rate per angle (angle is looked up from `drafts.csv`). Below 10 sends per angle it says "not enough data". In mock mode the dashboard uses `fixtures/mock_replies.csv`, which is labeled as sample data.

## Dashboard

`out/dashboard.html` is one self-contained file: inline CSS, JS, and fonts, with no network requests. It shows the funnel (with drop-offs and their reasons), a sortable and filterable lead table, a side panel with each drafted email and the evidence highlighted inside it, the angle experiment, guardrail counts, and token cost. Colors follow blaze.money (yellow `#FAF000`, near-black `#0A0A0A`, warm greys). The layout takes cues from a fintech dashboard reference. Keyboard: `/` search, `↑`/`↓` move between leads in the panel, `esc` closes it. Motion respects `prefers-reduced-motion`.

## Choices I made (and why)

- **Model:** `claude-sonnet-5-5` by default (override with `MODEL`). Sonnet 5.5 at low effort is plenty for a 1–5 judgment plus a quote. Responses use JSON structured outputs, and the code still validates them. Requests opt into Anthropic's server-side refusal fallback (`fallbacks: "default"`), so a rare safety-classifier decline gets rerouted instead of dropping a lead.
- **Cost estimate** uses list prices ($2 / $10 per million input/output tokens for Sonnet 5.5). Mock mode estimates tokens at about 4 characters per token and labels it "simulated".
- **Leads:** I had no Google Places key, so `leads/seed_raleigh_durham.csv` holds 20 independent, locally based property managers. They come from the public iPropertyManagement directory listings for Raleigh and Durham, plus Chapel Hill / Hillsborough firms in the same lists. I skipped national franchises and flat-fee platforms (Mynd, Ziprent, Belong), since they aren't the "runs finances by hand" profile. I checked that each homepage loads.
- **Homepage only.** Bookkeeper job posts and "owner statements" language often live on inner pages, so homepage-only signals undercount. That's the main accuracy limitation (see below).
- **Software detection** also scans raw HTML for portal links (e.g. `*.appfolio.com`, `managebuilding.com` for Buildium), since many sites link out to a portal instead of naming the vendor.
- **Fail closed everywhere:** fetch errors, robots.txt disallows, and pages under 200 characters of text (usually JavaScript-rendered) become `unscored` with a reason. They don't get guessed at.
- **Emails sign off** with `SENDER_NAME` (default "Manas").
- **Fonts:** Geist and Geist Mono (SIL OFL, see `blaze_prospector/assets/GEIST-LICENSE.txt`), embedded so the dashboard stays offline.

## What I'd do next

- **Warm the domain** before any volume: a separate sending subdomain, SPF/DKIM/DMARC, and a few weeks of gradual ramp so outreach doesn't burn the main domain's reputation.
- **Pick the best send time:** log send timestamps in `replies.csv` and test morning vs. afternoon, Tue–Thu, in the prospect's time zone.
- **Pull more sources:** NARPM member directories (the professional association for residential property managers) and BiggerPockets' property manager listings, deduped against Places.
- **Scrape job boards for "bookkeeper" posts** (Indeed, LinkedIn, Craigslist, through their APIs or allowed feeds). A PM firm hiring a bookkeeper is the strongest buying signal we have, and it rarely shows up on the homepage.
- **Hook into a CRM** (HubSpot or Attio): push approved drafts as tasks and pull reply/meeting status back automatically instead of hand-editing `replies.csv`.
- Crawl 2–3 inner pages per site (Owners, Careers, Pay Rent) with the same robots/rate limits.
- Grow the eval set: hand-label 30 real sites, then measure score agreement and quote-check pass rate per prompt change.

## Tests

```bash
pytest -q
```

Covers the validators: verbatim quote check (case and whitespace, smart quotes, paraphrase rejection, too-short quotes), word count, placeholder detection, evidence reuse, unknown-number detection, and the signal regexes.

## Layout

```
prospector.py                 CLI (typer)
blaze_prospector/
  find.py enrich.py score.py draft.py track.py   pipeline steps
  validators.py               the hard constraints (pure functions, tested)
  llm.py                      Anthropic SDK wrapper + token accounting
  mock.py                     canned responses for --mock
  report.py dashboard.py      out/report.md and out/dashboard.html
  assets/                     dashboard template + embedded fonts
fixtures/                     mock companies + sample replies
leads/seed_raleigh_durham.csv 20 real Triangle-area property managers
tests/test_validators.py
```
