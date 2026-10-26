"""Parts B and C: the crash matrix. For every plan step N, kill issue 4201 after it and
during it, resume each run, and read the tracker for the run. Prints the entries for
reports/LAB_04.json and saves them to eval/matrix_<mode>.json.

    python eval/crash_matrix.py --mode derived --uncrashed <run_id>
    python eval/crash_matrix.py --mode uuid --uncrashed <run_id>      # Part C, after make reset
    python eval/crash_matrix.py --mode derived --uncrashed <run_id> --cases 3:during,5:after
    python eval/crash_matrix.py --mode derived --read 5:during:<run_id>   # a case you resumed by hand
    python eval/crash_matrix.py --mode derived --uncrashed <run_id> --backend harness.scripted:ScriptedTriage   # free dry run

It reads the tracker's comments and prs tables, never its attempts table. Never run
make reset between a crash and its resume."""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness.formats import parse_triage_comment   # noqa: E402
from state.checkpointer import Checkpointer          # noqa: E402

ISSUE = 4201
BRANCH = f"repomind/issue-{ISSUE}"
KILLED = re.compile(r"killed (\S+) (?:during|after) step")


def trace_lines(run_id):
    path = ROOT / "runs" / f"{run_id}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def numbered_steps(run_id):
    """How many plan steps called a tool: the steps a kill can land on."""
    seen = []
    for r in trace_lines(run_id):
        if r.get("type") == "tool_call" and r.get("plan_step") not in seen:
            seen.append(r["plan_step"])
    return len(seen)


def plan_length(run_id):
    """The number of steps in the run's first plan (a restart writes a second plan line)."""
    for r in trace_lines(run_id):
        if r.get("type") == "plan":
            return len(r["plan"]["steps"])
    return None


def tracker(run_id):
    db = sqlite3.connect(ROOT / "tracker" / "tracker.db")
    try:
        comments = db.execute("select id, body from comments where run_id = ? order by id", (run_id,)).fetchall()
        prs = db.execute("select id from prs where run_id = ? and branch = ? and state = 'open' order by id",
                         (run_id, BRANCH)).fetchall()
    finally:
        db.close()
    return comments, prs


def matches(run_id, comments, prs):
    """One comment stating the label and file of the run's own last checkpoint, and a PR
    exactly when that file is not none."""
    last = Checkpointer().last(run_id)
    if last is None or len(comments) != 1:
        return False
    found = last["findings"]
    label, file = parse_triage_comment(comments[0][1])
    wants_pr = 0 if found.get("file") in (None, "none") else 1
    return label == found.get("label") and (file or "none") == found.get("file") and len(prs) == wants_pr


def repeated(run_id):
    """Tool calls after the resume line that repeat one from before the crashed half's last
    checkpoint line (Part B's 'resume, not restart')."""
    lines = trace_lines(run_id)
    cut = next((i for i, r in enumerate(lines) if r.get("type") == "resume"), None)
    if cut is None:
        return None
    marks = [i for i, r in enumerate(lines[:cut]) if r.get("type") == "checkpoint"]
    if not marks:
        return 0

    def key(r):
        return (r.get("plan_step"), r.get("name"), json.dumps(r.get("input"), sort_keys=True))
    done = {key(r) for r in lines[:marks[-1]] if r.get("type") == "tool_call"}
    return sum(1 for r in lines[cut:] if r.get("type") == "tool_call" and key(r) in done)


def load(path):
    return json.loads(path.read_text()) if path.exists() else {}


def record(saved, step, when, run_id):
    """Read a run's tracker rows and save the case. Returns the entry."""
    comments, prs = tracker(run_id)
    entry = {"run_id": run_id, "step": step, "when": when, "comments": len(comments),
             "open_prs": len(prs), "matches": matches(run_id, comments, prs)}
    for part in ("cases", "comment_ids", "repeated", "plan_steps"):
        saved.setdefault(part, {})
    saved["cases"][f"{step}:{when}"] = entry          # the latest run of a case is the one read
    saved["comment_ids"][run_id] = [c[0] for c in comments]
    saved["repeated"][run_id] = repeated(run_id)
    saved["plan_steps"][run_id] = plan_length(run_id)
    return entry


