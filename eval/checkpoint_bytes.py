"""Part E, task 3: what a run's checkpoints cost on disk, as saved and with the conversation.

    python eval/checkpoint_bytes.py RUN_ID [RUN_ID ...]

For each run (on a development issue, 4191 or 4192, so it has a request record), the
structured size is the summed length in bytes of the checkpoints the run saved through
state/checkpointer.py, as the checkpoint database holds them. For the other size, each of
those checkpoints is copied with one more field, `conversation`: every request the run had
sent the model before that checkpoint (its system prompt and messages, from
runs/<run_id>.requests.jsonl), and the copies go to eval/checkpoints_with_conversation.db,
never to the checkpoint database, whose checkpoints hold no conversation. Prints both
sizes per run and their means, and the JSON for reports/LAB_04.json.
"""

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from state.checkpointer import DEFAULT as CHECKPOINTS  # noqa: E402

COPIES = ROOT / "eval" / "checkpoints_with_conversation.db"


def lines(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def measure(run_id: str, copies: sqlite3.Connection) -> tuple[int, int, int]:
    """(structured bytes, with-conversation bytes, checkpoints) for one run."""
    db = sqlite3.connect(f"file:{CHECKPOINTS}?mode=ro", uri=True)
    rows = db.execute("SELECT checkpoint, LENGTH(CAST(checkpoint AS BLOB)) FROM checkpoints "
                      "WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    db.close()
    requests_path = ROOT / "runs" / f"{run_id}.requests.jsonl"
    if not rows or not requests_path.exists():
        raise SystemExit(f"{run_id}: needs saved checkpoints and a request record (a run on 4191 or 4192)")
    requests = {r["n"]: {"system": r["system"], "messages": r["messages"]} for r in lines(requests_path)}
    # the k-th checkpoint line in the trace is the k-th checkpoint saved; the conversation at it is
    # every request the run's model_call lines named before it
    sent, at_checkpoint = 0, []
    for r in lines(ROOT / "runs" / f"{run_id}.jsonl"):
        if r.get("type") == "model_call" and r.get("request"):
            sent = max(sent, r["request"])
        elif r.get("type") == "checkpoint":
            at_checkpoint.append(sent)
    if len(at_checkpoint) != len(rows):
        raise SystemExit(f"{run_id}: {len(rows)} checkpoints saved but {len(at_checkpoint)} checkpoint lines")
    structured = sum(n for _, n in rows)
    with_conversation = 0
    copies.execute("DELETE FROM copies WHERE run_id = ?", (run_id,))
    for (text, _), upto in zip(rows, at_checkpoint):
        copy = {**json.loads(text), "conversation": [requests[n] for n in sorted(requests) if n <= upto]}
        data = json.dumps(copy, ensure_ascii=False)
        copies.execute("INSERT INTO copies (run_id, checkpoint) VALUES (?, ?)", (run_id, data))
        with_conversation += len(data.encode("utf-8"))
    copies.commit()
    return structured, with_conversation, len(rows)


def main() -> None:
    run_ids = sys.argv[1:]
    if not run_ids:
        raise SystemExit(__doc__)
    copies = sqlite3.connect(COPIES)
    copies.execute("CREATE TABLE IF NOT EXISTS copies (id INTEGER PRIMARY KEY, run_id TEXT, checkpoint TEXT)")
    results = []
    for run_id in run_ids:
        s, c, k = measure(run_id, copies)
        results.append((s, c))
        print(f"{run_id}: {k} checkpoints, structured {s:,} bytes, with the conversation {c:,} bytes ({c / s:.1f}x)")
    copies.close()
    mean_s = round(sum(s for s, _ in results) / len(results))
    mean_c = round(sum(c for _, c in results) / len(results))
    print(f"\nmean over {len(results)} runs: structured {mean_s:,} bytes, with the conversation {mean_c:,} bytes "
          f"({mean_c / mean_s:.1f}x)")
    print(json.dumps({"checkpoint_bytes": {"structured": mean_s, "with_conversation": mean_c, "runs": len(results)}}))


if __name__ == "__main__":
    main()
