"""Part F: for runs on 4191 or 4192, where was the maintainer's fact in what each call was sent,
and what did the comment state? Reads runs/<run_id>.jsonl and runs/<run_id>.requests.jsonl, as the
checker does (checks/FORMATS.md, section 12.11). No model, no tokens.

    python eval/forgetting.py RUN_ID [RUN_ID ...]
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness.formats import parse_triage_comment, request_text, squeeze  # noqa: E402
from harness.roles import arg, tool_for  # noqa: E402
from ways.planned import FACT  # noqa: E402


def lines(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def where(text: str, fact: str) -> str:
    """Where the fact sits in what a call was sent: its place as a share of the text, and the
    distance to the nearer end in characters."""
    text, fact = squeeze(text), squeeze(fact)
    at = text.find(fact)
    if at < 0:
        return "not sent"
    near = min(at, len(text) - (at + len(fact)))
    return f"sent at {at / len(text):.0%} of {len(text):,} characters; {near:,} from the nearer end"


def report(run_id: str) -> None:
    trace = lines(ROOT / "runs" / f"{run_id}.jsonl")
    requests = {r["n"]: r for r in lines(ROOT / "runs" / f"{run_id}.requests.jsonl")}
    comment, body = tool_for("comment"), arg("comment", "body")
    posted = next((i for i in range(len(trace) - 1, -1, -1)
                   if trace[i].get("type") == "tool_call" and trace[i].get("name") == comment), None)
    if posted is None:
        print(f"{run_id}: no comment was posted")
        return
    label, file = parse_triage_comment(str((trace[posted].get("input") or {}).get(body)))
    drafting = None
    for fallback in (False, True):                      # a call that states the posted label and file, else any that called the tool
        for i in range(posted - 1, -1, -1):
            if trace[i].get("type") != "model_call":
                continue
            for c in trace[i].get("tool_calls") or []:
                if c.get("name") == comment and (fallback or parse_triage_comment(str((c.get("input") or {}).get(body))) == (label, file)):
                    drafting = i
                    break
            if drafting is not None:
                break
        if drafting is not None:
            break
    print(f"{run_id}: the comment states label {label}, file {file}")
    from harness.tracker import Tracker
    tracked = Tracker().comments(run_id, 4192 if "-4192-" in run_id else 4191)
    print(f"  tracker: {len(tracked)} comment(s)", [parse_triage_comment(c["body"]) for c in tracked])
    compactions = [i for i, r in enumerate(trace) if r.get("type") == "compaction"]
    first = compactions[0] if compactions else len(trace)
    for i, r in enumerate(trace):
        if r.get("type") != "model_call" or not r.get("request") or r["request"] not in requests:
            continue
        sent = where(request_text(requests[r["request"]]), FACT)
        mark = "  <- the drafting call" if i == drafting else ""
        before = "  (before the first compaction)" if i < first else ""
        print(f"  request {r['request']:>2}, {r.get('purpose') or 'other'}: {sent}{before}{mark}")
    print(f"  compaction lines before the drafting call: {sum(1 for c in compactions if drafting is not None and c < drafting)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for run_id in sys.argv[1:]:
        report(run_id)