def run_case(step, when, mode, backend, saved):
    env = dict(os.environ)
    env.pop("KEY_MODE", None)
    if mode == "uuid":
        env["KEY_MODE"] = "uuid"
    extra = ["--backend", backend] if backend else []
    cmd = [sys.executable, "run.py", "--way", "planned", "--issues", str(ISSUE),
           f"--die-{when}-step", str(step)] + extra
    kill = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    out = kill.stdout + kill.stderr
    found = KILLED.search(out)
    if not found:
        print(f"  the kill did not fire (exit {kill.returncode}): run this case again\n{out[-500:]}")
        return None
    run_id = found.group(1)
    resume_env = dict(env)
    resume_env.pop("KEY_MODE", None)
    resume = subprocess.run([sys.executable, "run.py", "--resume", run_id] + extra, cwd=ROOT,
                            env=resume_env, capture_output=True, text=True)
    out = resume.stdout + resume.stderr
    if resume.returncode != 0 or "ERROR" in out:
        print(f"  {run_id}: the resume failed (exit {resume.returncode}); resume it by hand, then record it "
              f"with --read {step}:{when}:{run_id}\n{out[-500:]}")
        return None
    return record(saved, step, when, run_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("derived", "uuid"), required=True)
    ap.add_argument("--uncrashed", help="the uncrashed run's run_id: its plan gives the steps to cover")
    ap.add_argument("--steps", type=int, help="the number of steps, when you give no --uncrashed")
    ap.add_argument("--cases", help="only these, such as 3:during,5:after")
    ap.add_argument("--read", help="record a case you resumed by hand: step:when:run_id, "
                                   "such as 5:during:planned-4201-s01-abc123")
    ap.add_argument("--backend", help="e.g. harness.scripted:ScriptedTriage, to test the script for free")
    args = ap.parse_args()
    out = ROOT / "eval" / f"matrix_{args.mode}.json"
    saved = load(out)

    if args.read:
        step, when, run_id = args.read.split(":", 2)
        entry = record(saved, int(step), when, run_id)
        out.write_text(json.dumps(saved, indent=2))
        print(json.dumps(entry, indent=2))
        return

    steps = args.steps or (numbered_steps(args.uncrashed) if args.uncrashed else None)
    if not steps:
        sys.exit("give --uncrashed <run_id> or --steps N")
    wanted = ([(int(s), w) for s, w in (c.split(":") for c in args.cases.split(","))] if args.cases
              else [(n, w) for n in range(1, steps + 1) for w in ("after", "during")])
    for step, when in wanted:
        print(f"step {step}, {when} ...", flush=True)
        entry = run_case(step, when, args.mode, args.backend, saved)
        if entry is None:
            continue
        rid = entry["run_id"]
        print(f"  {rid}: comments {entry['comments']} {saved['comment_ids'][rid]}, open PRs {entry['open_prs']}, "
              f"matches {entry['matches']}, repeated calls after resume {saved['repeated'][rid]}, "
              f"plan steps {saved['plan_steps'][rid]}", flush=True)
        out.write_text(json.dumps(saved, indent=2))

    cases = sorted(saved.get("cases", {}).values(), key=lambda e: (e["step"], e["when"] != "after"))
    print("\nmatrix_" + args.mode + ":\n" + json.dumps(cases, indent=2))
    odd = {r: n for r, n in saved.get("plan_steps", {}).items() if n != plan_length(args.uncrashed or "")} \
        if args.uncrashed else {}
    if odd:
        print("\ncrashed runs whose plan had a different number of steps than the uncrashed run:\n"
              + json.dumps(odd, indent=2))
    if args.mode == "uuid":
        dupes = {e["run_id"]: saved["comment_ids"][e["run_id"]] for e in cases if e["comments"] > 1}
        print("\nduplicate_comment_ids:\n" + json.dumps(dupes, indent=2))


if __name__ == "__main__":
    main()