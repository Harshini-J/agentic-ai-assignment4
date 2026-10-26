"""Part E, the contradiction test, session 3: a process of its own that opens the store, retrieves the
maintainer's rule on duplicates, and only that, prints the IDs it got, and exits.

    python eval/store_retrieve.py
    python eval/store_retrieve.py --store PATH     # another store file (a test)

The rule is the active record whose source is user and whose text is about duplicates. An inactive
(superseded) record is never retrieved: after session 2 this should be exactly one ID, the new record's."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from state.store import REPOSITORY, Store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", help="a store file other than state/store.db")
    args = ap.parse_args()
    store = Store(Path(args.store) if args.store else None)
    if store.mode == "off":
        sys.exit("REPOMIND_STORE is 'off': this would read an empty store. Unset it.")
    rules = [r for r in store.active(REPOSITORY)
             if r["source"] == "user" and "duplicate" in r["text"].lower()]
    for r in rules:
        print(f"record {r['id']} (stated by {r.get('stated_by')}, {r['written_at']}, "
              f"supersedes {r['supersedes']}): {r['text']}")
    print("retrieved:", [r["id"] for r in rules])


if __name__ == "__main__":
    main()
