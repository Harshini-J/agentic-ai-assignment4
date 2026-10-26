"""Part D: put the three candidate memories in fixtures/memory/ through the write policy
(state/write_policy.md) and into the store.

    python eval/store_candidates.py                # into state/store.db
    python eval/store_candidates.py --dry-run      # show the records, write nothing
    python eval/store_candidates.py --store PATH   # into another file (a test)

Each candidate is decided by who stated it and where it was seen, not by what it claims:

  * RepoMind's own work (A, "who: RepoMind"): kind procedural, source memory, origin derived.
    It is confirmed only if the claims it makes check out against the repository's own files,
    which this script reads (fixtures/repo): so it is dated, derived and verified. It is stored
    for the repository as a whole, because a way to reproduce a bug is not about one issue.
  * A person with standing (C, a maintainer by a discussion header): kind procedural, source user, origin ingested,
    stated_by the login, written_at the day it was said. A person with standing stating it is
    what confirms it, so confirmed is true (policy, section 4). Stored for the repository as a
    whole: a rule about duplicates applies to every issue.
  * Anyone else, in an issue's body (B, no header names them a maintainer): kind episodic, source
    issue:<n>, origin ingested, confirmed false, stated_by the login. It is a record that the issue
    said it, and it carries the words so a reader can see what was claimed. Never semantic.

The standing of a person comes from a discussion header naming them as a maintainer (priya-s,
in issue 4207's discussion), not from the candidate's `who` field: policy, section 4.
written_at is the candidate's `when`: when it was seen, not when this script ran (policy,
section 4).

Run it twice and nothing is written the second time. A memory the store already holds, active or
superseded, is not written again: rerunning this script must never bring back a rule that a later
statement replaced."""

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from state.store import Store  # noqa: E402

MEMORY = ROOT / "fixtures" / "memory"
REPO = ROOT / "fixtures" / "repo"
ISSUES = ROOT / "fixtures" / "issues"


def login(who: str) -> str:
    return who.split(",")[0].strip()


def has_standing(who: str) -> bool:
    """Standing from a discussion header, "### <login> (maintainer)", never from the claim itself."""
    header = re.compile(rf"^###\s+{re.escape(login(who))}\s+\(maintainer\)", re.MULTILINE)
    return any(header.search(path.read_text()) for path in ISSUES.rglob("*.md"))


def is_repominds_own(who: str) -> bool:
    return who.strip().lower().startswith("repomind")


def verify_procedure() -> dict[str, bool]:
    """Candidate A's claims, each checked against the repository's own files."""
    scripts = json.loads((REPO / "package.json").read_text()).get("scripts", {})
    readme = (REPO / "README.md").read_text()
    config = (REPO / "config" / "app.yaml").read_text()
    return {
        "package.json has the build and start scripts": all(name in scripts for name in ("build", "start")),
        "package.json's scripts are exactly start, build, test and lint":
            set(scripts) == {"start", "build", "test", "lint"},
        "package.json has no serve script": "serve" not in scripts,
        "the README tells the reader to run npm run serve": "npm run serve" in readme,
        "the app listens on port 3000 (config/app.yaml)": bool(re.search(r"^port:\s*3000\b", config, re.M)),
    }


def to_record(candidate: dict) -> tuple[dict, str]:
    """The record for one candidate, and the reason in a line."""
    who, when, seen = candidate["who"], str(candidate["when"]), " ".join(candidate["seen"].split())
    where = candidate["where"]
    if is_repominds_own(who):
        checks = verify_procedure()
        confirmed = all(checks.values())
        failed = [name for name, ok in checks.items() if not ok]
        record = {"issue": None, "kind": "procedural", "source": "memory", "origin": "derived",
                  "confirmed": confirmed, "written_at": when,
                  "text": f"{seen} Worked out by RepoMind in {where}."}
        why = ("derived by RepoMind, verified against the repository's files" if confirmed
               else "derived by RepoMind, NOT confirmed: " + "; ".join(failed))
        return record, why
    if has_standing(who):
        record = {"issue": None, "kind": "procedural", "source": "user", "origin": "ingested",
                  "confirmed": True, "written_at": when, "stated_by": login(who),
                  "text": f"{seen} Stated by {who}, in {where}."}
        return record, "stated by a person with standing, who is named; that confirms it"
    record = {"issue": int(candidate["issue"]), "kind": "episodic", "source": f"issue:{candidate['issue']}",
              "origin": "ingested", "confirmed": False, "written_at": when, "stated_by": login(who),
              "text": f"{where.capitalize()}, by {who}, says: {seen}"}
    return record, "anyone can write an issue's text: a record that it said this, never a fact"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="show the records, write nothing")
    ap.add_argument("--store", help="a store file other than state/store.db")
    args = ap.parse_args()

    candidates = [yaml.safe_load(path.read_text()) for path in sorted(MEMORY.glob("*.yaml"))]
    store = None if args.dry_run else Store(Path(args.store) if args.store else None)
    if store is not None and store.mode != "on":
        sys.exit(f"REPOMIND_STORE is {store.mode!r}: this store skips writes. Unset it to store the candidates.")
    before = {r["id"] for r in store.records()} if store else set()
    seen = {(r["source"], " ".join(r["text"].split()).casefold()) for r in store.records()} if store else set()

    for candidate in candidates:
        record, why = to_record(candidate)
        if store is not None and (record["source"], " ".join(record["text"].split()).casefold()) in seen:
            print(f"{candidate['candidate']}: already in the store (active or superseded): nothing written")
            continue
        line = (f"{candidate['candidate']}: {record['kind']}, source {record['source']}, origin "
                f"{record['origin']}, confirmed {str(record['confirmed']).lower()}, written_at "
                f"{record['written_at']}, issue {record['issue']}")
        if store is None:
            print(f"{line}\n   {why}\n   {record['text']}")
            continue
        rid = store.write(record)
        print(f"{line}\n   {why}\n   -> {'merged into' if rid in before else 'stored as'} record {rid}")


if __name__ == "__main__":
    main()