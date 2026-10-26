"""Assignment 4, Part A: the checkpointer. This file is yours.

A checkpointer saves one run's state, so its checkpoints are keyed by the run's
run_id. They live in a SQLite file, which survives a crash.

The mode says how save() writes (Lecture 4, slide 12):
  sync   the checkpoint is on disk before save() returns
  async  save() returns at once; one background thread writes it
  exit   checkpoints are held in memory and written by flush(), at the end of a run

A killed process runs no cleanup, so async and exit checkpoints not yet written are
lost: that is their cost. RepoMind's runs use sync. The way calls close() when a run
ends, which writes whatever async and exit still hold.

last(), all() and runs() read the file only: what they return is what a resume in
a new process would find.

The checker calls these, with these exact signatures (checks/FORMATS.md, section
12.4):

    Checkpointer(path=None)
    .save(run_id, checkpoint, *, step, mode)
    .last(run_id) -> dict | None
    .all(run_id) -> list[dict]
    .runs() -> list[str]
"""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from harness import ROOT
from harness.formats import CHECKPOINT_FIELDS, DURABILITY_MODES

DEFAULT = ROOT / "state" / "checkpoints.db"
CONVERSATION_KEYS = {"conversation", "messages", "history", "transcript"}

TABLE = """CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step TEXT,
    mode TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    checkpoint TEXT NOT NULL
)"""
INDEX = "CREATE INDEX IF NOT EXISTS checkpoints_by_run ON checkpoints (run_id, id)"


def conversation_in(value) -> str | None:
    """What in `value` looks like the conversation, or None. A checkpoint holds no
    conversation: no field named for one, and no list of chat messages."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in CONVERSATION_KEYS:
                return f"a field named {key!r}"
            found = conversation_in(item)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        if value and all(isinstance(m, dict) and "role" in m and "content" in m for m in value):
            return "a list of chat messages"
        for item in value:
            found = conversation_in(item)
            if found:
                return found
    return None


def encode(checkpoint: dict) -> str:
    """The checkpoint as JSON text, or ValueError. The text is made when save() is
    called, so a later change to the dict cannot change what was saved."""
    if not isinstance(checkpoint, dict):
        raise ValueError("a checkpoint is a dict")
    fields = set(CHECKPOINT_FIELDS)
    if set(checkpoint) != fields:
        missing, extra = sorted(fields - set(checkpoint)), sorted(set(checkpoint) - fields)
        raise ValueError(f"a checkpoint holds exactly {sorted(fields)}; missing {missing}, extra {extra}")
    found = conversation_in(checkpoint)
    if found:
        raise ValueError(f"a checkpoint holds no conversation, but this one has {found}")
    try:
        return json.dumps(checkpoint, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"a checkpoint must serialize to JSON: {exc}") from exc


class Checkpointer:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or DEFAULT)
        self._held: list[tuple] = []           # exit mode: rows waiting for flush()
        self._pending: list = []               # async mode: writes the thread has not finished
        self._pool: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()

    # -- writing ---------------------------------------------------------------------------
    def save(self, run_id: str, checkpoint: dict, *, step, mode: str = "sync") -> None:
        if mode not in DURABILITY_MODES:
            raise ValueError(f"mode is one of {sorted(DURABILITY_MODES)}, not {mode!r}")
        row = (str(run_id), json.dumps(step), mode,
               datetime.now(timezone.utc).isoformat(timespec="seconds"), encode(checkpoint))
        if mode == "sync":
            self._write([row])                 # on disk before this returns
        elif mode == "async":
            with self._lock:
                if self._pool is None:         # one worker, so checkpoints land in order
                    self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="checkpointer")
                self._pending.append(self._pool.submit(self._write, [row]))
        else:                                  # exit
            with self._lock:
                self._held.append(row)

    def flush(self) -> None:
        """Wait for async writes, and write what exit mode held. Call it when a run ends."""
        with self._lock:
            pending, held = self._pending, self._held
            self._pending, self._held = [], []
        for future in pending:
            future.result()                    # raises here if a background write failed
        if held:
            self._write(held)

    def close(self) -> None:
        self.flush()
        if self._pool is not None:
            self._pool.shutdown()
            self._pool = None

    def _write(self, rows: list[tuple]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("PRAGMA synchronous = FULL")      # the commit reaches the disk
            with db:                                      # one transaction, committed on exit
                db.execute(TABLE)
                db.execute(INDEX)
                db.executemany("INSERT INTO checkpoints (run_id, step, mode, saved_at, checkpoint) "
                               "VALUES (?, ?, ?, ?, ?)", rows)
        finally:
            db.close()

    # -- reading: the file only --------------------------------------------------------------
    def _rows(self, sql: str, args: tuple = ()) -> list[tuple]:
        if not self.path.exists():
            return []
        db = sqlite3.connect(self.path, timeout=30)
        try:
            return db.execute(sql, args).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):    # the file exists and has no table yet
                return []
            raise                              # a lock or a disk error is never "no checkpoint"
        finally:
            db.close()

    def last(self, run_id: str) -> dict | None:
        rows = self._rows("SELECT checkpoint FROM checkpoints WHERE run_id = ? ORDER BY id DESC LIMIT 1",
                          (str(run_id),))
        return json.loads(rows[0][0]) if rows else None

    def all(self, run_id: str) -> list[dict]:
        rows = self._rows("SELECT checkpoint FROM checkpoints WHERE run_id = ? ORDER BY id", (str(run_id),))
        return [json.loads(r[0]) for r in rows]

    def runs(self) -> list[str]:
        return [r[0] for r in self._rows("SELECT run_id FROM checkpoints GROUP BY run_id ORDER BY MIN(id)")]