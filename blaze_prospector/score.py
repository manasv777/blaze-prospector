"""Step 3: the LLM scores each lead, and code throws out any score whose evidence isn't on the page."""

import json

from pydantic import BaseModel, Field, ValidationError

from . import config, mock
from .llm import LLM, LLMError, parse_json
from .store import write_jsonl
from .validators import quote_in_text

MIN_PAGE_CHARS = 200


class Score(BaseModel):
    manual_finance_likelihood: int = Field(ge=1, le=5)
    reason: str
    evidence_quote: str
    portfolio_size_hint: str | None = None


SCHEMA = {
    "type": "object",
    "properties": {
        "manual_finance_likelihood": {"type": "integer"},
        "reason": {"type": "string"},
        "evidence_quote": {"type": "string"},
        "portfolio_size_hint": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["manual_finance_likelihood", "reason", "evidence_quote", "portfolio_size_hint"],
    "additionalProperties": False,
}

SYSTEM = f"""You help a sales team at Blaze qualify property management companies.

About Blaze: {config.BLAZE_DESCRIPTION}

Given the visible text of a company's homepage, estimate how likely it is that the company still runs
its back-office finances (bills, owner payouts, reconciliation, owner statements) by hand or with
spreadsheets and basic tools, rather than a fully automated stack.

Scale for manual_finance_likelihood:
1 = clearly automated (modern software everywhere, self-serve portals, large ops team)
3 = mixed or unclear
5 = strong signs of manual work (checks, money orders, small team doing books, hiring a bookkeeper, paper statements)

Rules:
- evidence_quote MUST be copied character-for-character from the page text: one contiguous phrase or sentence,
  8 to 30 words, no ellipses, no edits. It is checked by code; a paraphrase gets your score thrown out.
- Pick the quote that best supports your score. Prefer sentences about money and back-office work: rent
  collection, owner payouts or statements, paying bills, accounting, reconciliation, payment methods, software,
  portals, staff, portfolio size. Avoid generic marketing lines, testimonials about unrelated topics, navigation
  text, and form instructions.
- Use the whole 1-5 range. Most firms that name modern software (AppFolio, Buildium, Yardi, Propertyware) and
  self-serve portals are 1-2; small firms whose page suggests in-house, hands-on bookkeeping are 4-5.
- reason: one or two plain sentences that refer only to what the page says.
- portfolio_size_hint: a short phrase about portfolio size ONLY if the page states it (e.g. "manages 300+ homes"), else null.
- Do not invent facts. If the page is thin, say so in the reason and score 3."""


def build_prompt(lead: dict) -> str:
    return (
        f"Company: {lead['name']}\nWebsite: {lead['website']}\nCity: {lead.get('city', '')}\n"
        f"Detected signals (regex, may be noisy): {json.dumps(lead['signals'])}\n\n"
        f"Homepage text:\n<<<\n{lead['page_text']}\n>>>"
    )


def priority(likelihood: int, signals: dict) -> tuple[float, list[str]]:
    """LLM likelihood plus small, deterministic bonuses."""
    bonus, why = 0.0, []
    if signals.get("bookkeeper_hiring"):
        bonus += 1
        why.append("+1 hiring bookkeeper/accounting")
    if (signals.get("units") or 0) > 100:
        bonus += 1
        why.append("+1 manages 100+ doors")
    if signals.get("pay_by_check"):
        bonus += 0.5
        why.append("+0.5 accepts checks/money orders")
    return likelihood + bonus, why


def score_one(lead: dict, llm: LLM) -> dict:
    row = {k: v for k, v in lead.items()}
    row.update(status="unscored", unscored_reason="", likelihood=None, reason="",
               evidence_quote="", portfolio_size_hint=None, priority=None, bonuses=[])

    if lead.get("fetch_status") != "ok":
        row["unscored_reason"] = f"homepage not fetched ({lead.get('fetch_error') or lead.get('fetch_status')})"
        return row
    if len(lead.get("page_text", "")) < MIN_PAGE_CHARS:
        row["unscored_reason"] = "page text too short (likely JS-rendered)"
        return row

    prompt = build_prompt(lead)
    try:
        if llm.mock:
            raw = mock.score_response(lead)
            llm.record_mock(SYSTEM + prompt, raw)
        else:
            raw = llm.complete_json(SYSTEM, prompt, SCHEMA)
    except LLMError as e:
        row["unscored_reason"] = f"LLM call failed: {e}"
        return row

    try:
        s = Score.model_validate(parse_json(raw))
    except (json.JSONDecodeError, ValueError) as e:
        row["unscored_reason"] = "LLM output was not valid JSON" if isinstance(e, json.JSONDecodeError) \
            else f"LLM output failed schema: {_first_error(e)}"
        return row

    ok, why = quote_in_text(s.evidence_quote, lead["page_text"])
    if not ok:
        row.update(unscored_reason=why, evidence_quote=s.evidence_quote)
        return row

    p, bonuses = priority(s.manual_finance_likelihood, lead["signals"])
    row.update(status="scored", likelihood=s.manual_finance_likelihood, reason=s.reason,
               evidence_quote=s.evidence_quote, portfolio_size_hint=s.portfolio_size_hint,
               priority=p, bonuses=bonuses)
    return row


def _first_error(e: Exception) -> str:
    if isinstance(e, ValidationError):
        err = e.errors()[0]
        return f"{'.'.join(map(str, err['loc']))}: {err['msg']}"
    return str(e)


def score(enriched: list[dict], llm: LLM, log=print) -> list[dict]:
    out = []
    for lead in enriched:
        row = score_one(lead, llm)
        if row["status"] == "scored":
            log(f"  ✓ {row['name']}: likelihood {row['likelihood']} → priority {row['priority']}")
        else:
            log(f"  ✗ {row['name']}: unscored ({row['unscored_reason']})")
        out.append(row)
    out.sort(key=lambda r: (r["priority"] is None, -(r["priority"] or 0)))
    write_jsonl(config.SCORED, out)
    return out
