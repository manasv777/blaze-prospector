"""Gather everything on disk into one state dict, then render out/report.md from it."""

import re
from collections import Counter
from datetime import datetime
from urllib.parse import quote

from . import config
from .store import read_csv, read_json, read_jsonl
from .track import experiment


def collect_state() -> dict:
    meta = read_json(config.META, {}) or {}
    leads = read_csv(config.LEADS_CSV)
    enriched = read_jsonl(config.ENRICHED)
    scored = read_jsonl(config.SCORED)
    drafts = read_csv(config.DRAFTS_CSV)
    failures = read_jsonl(config.DRAFT_FAILURES)
    usage = read_json(config.USAGE, {}) or {}

    replies_path = config.MOCK_REPLIES if meta.get("mode") == "mock" else config.REPLIES_CSV
    exp = experiment(replies_path, drafts)
    exp["sample_data"] = meta.get("mode") == "mock"

    draft_by = {d["company"]: d for d in drafts}
    fail_by = {f["company"]: f for f in failures}
    unscored = [r for r in scored if r["status"] != "scored"]
    reasons = Counter(r["unscored_reason"] for r in unscored)

    rows = []
    for r in scored:
        rows.append({
            "name": r["name"], "website": r["website"], "city": r.get("city", ""),
            "status": r["status"], "unscored_reason": r.get("unscored_reason", ""),
            "priority": r.get("priority"), "likelihood": r.get("likelihood"),
            "reason": r.get("reason", ""), "evidence_quote": r.get("evidence_quote", ""),
            "portfolio_size_hint": r.get("portfolio_size_hint"), "bonuses": r.get("bonuses", []),
            "signals": r.get("signals") or {},
            "draft": draft_by.get(r["name"]), "draft_failure": fail_by.get(r["name"]),
        })

    steps = [u for u in usage.values() if isinstance(u, dict)]
    inp = sum(u.get("input_tokens", 0) for u in steps)
    out = sum(u.get("output_tokens", 0) for u in steps)
    model = next((u["model"] for u in steps if u.get("model")), config.MODEL)
    total = {"input_tokens": inp, "output_tokens": out, "calls": sum(u.get("calls", 0) for u in steps),
             "cost_usd": round(config.cost_usd(model, inp, out), 4), "model": model,
             "simulated": any(u.get("simulated") for u in steps),
             "price_per_mtok": config.PRICING.get(model, config.PRICING["claude-sonnet-5-5"])}

    return {
        "meta": {**meta, "generated_at": meta.get("generated_at") or datetime.now().isoformat(timespec="seconds")},
        "funnel": {
            "found": len(leads),
            "enriched": sum(1 for e in enriched if e.get("fetch_status") == "ok"),
            "scored": len(scored) - len(unscored),
            "unscored": len(unscored),
            "drafted": len(drafts),
            "draft_failed": len(failures),
        },
        "unscored_reasons": [{"reason": k, "count": v,
                              "companies": [r["name"] for r in unscored if r["unscored_reason"] == k]}
                             for k, v in reasons.most_common()],
        "leads": rows,
        "draft_failures": failures,
        "experiment": exp,
        "usage": {"steps": usage, "total": total},
        "angles": {k: {"name": v[0], "description": v[1]} for k, v in config.ANGLES.items()},
    }


def _signals_text(s: dict) -> str:
    parts = []
    if s.get("software"):
        parts.append("software: " + ", ".join(s["software"]))
    if s.get("owner_portal"):
        parts.append("owner portal/statements")
    if s.get("pay_rent_online"):
        parts.append("pays rent online")
    if s.get("pay_by_check"):
        parts.append("accepts checks")
    if s.get("bookkeeper_hiring"):
        parts.append("hiring bookkeeper")
    if s.get("units"):
        parts.append(f"{s['units']} units")
    return "; ".join(parts) or "none"


_MD_SPECIAL = re.compile(r"([\\`*_\[\]()#!|~])")


def _md(text) -> str:
    """Escape untrusted (scraped or model-written) text so it renders as plain text: no HTML, links, or images."""
    t = str(text if text is not None else "")
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _MD_SPECIAL.sub(r"\\\1", t)


def _cell(text) -> str:
    return _md(text).replace("\n", " ")


