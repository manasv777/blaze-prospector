"""Blaze Prospector: find → enrich → score → draft → track. Drafts only; nothing is ever sent."""

from datetime import datetime
from pathlib import Path

import typer

from blaze_prospector import config
from blaze_prospector.dashboard import write_dashboard
from blaze_prospector.draft import draft as draft_step
from blaze_prospector.enrich import enrich as enrich_step
from blaze_prospector.find import find as find_step
from blaze_prospector.llm import LLM
from blaze_prospector.report import collect_state, write_report
from blaze_prospector.score import score as score_step
from blaze_prospector.store import read_csv, read_json, read_jsonl, write_json
from blaze_prospector.track import ensure_replies_file, experiment, format_table

app = typer.Typer(add_completion=False, help=__doc__, no_args_is_help=True)

MockOpt = typer.Option(False, "--mock", help="Run on fixtures/mock_companies.json. No API keys needed.")


def _save_meta(**kw) -> None:
    meta = read_json(config.META, {}) or {}
    meta.update(kw, generated_at=datetime.now().isoformat(timespec="seconds"))
    write_json(config.META, meta)


def _save_usage(step: str, llm: LLM) -> None:
    usage = read_json(config.USAGE, {}) or {}
    usage[step] = llm.usage()
    write_json(config.USAGE, usage)


def _is_mock() -> bool:
    return (read_json(config.META, {}) or {}).get("mode") == "mock"


def _need_key(mock: bool) -> None:
    import os
    if not mock and not os.getenv("ANTHROPIC_API_KEY"):
        typer.secho("ANTHROPIC_API_KEY is not set. Add it to .env or use --mock.", fg="red")
        raise typer.Exit(1)


@app.command()
def find(city: str = typer.Option("", help='e.g. "Raleigh, NC" (used for Google Places)'),
         seed: Path = typer.Option(None, help="CSV with name,website,city"),
         mock: bool = MockOpt):
    """1. Source leads (Google Places if a key is set, plus --seed)."""
    if not (mock or seed or (config.GOOGLE_PLACES_API_KEY and city)):
        typer.secho("Nothing to search: pass --seed leads.csv, set GOOGLE_PLACES_API_KEY with --city, or use --mock.", fg="red")
        raise typer.Exit(1)
    leads, notes = find_step(city, seed, mock)
    if not city:
        towns = list(dict.fromkeys(l["city"].split(",")[0].strip() for l in leads if l.get("city")))
        city = "fixture data" if mock else (", ".join(towns[:3]) + (f" +{len(towns) - 3}" if len(towns) > 3 else "") or "seed list")
    _save_meta(mode="mock" if mock else "live", city=city, source_notes=notes)
    for n in notes:
        typer.echo(f"  · {n}")
    typer.echo(f"Found {len(leads)} leads → {config.LEADS_CSV.relative_to(config.ROOT)}")


@app.command()
def enrich(mock: bool = MockOpt):
    """2. Fetch homepages (robots.txt, 1 req/s, 10s timeout) and detect signals."""
    rows = enrich_step(read_csv(config.LEADS_CSV), mock=mock or _is_mock())
    ok = sum(r["fetch_status"] == "ok" for r in rows)
    typer.echo(f"Enriched {ok}/{len(rows)} → {config.ENRICHED.relative_to(config.ROOT)}")


@app.command()
def score(mock: bool = MockOpt):
    """3. LLM scores each lead; scores without a verbatim evidence quote are thrown out."""
    mock = mock or _is_mock()
    _need_key(mock)
    llm = LLM(mock=mock)
    rows = score_step(read_jsonl(config.ENRICHED), llm)
    _save_usage("score", llm)
    n = sum(r["status"] == "scored" for r in rows)
    typer.echo(f"Scored {n}, unscored {len(rows) - n} → {config.SCORED.relative_to(config.ROOT)}")


@app.command()
def draft(top: int = typer.Option(20, help="Draft emails for the top N leads"), mock: bool = MockOpt):
    """4. Draft emails for the top N leads (angles A/B/C round-robin). Never sends."""
    mock = mock or _is_mock()
    _need_key(mock)
    llm = LLM(mock=mock)
    drafts, failures = draft_step(read_jsonl(config.SCORED), llm, top=top)
    _save_usage("draft", llm)
    typer.echo(f"Drafted {len(drafts)}, rejected {len(failures)} → {config.DRAFTS_CSV.relative_to(config.ROOT)}")


@app.command()
def track():
    """5. Reply and meeting rate per angle, from data/replies.csv (you fill it in by hand)."""
    path = config.MOCK_REPLIES if _is_mock() else config.REPLIES_CSV
    if not _is_mock():
        ensure_replies_file()
    typer.echo(f"Source: {path.relative_to(config.ROOT)}\n")
    typer.echo(format_table(experiment(path, read_csv(config.DRAFTS_CSV))))


@app.command()
def dashboard():
    """Regenerate out/dashboard.html from the files in data/."""
    write_dashboard(collect_state())
    typer.echo(f"Dashboard → {config.DASHBOARD_HTML}")


@app.command()
def run(city: str = typer.Option("", help='e.g. "Raleigh, NC"'),
        seed: Path = typer.Option(None, help="CSV with name,website,city"),
        top: int = typer.Option(20, help="Draft emails for the top N leads"),
        mock: bool = MockOpt):
    """Run steps 1-4, then write out/report.md and out/dashboard.html."""
    _need_key(mock)
    for p in (config.ENRICHED, config.SCORED, config.DRAFTS_CSV, config.DRAFT_FAILURES, config.USAGE):
        p.unlink(missing_ok=True)

    typer.secho("1/4 find", bold=True)
    find(city=city, seed=seed, mock=mock)
    typer.secho("2/4 enrich", bold=True)
    enrich(mock=mock)
    typer.secho("3/4 score", bold=True)
    score(mock=mock)
    typer.secho("4/4 draft", bold=True)
    draft(top=top, mock=mock)
    if not mock:
        ensure_replies_file()

    state = collect_state()
    write_report(state)
    write_dashboard(state)
    t, f = state["usage"]["total"], state["funnel"]
    typer.secho("\nDone.", bold=True, fg="green")
    typer.echo(f"  found {f['found']} · enriched {f['enriched']} · scored {f['scored']} · unscored {f['unscored']} · "
               f"drafted {f['drafted']} · rejected {f['draft_failed']}")
    typer.echo(f"  tokens {t['input_tokens'] + t['output_tokens']:,} · est. ${t['cost_usd']:.4f}"
               + (" (simulated)" if t["simulated"] else ""))
    typer.echo(f"  report    {config.REPORT_MD}\n  dashboard {config.DASHBOARD_HTML}")


if __name__ == "__main__":
    app()
