"""Step 4: draft (never send) a short email per top lead, validated in code before it's saved."""

import json

from pydantic import BaseModel, ValidationError

from . import config, mock
from .llm import LLM, LLMError, parse_json
from .store import write_csv, write_jsonl
from .validators import MAX_WORDS, validate_email

DRAFT_FIELDS = ["company", "angle", "subject", "body", "evidence"]
CALL_ASK = "a 15-minute call"


class Draft(BaseModel):
    subject: str
    body: str


SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"],
    "additionalProperties": False,
}

SYSTEM = f"""You write short cold emails for Blaze to property management companies. A human reviews every
draft before anything is sent.

What Blaze does (the only claims you may make about Blaze): {config.BLAZE_DESCRIPTION}

Rules:
- Body under {MAX_WORDS - 15} words, including greeting and sign-off. Plain, friendly, no hype, no exclamation marks.
- Open by referencing the evidence quote naturally, reusing at least four of its words in the same order.
- Mention Blaze exactly once.
- One clear ask: {CALL_ASK}.
- Greet with "Hi {{company}} team," (use the real company name) and sign off with "{config.SENDER_NAME}".
- No invented facts: no numbers, names, stats, or claims about the company beyond the quote and the facts given.
  Never assert how they work today (e.g. "you do this by hand"); frame pain points as common for firms like theirs,
  or as a question.
  Do not use any number except those in the facts given (and 15 for the call length).
- No placeholders or brackets of any kind.
- Subject: under 8 words, lowercase is fine, no numbers."""


def build_prompt(lead: dict, angle: str, feedback: list[str] | None = None) -> str:
    name, desc = config.ANGLES[angle]
    facts = {
        "company": lead["name"],
        "city": lead.get("city", ""),
        "evidence_quote": lead["evidence_quote"],
        "why_we_think_they_run_finances_by_hand": lead["reason"],
        "portfolio_size_hint": lead.get("portfolio_size_hint"),
        "software_mentioned_on_site": lead["signals"].get("software", []),
    }
    p = f"Angle {angle} ({name}): {desc}\n\nFacts:\n{json.dumps(facts, indent=2)}"
    if feedback:
        p += "\n\nYour previous draft failed these checks, fix them:\n- " + "\n- ".join(feedback)
    return p


def allowed_sources(lead: dict) -> list[str]:
    return [lead.get("page_text", ""), lead["name"], lead.get("city", ""), lead.get("address", ""),
            str(lead.get("portfolio_size_hint") or ""), config.BLAZE_DESCRIPTION, CALL_ASK]


def draft_one(lead: dict, angle: str, llm: LLM) -> tuple[dict | None, dict | None]:
    """Returns (draft_row, failure_row). Exactly one is not None."""
    feedback, attempts = None, []
    for attempt in (1, 2):  # first try + one retry
        prompt = build_prompt(lead, angle, feedback)
        try:
            if llm.mock:
                raw = mock.draft_response(lead, angle, attempt)
                llm.record_mock(SYSTEM + prompt, raw)
            else:
                raw = llm.complete_json(SYSTEM, prompt, SCHEMA)
            d = Draft.model_validate(parse_json(raw))
            problems = validate_email(d.subject, d.body, lead["evidence_quote"], allowed_sources(lead))
        except (LLMError, json.JSONDecodeError, ValueError, ValidationError) as e:
            d, problems = None, [f"bad model output: {e.__class__.__name__}: {str(e)[:120]}"]
        attempts.append(problems)
        if not problems:
            return {"company": lead["name"], "angle": angle, "subject": d.subject.strip(),
                    "body": d.body.strip(), "evidence": lead["evidence_quote"],
                    "retried": attempt == 2}, None
        feedback = problems
    return None, {"company": lead["name"], "angle": angle, "reasons": attempts[-1],
                  "first_attempt_reasons": attempts[0],
                  "last_subject": d.subject if d else "", "last_body": d.body if d else ""}


def draft(scored: list[dict], llm: LLM, top: int = 20, log=print) -> tuple[list[dict], list[dict]]:
    leads = [r for r in scored if r["status"] == "scored"]
    leads.sort(key=lambda r: -r["priority"])
    drafts, failures = [], []
    for i, lead in enumerate(leads[:top]):
        angle = "ABC"[i % 3]  # round-robin keeps the experiment balanced
        ok, fail = draft_one(lead, angle, llm)
        if ok:
            drafts.append(ok)
            log(f"  ✓ {lead['name']} [{angle}]{' (passed on retry)' if ok['retried'] else ''}")
        else:
            failures.append(fail)
            log(f"  ✗ {lead['name']} [{angle}]: {'; '.join(fail['reasons'])}")
    write_csv(config.DRAFTS_CSV, drafts, DRAFT_FIELDS)
    write_jsonl(config.DRAFT_FAILURES, failures)
    return drafts, failures