def _link(name: str, url: str) -> str:
    """Markdown link only for http(s) URLs, percent-encoded so the URL can't close the link early."""
    if not url.lower().startswith(("http://", "https://")):
        return _cell(name)
    return f"[{_cell(name)}]({quote(url, safe=':/?#=&%.-_~+,;@$')})"


def write_report(state: dict) -> str:
    m, f, t = state["meta"], state["funnel"], state["usage"]["total"]
    mode = "MOCK (fixture data, simulated tokens)" if m.get("mode") == "mock" else "live"
    L = [f"# Blaze Prospector report", "",
         f"- Run: {m.get('generated_at', '')} · mode: {mode} · model: `{t['model']}`",
         f"- Sources: {_md('; '.join(m.get('source_notes', []))) or 'n/a'}",
         "- Draft-only: nothing was sent. A human reviews every email before it goes out.", "",
         "## Summary", "",
         "| Step | Count |", "|---|---|",
         f"| Leads found | {f['found']} |",
         f"| Homepages enriched | {f['enriched']} |",
         f"| Scored (evidence verified) | {f['scored']} |",
         f"| Unscored | {f['unscored']} |",
         f"| Emails drafted | {f['drafted']} |",
         f"| Drafts rejected by validator | {f['draft_failed']} |", ""]

    if state["unscored_reasons"]:
        L += ["**Unscored, with reasons**", ""]
        for u in state["unscored_reasons"]:
            L.append(f"- {_md(u['reason'])} ({u['count']}): {_md(', '.join(u['companies']))}")
        L.append("")

    scored = [r for r in state["leads"] if r["status"] == "scored"]
    L += ["## Top 10 leads", "", "| # | Company | Priority | LLM 1-5 | Evidence quote | Signals |", "|---|---|---|---|---|---|"]
    for i, r in enumerate(scored[:10], 1):
        L.append(f"| {i} | {_link(r['name'], r['website'])} | {r['priority']:g} | {r['likelihood']} | "
                 f"\"{_cell(r['evidence_quote'])}\" | {_cell(_signals_text(r['signals']))} |")
    L.append("")

    L += ["## Sample emails (one per angle)", ""]
    for a, info in state["angles"].items():
        d = next((r["draft"] for r in scored if r["draft"] and r["draft"]["angle"] == a), None)
        L.append(f"### Angle {a}: {info['name']}")
        if not d:
            L += ["", "_No passing draft for this angle._", ""]
            continue
        body = "\n".join("> " + _md(line) if line else ">" for line in d["body"].splitlines())
        L += ["", f"**To:** {_md(d['company'])}  ", f"**Subject:** {_md(d['subject'])}", "", body, "",
              f"_Evidence:_ \"{_md(d['evidence'])}\"", ""]

    if state["draft_failures"]:
        L += ["## Drafts rejected by the validator", ""]
        for x in state["draft_failures"]:
            L.append(f"- {_md(x['company'])} \\[{x['angle']}\\]: {_md('; '.join(x['reasons']))}")
        L.append("")

    exp = state["experiment"]
    L += ["## Experiment", "", f"Source: `{exp['source']}`" + (" (sample data for the demo)" if exp.get("sample_data") else ""), "",
          "| Angle | Sends | Reply rate | Meeting rate |", "|---|---|---|---|"]
    for s in exp["angles"]:
        if s["enough_data"]:
            L.append(f"| {s['angle']}: {s['name']} | {s['sends']} | {s['reply_rate']:.0%} ({s['replies']}) | "
                     f"{s['meeting_rate']:.0%} ({s['meetings']}) |")
        else:
            L.append(f"| {s['angle']}: {s['name']} | {s['sends']} | not enough data | not enough data |")
    L.append("")

    L += ["## Cost", "",
          f"- Model calls: {t['calls']}",
          f"- Tokens: {t['input_tokens']:,} input + {t['output_tokens']:,} output = {t['input_tokens'] + t['output_tokens']:,}",
          f"- Estimated cost: ${t['cost_usd']:.4f} (at ${t['price_per_mtok'][0]:g} / ${t['price_per_mtok'][1]:g} per million input/output tokens)"
          + (" (simulated in mock mode)" if t["simulated"] else ""), ""]

    text = "\n".join(L)
    config.REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    config.REPORT_MD.write_text(text, encoding="utf-8")
    return text
