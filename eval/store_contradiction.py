"""Part D: supersede, do not append (slide 25). A maintainer changes the rule about duplicates
that candidate C stored; the old record is replaced, not left beside the new one.

    python eval/store_contradiction.py                 # the default changed rule, said by priya-s, now
    python eval/store_contradiction.py --text "..." --by priya-s --when 2026-10-26T15:00:00Z
    python eval/store_contradiction.py --dry-run       # show what would be replaced, write nothing
    python eval/store_contradiction.py --store PATH    # another store file (a test)

The new statement is YOUR test input: the fixtures hold no later statement, so the text, the
person and the time are arguments. The person must be a maintainer by a discussion header
(### <login> (maintainer)), as for candidate C. The new record is source user, origin ingested,
confirmed, stated_by the person, written_at the time it was said (default: now), and it must be
newer than the record it replaces.

It replaces the one active user record that mentions duplicates, through Store.supersede: the
new record names the old one in `supersedes`, the old one goes inactive and stays in the store,
and one rule is active. Run it twice and the second run writes nothing."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
from state.store import Store, now  # noqa: E402
from store_candidates import has_standing  # noqa: E402

DEFAULT_TEXT = ("For duplicates: label them `duplicate`, link the original, and close them once the reporter "
                "has seen the link. The earlier rule never to close them no longer applies.")


def norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def duplicate_rules(store: Store, active_only: bool) -> list[dict]:
    return [r for r in store.records() if r["source"] == "user" and "duplicate" in r["text"].lower()
            and (r["active"] or not active_only)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=DEFAULT_TEXT, help="the changed rule, as the maintainer states it")
    ap.add_argument("--by", default="priya-s", help="the maintainer's login")
    ap.add_argument("--when", default=None, help="when it was said, ISO 8601 (default: now, in UTC)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--store", help="a store file other than state/store.db")
    args = ap.parse_args()

    store = Store(Path(args.store) if args.store else None)
    if store.mode != "on":
        sys.exit(f"REPOMIND_STORE is {store.mode!r}: this store skips writes. Unset it to run this.")
    if not has_standing(args.by):
        sys.exit(f"{args.by} has no '### {args.by} (maintainer)' header in the fixtures: no standing to change the rule.")
    if any(norm(r["text"]) == norm(args.text) for r in duplicate_rules(store, active_only=False)):
        print("this rule is already in the store (active or superseded): nothing written")
        return
    old = duplicate_rules(store, active_only=True)
    if len(old) != 1:
        sys.exit(f"expected exactly one active user record about duplicates, found {[r['id'] for r in old]}: "
                 "run eval/store_candidates.py first" if not old else
                 f"more than one active rule about duplicates, {[r['id'] for r in old]}: resolve that by hand")
    old = old[0]
    new = {"kind": "procedural", "source": "user", "origin": "ingested", "confirmed": True, "issue": None,
           "stated_by": args.by, "written_at": args.when or now(), "text": args.text.strip()}
    print(f"replaces record {old['id']} (written {old['written_at']}): {old['text']}")
    print(f"with: {new['text']}\n      stated by {new['stated_by']}, {new['written_at']}")
    if args.dry_run:
        return
    new_id = store.supersede(old["id"], new)
    print(f"-> record {new_id} supersedes {old['id']}; record {old['id']} is inactive and stays in the store")
    print("active rules about duplicates:", [r["id"] for r in duplicate_rules(store, active_only=True)])


if __name__ == "__main__":
    main()