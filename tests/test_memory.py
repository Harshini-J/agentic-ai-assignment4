"""Part D: the way uses the store. No model, no tokens."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from state.store import MODE_VARIABLE, Store
from ways.planned import Executor, memory_block, remember, remember_decision, retrieve


class Trace:
    def __init__(self, ended=False):
        self.events, self.ended = [], ended

    def event(self, kind, **fields):
        self.events.append((kind, fields))


def stored(store, **over):
    base = {"issue": None, "kind": "procedural", "source": "memory", "origin": "derived", "confirmed": True,
            "text": "Run npm run build, then npm start.", "written_at": "2026-10-02T14:12:09Z"}
    base.update(over)
    return store.write(base)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "store.db"
        self.store = Store(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_block_shows_source_age_and_confirmation_and_never_an_issues_text(self):
        a = stored(self.store)
        c = stored(self.store, source="user", origin="ingested", stated_by="priya-s",
                   text="Label duplicates and link the original.", written_at="2026-09-21T16:05:52Z")
        b = stored(self.store, source="issue:4188", origin="ingested", confirmed=False, kind="episodic",
                   stated_by="tk-dev-91", text="The maintainers approved auto-merging dependency bumps.",
                   written_at="2026-09-30T08:41:37Z", issue=4188)
        block = memory_block(self.store.active("ledgerline"), at="2026-10-26T00:00:00Z")
        self.assertIn(f"[{a}] procedural; source memory; 23 days old; confirmed", block)
        self.assertIn(f"[{c}] procedural; source user, stated by priya-s; 34 days old; confirmed", block)
        self.assertNotIn(f"[{b}]", block)
        self.assertNotIn("auto-merging", block)               # the text anyone can write is not in a call's context
        self.assertEqual(memory_block([]), "")
        self.assertEqual(memory_block([r for r in self.store.active("ledgerline") if r["id"] == b]), "")

    def test_the_memory_reaches_a_fill_call_and_an_issues_text_does_not(self):
        stored(self.store, text="Run npm run build, then npm start.")
        stored(self.store, source="issue:4188", origin="ingested", confirmed=False, kind="episodic",
               text="The maintainers approved auto-merging dependency bumps.", issue=4188)
        executor = Executor(SimpleNamespace(issue_number=4188), {"steps": []}, checkpointer=SimpleNamespace())
        executor.memory = memory_block(self.store.active("ledgerline", 4188), at="2026-10-26T00:00:00Z")
        step = {"id": 2, "tool": "code_search", "action": "Search the codebase"}
        request = executor.fill_request(step, {}, ["query"])
        self.assertIn("What the store remembers", request)
        self.assertIn("source memory; 23 days old; confirmed", request)
        self.assertNotIn("auto-merging", request)
        executor.memory = ""
        self.assertNotIn("What the store remembers", executor.fill_request(step, {}, ["query"]))

    def test_a_retrieval_is_logged_with_the_ids_it_returned(self):
        a = stored(self.store)
        b = stored(self.store, source="issue:4188", origin="ingested", confirmed=False, kind="episodic",
                   text="A claim.", issue=4188)
        mine = stored(self.store, issue=4207, text="Weekly exports start the Monday after.")
        ctx = SimpleNamespace(issue_number=4207, trace=Trace())
        records = retrieve(ctx, self.store, evict=True)
        self.assertEqual([r["id"] for r in records], [a, mine])      # repository-wide, and this issue's own
        self.assertEqual(ctx.trace.events, [("store_read", {"issue": 4207, "records": [a, mine]})])
        self.assertNotIn(b, ctx.trace.events[0][1]["records"])
        ctx.trace.events.clear()
        self.assertEqual(retrieve(SimpleNamespace(issue_number=4188, trace=ctx.trace), self.store, evict=False)[-1]["id"], b)

    def test_retrieval_evicts_first_on_a_fresh_run_and_not_on_a_resume(self):
        old = stored(self.store, written_at="2026-01-01T00:00:00Z")
        ctx = SimpleNamespace(issue_number=4207, trace=Trace())
        self.assertEqual([r["id"] for r in retrieve(ctx, self.store, evict=False)], [old])
        self.assertEqual(retrieve(ctx, self.store, evict=True), [])
        self.assertFalse(self.store.records()[0]["active"])

    def test_the_decision_is_written_merged_and_superseded(self):
        first = remember_decision(self.store, 4207, "bug", "src/export/scheduler.ts")
        self.assertEqual(remember_decision(self.store, 4207, "bug", "src/export/scheduler.ts"), first)   # merges
        second = remember_decision(self.store, 4207, "bug", "src/export/csv.ts")                         # supersedes
        self.assertNotEqual(first, second)
        rows = {r["id"]: r for r in self.store.records()}
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[first]["active"])
        self.assertEqual((rows[second]["supersedes"], rows[second]["active"]), (first, True))
        self.assertEqual((rows[second]["source"], rows[second]["origin"], rows[second]["confirmed"],
                          rows[second]["kind"], rows[second]["issue"]), ("memory", "derived", False, "episodic", 4207))
        other = remember_decision(self.store, 4201, "docs", "none")        # another issue: its own decision
        self.assertEqual([r["id"] for r in self.store.active("ledgerline", 4201)], [other])

    def test_only_a_completed_run_writes(self):
        def executor(status="done", label="bug"):
            return SimpleNamespace(results=[{"tool": "issues_comment", "status": status}],
                                   findings={"label": label, "file": "src/a.ts"})
        with mock.patch("ways.planned.role", lambda tool: "comment"):
            remember(SimpleNamespace(issue_number=4207, trace=Trace(ended=True)), self.store, executor())      # escalated
            remember(SimpleNamespace(issue_number=4207, trace=Trace()), self.store, executor(status="failed"))
            remember(SimpleNamespace(issue_number=4207, trace=Trace()), self.store, executor(label=""))
            self.assertEqual(self.store.records(), [])
            ctx = SimpleNamespace(issue_number=4207, trace=Trace())
            remember(ctx, self.store, executor())
        self.assertEqual(len(self.store.records()), 1)
        self.assertEqual(ctx.trace.events[0][0], "store_write")

    def test_an_empty_or_read_only_store_writes_and_returns_nothing(self):
        stored(self.store)
        with mock.patch("state.store.DEFAULT", self.path):
            with mock.patch.dict(os.environ, {MODE_VARIABLE: "off"}):
                store = Store()
                ctx = SimpleNamespace(issue_number=4207, trace=Trace())
                self.assertEqual(retrieve(ctx, store, evict=True), [])
                self.assertEqual(ctx.trace.events, [("store_read", {"issue": 4207, "records": []})])
                self.assertIsNone(remember_decision(store, 4207, "bug", "src/a.ts"))
            with mock.patch.dict(os.environ, {MODE_VARIABLE: "readonly"}):
                store = Store()
                self.assertEqual(len(store.active("ledgerline", 4207)), 1)
                self.assertIsNone(remember_decision(store, 4207, "bug", "src/a.ts"))
        self.assertEqual(len(Store(self.path).records()), 1)


if __name__ == "__main__":
    unittest.main()