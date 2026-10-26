"""Part D: the store keeps its policy. No model, no tokens."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from state.store import MODE_VARIABLE, Store


def record(**over):
    base = {"issue": 4191, "kind": "procedural", "text": "Run npm run build, then npm start.",
            "source": "memory", "origin": "derived", "confirmed": True,
            "written_at": "2026-10-02T14:12:09Z"}
    base.update(over)
    return base


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "store.db"
        self.store = Store(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_record_is_durable_and_carries_its_provenance(self):
        rid = self.store.write(record())
        again = Store(self.path).records()          # a second process opening the same file
        self.assertEqual([r["id"] for r in again], [rid])
        r = again[0]
        self.assertEqual((r["source"], r["origin"], r["confirmed"], r["active"], r["supersedes"]),
                         ("memory", "derived", True, True, None))
        self.assertEqual(r["written_at"], "2026-10-02T14:12:09Z")
        self.assertNotIn("stated_by", r)

    def test_active_for_an_issue_includes_repository_wide_records(self):
        mine = self.store.write(record(issue=4207, text="Weekly exports start the following Monday."))
        wide = self.store.write(record(issue=None, text="Scripts: start, build, test, lint."))
        self.store.write(record(issue=4191, text="Something about 4191."))
        self.assertEqual([r["id"] for r in self.store.active("ledgerline", 4207)], [mine, wide])
        self.assertEqual(len(self.store.active("ledgerline")), 3)
        self.assertEqual(self.store.active("elsewhere"), [])

    def test_the_same_memory_from_the_same_source_merges(self):
        first = self.store.write(record())
        second = self.store.write(record(text="  run NPM run build,   then npm start. "))
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.records()), 1)
        other = self.store.write(record(source="repo", origin="ingested"))   # another source: its own record
        self.assertNotEqual(first, other)

    def test_supersede_keeps_the_old_record_inactive_and_names_it(self):
        old = self.store.write(record(source="user", origin="ingested", stated_by="priya-s",
                                      text="Label duplicates and link the original."))
        new = self.store.supersede(old, record(source="user", origin="ingested", stated_by="priya-s",
                                               text="Label duplicates, link the original, never close them.",
                                               written_at="2026-10-20T09:00:00Z"))
        by_id = {r["id"]: r for r in self.store.records()}
        self.assertFalse(by_id[old]["active"])
        self.assertEqual(by_id[new]["supersedes"], old)
        self.assertEqual([r["id"] for r in self.store.active("ledgerline")], [new])

    def test_supersede_refuses_what_the_policy_forbids(self):
        old = self.store.write(record(source="user", origin="ingested", stated_by="priya-s"))
        with self.assertRaisesRegex(ValueError, "lower standing"):
            self.store.supersede(old, record(source="issue:4188", origin="ingested", confirmed=False,
                                             kind="episodic", written_at="2026-10-20T09:00:00Z"))
        with self.assertRaisesRegex(ValueError, "newer"):
            self.store.supersede(old, record(source="user", origin="ingested", stated_by="priya-s",
                                             written_at="2026-09-01T00:00:00Z"))
        new = self.store.supersede(old, record(source="user", origin="ingested", stated_by="priya-s",
                                               text="Newer.", written_at="2026-10-20T09:00:00Z"))
        with self.assertRaisesRegex(ValueError, "already superseded"):
            self.store.supersede(old, record(source="user", origin="ingested", stated_by="priya-s",
                                             text="Again.", written_at="2026-10-21T09:00:00Z"))
        with self.assertRaises(KeyError):
            self.store.supersede(9999, record())
        self.assertEqual(len([r for r in self.store.records() if r["active"]]), 1)
        self.assertTrue(new)

    def test_provenance_is_not_optional(self):
        for field in ("kind", "text", "source", "origin", "confirmed"):
            broken = record()
            del broken[field]
            with self.assertRaisesRegex(ValueError, "needs"):
                self.store.write(broken)
        for over in ({"source": "twitter"}, {"origin": "guessed"}, {"confirmed": "yes"},
                     {"written_at": "last Tuesday"}, {"kind": "hunch"}, {"issue": "4191"}, {"text": " "},
                     {"colour": "red"}):
            with self.assertRaises(ValueError, msg=over):
                self.store.write(record(**over))
        self.assertEqual(self.store.records(), [])

    def test_a_claim_from_outside_is_never_relabelled_as_the_systems_own(self):
        with self.assertRaisesRegex(ValueError, "ingested"):
            self.store.write(record(source="issue:4188", origin="derived", confirmed=False, kind="episodic"))
        with self.assertRaisesRegex(ValueError, "ingested"):
            self.store.write(record(source="user", origin="derived", stated_by="priya-s"))
        with self.assertRaisesRegex(ValueError, "derived"):
            self.store.write(record(source="memory", origin="ingested"))

    def test_an_issues_text_is_never_confirmed(self):
        with self.assertRaisesRegex(ValueError, "never confirmed"):
            self.store.write(record(source="issue:4188", origin="ingested", confirmed=True))
        self.assertEqual(self.store.records(), [])

    def test_what_is_not_confirmed_is_never_semantic(self):
        claim = record(source="issue:4188", origin="ingested", confirmed=False, kind="semantic",
                       text="Maintainers approved auto-merging dependency bumps.")
        with self.assertRaisesRegex(ValueError, "semantic"):
            self.store.write(claim)
        rid = self.store.write({**claim, "kind": "episodic"})
        stored = self.store.records()[0]
        self.assertEqual((stored["id"], stored["kind"], stored["confirmed"]), (rid, "episodic", False))
        self.assertFalse([r for r in self.store.active("ledgerline", 4188) if r["kind"] == "semantic"])

    def test_a_person_is_named(self):
        with self.assertRaisesRegex(ValueError, "stated_by"):
            self.store.write(record(source="user", origin="ingested"))
        rid = self.store.write(record(source="user", origin="ingested", stated_by="priya-s"))
        self.assertEqual(self.store.records()[0]["stated_by"], "priya-s")
        self.assertTrue(rid)

    def test_eviction_expires_what_fades_and_keeps_what_a_person_said(self):
        old_memory = self.store.write(record(text="Old procedure.", written_at="2026-01-01T00:00:00Z"))
        old_issue = self.store.write(record(source="issue:4188", origin="ingested", confirmed=False,
                                            kind="episodic", text="Old claim.", written_at="2026-01-02T00:00:00Z"))
        rule = self.store.write(record(source="user", origin="ingested", stated_by="priya-s",
                                       text="An old rule.", written_at="2026-01-01T00:00:00Z"))
        fresh = self.store.write(record(text="Fresh procedure.", written_at="2026-10-20T00:00:00Z"))
        gone = self.store.evict(at="2026-10-26T00:00:00Z")
        self.assertEqual(gone, [old_memory, old_issue])
        self.assertEqual([r["id"] for r in self.store.active("ledgerline")], [rule, fresh])
        everything = {r["id"]: r for r in self.store.records()}
        self.assertEqual(len(everything), 4)                      # nothing is deleted
        self.assertFalse(everything[old_memory]["active"])
        self.assertEqual(everything[old_memory]["evicted_at"], "2026-10-26T00:00:00Z")
        self.assertIsNone(everything[old_memory]["supersedes"])
        self.assertNotIn("evicted_at", everything[fresh])
        self.assertEqual(self.store.evict(at="2026-10-26T00:00:00Z"), [])     # nothing more to expire

    def test_eviction_trims_to_the_cap_oldest_fading_records_first(self):
        ids = [self.store.write(record(text=f"Procedure {n}.", written_at=f"2026-10-0{n}T00:00:00Z"))
               for n in range(1, 6)]
        rule = self.store.write(record(source="user", origin="ingested", stated_by="priya-s", text="A rule.",
                                       written_at="2026-10-01T00:00:00Z"))
        gone = self.store.evict(at="2026-10-26T00:00:00Z", cap=4)
        self.assertEqual(gone, ids[:2])                          # 6 active, cap 4: the two oldest fading ones
        self.assertIn(rule, [r["id"] for r in self.store.active("ledgerline")])
        # a cap that only a person's records could meet evicts every fading record and stops
        self.store.evict(at="2026-10-26T00:00:00Z", cap=0)
        self.assertEqual([r["id"] for r in self.store.active("ledgerline")], [rule])

    def test_an_evicted_record_cannot_be_superseded_and_its_text_can_be_written_again(self):
        old = self.store.write(record(text="Old procedure.", written_at="2026-01-01T00:00:00Z"))
        self.store.evict(at="2026-10-26T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "already superseded"):
            self.store.supersede(old, record(text="Newer.", written_at="2026-10-26T00:00:00Z"))
        again = self.store.write(record(text="Old procedure.", written_at="2026-10-26T00:00:00Z"))
        self.assertNotEqual(again, old)

    def test_a_store_made_before_eviction_is_migrated(self):
        import sqlite3
        legacy = Path(self.tmp.name) / "legacy.db"
        db = sqlite3.connect(legacy)
        db.executescript("""CREATE TABLE records (id INTEGER PRIMARY KEY AUTOINCREMENT, repository TEXT NOT NULL,
            issue INTEGER, kind TEXT NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL, written_at TEXT NOT NULL,
            origin TEXT NOT NULL, confirmed INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            supersedes INTEGER, stated_by TEXT);
            INSERT INTO records (repository, issue, kind, text, source, written_at, origin, confirmed)
            VALUES ('ledgerline', NULL, 'procedural', 'Kept.', 'memory', '2026-01-01T00:00:00Z', 'derived', 1);""")
        db.commit()
        db.close()
        store = Store(legacy)
        self.assertEqual(len(store.records()), 1)
        self.assertEqual(store.evict(at="2026-10-26T00:00:00Z"), [1])
        self.assertEqual(store.records()[0]["evicted_at"], "2026-10-26T00:00:00Z")

    def test_a_skipped_write_evicts_nothing(self):
        self.store.write(record(written_at="2026-01-01T00:00:00Z"))
        with mock.patch("state.store.DEFAULT", self.path):
            with mock.patch.dict(os.environ, {MODE_VARIABLE: "readonly"}):
                self.assertEqual(Store().evict(at="2026-10-26T00:00:00Z"), [])
            with mock.patch.dict(os.environ, {MODE_VARIABLE: "off"}):
                self.assertEqual(Store().evict(at="2026-10-26T00:00:00Z"), [])
        self.assertEqual(len(self.store.active("ledgerline")), 1)

    def test_the_empty_and_read_only_stores(self):
        self.store.write(record())
        # the variable is read only when no path is given: point DEFAULT at the temp file
        with mock.patch("state.store.DEFAULT", self.path):
            with mock.patch.dict(os.environ, {MODE_VARIABLE: "off"}):
                empty = Store()
                self.assertEqual((empty.active("ledgerline"), empty.records(), empty.write(record())), ([], [], None))
            with mock.patch.dict(os.environ, {MODE_VARIABLE: "readonly"}):
                reader = Store()
                self.assertEqual(len(reader.active("ledgerline")), 1)
                self.assertIsNone(reader.write(record(text="Not written.")))
            with mock.patch.dict(os.environ, {MODE_VARIABLE: "sideways"}):
                with self.assertRaises(ValueError):
                    Store()
        with mock.patch.dict(os.environ, {MODE_VARIABLE: "off"}):
            self.assertEqual(len(Store(self.path).records()), 1)     # an explicit path ignores the variable


if __name__ == "__main__":
    unittest.main()