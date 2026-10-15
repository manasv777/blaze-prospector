"""Canned model responses for --mock, driven by fixtures/mock_companies.json. No network, no keys.

Some fixture companies are built to trip the validators so the demo shows them working:
a paraphrased evidence quote, a draft with a leftover placeholder, and a draft with an invented number.
"""

import json
from functools import cache

from . import config

TEMPLATES = {
    "A": ("less time on bills at {name}",
          "Hi {name} team,\n\nI saw this on your site: \"{quote}\" Work like that usually means someone spends "
          "real hours every month paying bills and sending owner payouts by hand.\n\nBlaze is an AI CFO for "
          "property managers. It prepares vendor payments and owner payouts, and nothing moves until you approve it.\n\n"
          "Open to a 15-minute call next week to see if it would give you that time back?\n\n{sender}"),
    "B": ("catching payment mistakes early",
          "Hi {name} team,\n\nYour site mentions \"{quote}\" When that runs on manual steps, late payments and "
          "month-end reconciliation surprises tend to creep in.\n\nBlaze reconciles bank activity against your books, "
          "flags late rent, and catches duplicate bills before they're paid.\n\n"
          "Would a 15-minute call be useful to see whether it fits how you work?\n\n{sender}"),
    "C": ("cleaner owner statements",
          "Hi {name} team,\n\nI noticed \"{quote}\" on your site. Owners notice how fast and clean their "
          "distributions are.\n\nBlaze drafts owner statements from each owner's terms and your bank data, so you "
          "review and approve instead of building them by hand.\n\nCould we grab a 15-minute call to see if it would "
          "help your owners?\n\n{sender}"),
}


@cache
def _fixture() -> dict:
    return {c["name"]: c for c in json.loads(config.MOCK_COMPANIES.read_text())}


def score_response(lead: dict) -> str:
    return json.dumps(_fixture()[lead["name"]]["mock_score"])


def draft_response(lead: dict, angle: str, attempt: int) -> str:
    subject, body = TEMPLATES[angle]
    behavior = _fixture()[lead["name"]].get("mock_draft_behavior", "ok")
    quote = lead["evidence_quote"].rstrip(".")
    body = body.format(name=lead["name"], quote=quote + ".", sender=config.SENDER_NAME)
    subject = subject.format(name=lead["name"])
    if behavior == "placeholder_then_ok" and attempt == 1:
        body = body.replace(f"Hi {lead['name']} team,", "Hi [First Name],")
    if behavior == "invented_number":
        body = body.replace("Blaze ", "Teams like yours save 20 hours a month with Blaze. Blaze ", 1)
    return json.dumps({"subject": subject, "body": body})
