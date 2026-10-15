"""Step 5: which angle gets replies? Reads the hand-maintained replies.csv."""

from . import config
from .store import read_csv, write_csv

MIN_SENDS = 10
REPLY_FIELDS = ["company", "sent_date", "replied", "meeting"]


def ensure_replies_file() -> None:
    if not config.REPLIES_CSV.exists():
        write_csv(config.REPLIES_CSV, [], REPLY_FIELDS)


def experiment(replies_path, drafts: list[dict]) -> dict:
    """Per-angle sends, reply rate and meeting rate. Angle comes from an `angle` column or drafts.csv."""
    angle_of = {d["company"].lower(): d["angle"] for d in drafts}
    stats = {a: {"angle": a, "name": config.ANGLES[a][0], "sends": 0, "replies": 0, "meetings": 0} for a in "ABC"}
    unmatched = 0
    for r in read_csv(replies_path):
        a = (r.get("angle") or angle_of.get(r.get("company", "").lower(), "")).upper()
        if a not in stats:
            unmatched += 1
            continue
        s = stats[a]
        s["sends"] += 1
        s["replies"] += r.get("replied", "").lower().startswith("y")
        s["meetings"] += r.get("meeting", "").lower().startswith("y")
    for s in stats.values():
        n = s["sends"]
        s["reply_rate"] = s["replies"] / n if n else None
        s["meeting_rate"] = s["meetings"] / n if n else None
        s["enough_data"] = n >= MIN_SENDS
    ready = [s for s in stats.values() if s["enough_data"]]
    leader = max(ready, key=lambda s: (s["reply_rate"], s["meeting_rate"]))["angle"] if len(ready) >= 2 else None
    return {"angles": list(stats.values()), "min_sends": MIN_SENDS, "unmatched": unmatched,
            "leader": leader, "source": str(replies_path.relative_to(config.ROOT))}


def format_table(exp: dict) -> str:
    lines = [f"{'Angle':<22}{'Sends':>6}{'Reply rate':>16}{'Meeting rate':>16}"]
    for s in exp["angles"]:
        label = f"{s['angle']}: {s['name']}"
        if not s["enough_data"]:
            lines.append(f"{label:<22}{s['sends']:>6}   not enough data (<{exp['min_sends']} sends)")
        else:
            rr = f"{s['reply_rate']:.0%} ({s['replies']})"
            mr = f"{s['meeting_rate']:.0%} ({s['meetings']})"
            lines.append(f"{label:<22}{s['sends']:>6}{rr:>16}{mr:>16}")
    if exp["leader"]:
        lines.append(f"\nLeading angle: {exp['leader']}")
    if exp["unmatched"]:
        lines.append(f"\n{exp['unmatched']} reply row(s) skipped: company not found in drafts.csv")
    return "\n".join(lines)
