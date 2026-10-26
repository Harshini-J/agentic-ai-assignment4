"""Assignment 4, Part D: the memory store.

A store is shared by every run, durable, and keyed by repository and issue. It is a
SQLite file, state/store.db. Memory, not state: a checkpoint belongs to one run and is
exact; a record here outlives its run and may be approximate. state/write_policy.md says
what may enter; this file refuses what the policy forbids, so a caller cannot break it
by accident.

The checker calls these (checks/FORMATS.md, section 12.5):

    Store(path=None)                                  # None: state/store.db
    .write(record) -> id                              # a new record; returns its id
    .supersede(old_id, record) -> id                  # a new record that replaces old_id
    .active(repository, issue=None) -> list[dict]     # the active records, for retrieval
    .records() -> list[dict]                          # every record, active or not

A record is a dict with the fields of section 12.5. The caller gives: kind, text, source,
origin and confirmed (all required), repository (default "ledgerline"), issue (default
null: the repository as a whole), written_at (default: now, in UTC) and stated_by. The
store sets id, active and supersedes.

What the store refuses (ValueError), each from the write policy:
  * a source that is not user, repo, memory or issue:<n>; an origin that is not derived
    or ingested; a written_at that is not ISO 8601; a confirmed that is not true or false
  * a claim read from outside (source user or issue:<n>) that is not origin ingested, or
    RepoMind's own work (source memory) that is not origin derived: a claim from outside
    is never relabelled as the system's own knowledge
  * a record from an issue's text (source issue:<n>) that is confirmed: anyone can write it
  * a record from a person (source user) with no stated_by
  * a semantic record that is not confirmed: what is not confirmed is never "what is true"
  * a supersede of a record that is not active, from a source of lower standing than the
    one it replaces, or older than it: only a newer statement of equal or higher standing
    replaces a record, and the old one stays, inactive, in records()

A write of what an active record already says (same repository, issue, kind, source and
text, ignoring case and spacing) merges: nothing new is written and the existing id is
returned, so a replay cannot write one memory twice.

Eviction (state/write_policy.md, section 6): Store.evict() marks inactive, and keeps in records(),
any active record from RepoMind's own work (source memory) or from an issue's text (source
issue:<n>) that is older than 90 days, and then the oldest of those while more than 2,000 records
are active. A record from a person (source user) or the repository (source repo) is never expired
by age: it stays until a person supersedes it. Nothing is deleted. The way calls evict() before a
run's first read, so the read never returns what has expired; reads themselves never write.

Giving a run an empty store, for Part D's second set of runs: set REPOMIND_STORE in its
environment, read once by Store(path=None).

    REPOMIND_STORE=off        an empty store: reads return nothing, writes are skipped
    REPOMIND_STORE=readonly   the real store, read only: writes are skipped
    (unset)                   the real store

A skipped write returns None: the caller treats None as "nothing was stored". A Store
given an explicit path ignores the variable, so tests and scripts are not affected by it.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from harness import ROOT

DEFAULT = ROOT / "state" / "store.db"
REPOSITORY = "ledgerline"
KINDS = ("episodic", "semantic", "procedural")
ORIGINS = ("derived", "ingested")
SOURCES = ("user", "repo", "memory")      # and issue:<n>
MODE_VARIABLE = "REPOMIND_STORE"
MODES = ("on", "readonly", "off")

# Standing: a record of lower standing never replaces one of higher standing.
# What anyone can write is below RepoMind's own earlier work, which is below the
# repository's files and a person with standing.
STANDING = {"issue": 1, "memory": 2, "repo": 3, "user": 3}

# Eviction: what RepoMind inferred and what anyone wrote fades; what a person with standing or the
# repository states stays until it is superseded. 90 days outlasts the 54-day gap between 4207's
# closing and its reopening; 2,000 is the backstop (write_policy.md, section 6).
EXPIRY_DAYS = 90
MAX_ACTIVE = 2000

_ISSUE_SOURCE = re.compile(r"^issue:[0-9]+$")
_GIVEN = {"repository", "issue", "kind", "text", "source", "written_at", "origin", "confirmed", "stated_by"}
_SET_BY_STORE = {"id", "active", "supersedes", "evicted_at"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    repository TEXT    NOT NULL,
    issue      INTEGER,
    kind       TEXT    NOT NULL,
    text       TEXT    NOT NULL,
    source     TEXT    NOT NULL,
    written_at TEXT    NOT NULL,
    origin     TEXT    NOT NULL,
    confirmed  INTEGER NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    supersedes INTEGER,
    stated_by  TEXT,
    evicted_at TEXT
);
CREATE INDEX IF NOT EXISTS records_by_issue ON records (repository, issue, active);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> datetime:
    """An ISO 8601 time as an aware datetime (a time with no offset is read as UTC)."""
    try:
        when = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"written_at {value!r} is not ISO 8601, such as 2026-10-24T14:05:00Z") from None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def standing(source: str) -> int:
    return STANDING["issue" if source.startswith("issue:") else source]


def expires(source: str) -> bool:
    """RepoMind's own work and an issue's text fade with age; a person's word and the repository do not."""
    return source == "memory" or source.startswith("issue:")


def same_text(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def clean(record: dict) -> dict:
    """The record as the store keeps it, or a ValueError saying which rule it broke."""
    if not isinstance(record, dict):
        raise TypeError("a record is a dict")
    unknown = set(record) - _GIVEN - _SET_BY_STORE
    if unknown:
        raise ValueError(f"a record has no field {sorted(unknown)}; the fields are {sorted(_GIVEN)}")
    missing = [f for f in ("kind", "text", "source", "origin", "confirmed") if f not in record]
    if missing:
        raise ValueError(f"a record needs {missing}: provenance is not optional")

    repository = record.get("repository", REPOSITORY)
    if not isinstance(repository, str) or not repository.strip():
        raise ValueError("repository is a non-empty string")
    issue = record.get("issue")
    if issue is not None and (isinstance(issue, bool) or not isinstance(issue, int)):
        raise ValueError(f"issue is an issue number or null (the repository as a whole), not {issue!r}")
    kind = record["kind"]
    if kind not in KINDS:
        raise ValueError(f"kind is one of {KINDS}, not {kind!r}")
    text = record["text"]
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text is the memory, in words")
    source = record["source"]
    if not isinstance(source, str) or not (source in SOURCES or _ISSUE_SOURCE.match(source)):
        raise ValueError(f"source is user, repo, memory or issue:<n>, not {source!r}")
    origin = record["origin"]
    if origin not in ORIGINS:
        raise ValueError(f"origin is one of {ORIGINS}, not {origin!r}")
    confirmed = record["confirmed"]
    if not isinstance(confirmed, (bool, int)) or confirmed not in (0, 1):
        raise ValueError(f"confirmed is true or false, not {confirmed!r}")
    confirmed = bool(confirmed)
    written_at = record.get("written_at")
    if written_at is None:
        written_at = now()
    parse_time(written_at)
    stated_by = record.get("stated_by")
    if stated_by is not None and (not isinstance(stated_by, str) or not stated_by.strip()):
        raise ValueError("stated_by is the login of the person who wrote it")

    if (source == "user" or source.startswith("issue:")) and origin != "ingested":
        raise ValueError(f"a claim read from outside (source {source}) is origin ingested, never derived: "
                         "a claim from outside is not relabelled as the system's own knowledge")
    if source.startswith("issue:") and confirmed:
        raise ValueError(f"a claim from an issue's text (source {source}) is never confirmed: anyone can "
                         "write it. If a run checks it against the repository, store what it found as a "
                         "record of its own, with source repo or memory")
    if source == "memory" and origin != "derived":
        raise ValueError("source memory is RepoMind's own earlier work, so its origin is derived")
    if source == "user" and not stated_by:
        raise ValueError("a record from a person (source user) says who in stated_by")
    if kind == "semantic" and not confirmed:
        raise ValueError("a record that is not confirmed is never semantic (what is true): "
                         "store it as episodic, what was said, with confirmed false")

    return {"repository": repository.strip(), "issue": issue, "kind": kind, "text": text.strip(),
            "source": source, "written_at": str(written_at).strip(), "origin": origin,
            "confirmed": confirmed, "stated_by": stated_by.strip() if stated_by else None}


def as_record(row: sqlite3.Row) -> dict:
    record = {"id": row["id"], "repository": row["repository"], "issue": row["issue"], "kind": row["kind"],
              "text": row["text"], "source": row["source"], "written_at": row["written_at"],
              "origin": row["origin"], "confirmed": bool(row["confirmed"]), "active": bool(row["active"]),
              "supersedes": row["supersedes"]}
    if row["stated_by"] is not None:
        record["stated_by"] = row["stated_by"]
    if row["evicted_at"] is not None:
        record["evicted_at"] = row["evicted_at"]
    return record


class Store:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or DEFAULT)
        mode = "on" if path is not None else (os.environ.get(MODE_VARIABLE) or "on").strip().lower()
        if mode not in MODES:
            raise ValueError(f"{MODE_VARIABLE} is one of {MODES}, not {mode!r}")
        self.mode = mode
        if self.mode != "off":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as db:
                db.executescript(_SCHEMA)
                if "evicted_at" not in {row["name"] for row in db.execute("PRAGMA table_info(records)")}:
                    db.execute("ALTER TABLE records ADD COLUMN evicted_at TEXT")   # a store made before eviction

    # -- the connection --------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous = FULL")     # a record is on disk when write() returns
        return db

    @contextmanager
    def _transaction(self):
        """One write at a time across runs: the check for what is there and the insert are one step."""
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @staticmethod
    def _insert(db: sqlite3.Connection, r: dict, supersedes: int | None) -> int:
        cursor = db.execute(
            "INSERT INTO records (repository, issue, kind, text, source, written_at, origin, confirmed, "
            "active, supersedes, stated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (r["repository"], r["issue"], r["kind"], r["text"], r["source"], r["written_at"], r["origin"],
             int(r["confirmed"]), supersedes, r["stated_by"]))
        return int(cursor.lastrowid)

    # -- the five calls --------------------------------------------------------------------
    def write(self, record: dict):
        r = clean(record)
        if self.mode != "on":
            return None
        with self._transaction() as db:
            for row in db.execute(
                    "SELECT * FROM records WHERE repository = ? AND issue IS ? AND kind = ? AND source = ? "
                    "AND active = 1", (r["repository"], r["issue"], r["kind"], r["source"])):
                if same_text(row["text"], r["text"]):
                    return row["id"]                    # already said: merge, write nothing new
            return self._insert(db, r, supersedes=None)

    def supersede(self, old_id, record: dict):
        r = clean(record)
        if self.mode != "on":
            return None
        with self._transaction() as db:
            old = db.execute("SELECT * FROM records WHERE id = ?", (old_id,)).fetchone()
            if old is None:
                raise KeyError(f"no record {old_id!r} to supersede")
            if not old["active"]:
                raise ValueError(f"record {old_id!r} is already superseded; replace the active one")
            if r["repository"] != old["repository"]:
                raise ValueError("a record replaces one of the same repository")
            if standing(r["source"]) < standing(old["source"]):
                raise ValueError(f"a source of lower standing ({r['source']}) never replaces a higher one "
                                 f"({old['source']})")
            if parse_time(r["written_at"]) < parse_time(old["written_at"]):
                raise ValueError("only a newer statement replaces an older one: "
                                 f"{r['written_at']} is before {old['written_at']}")
            new_id = self._insert(db, r, supersedes=old["id"])
            db.execute("UPDATE records SET active = 0 WHERE id = ?", (old["id"],))
            return new_id

    def evict(self, at: str | None = None, ttl_days: int = EXPIRY_DAYS, cap: int = MAX_ACTIVE) -> list:
        """Expire what has aged out, then trim to the cap. Returns the ids evicted, oldest first.

        An evicted record is marked inactive, stamped `evicted_at`, and stays in records(): the
        write policy deletes nothing. Only records whose source is memory or issue:<n> are evicted.
        A skipped write (off, or read only) evicts nothing."""
        if self.mode != "on":
            return []
        at = at or now()
        cutoff = parse_time(at).timestamp() - ttl_days * 86400
        with self._transaction() as db:
            rows = [r for r in db.execute("SELECT * FROM records WHERE active = 1 ORDER BY id")]
            fading = sorted((r for r in rows if expires(r["source"])), key=lambda r: parse_time(r["written_at"]))
            gone = [r for r in fading if parse_time(r["written_at"]).timestamp() < cutoff]
            kept = [r for r in fading if r["id"] not in {g["id"] for g in gone}]
            excess = len(rows) - len(gone) - cap
            if excess > 0:
                gone += kept[:excess]
            for r in gone:
                db.execute("UPDATE records SET active = 0, evicted_at = ? WHERE id = ?", (at, r["id"]))
            return [r["id"] for r in gone]

    def active(self, repository: str, issue: int | None = None) -> list[dict]:
        """With no issue, every active record of the repository. With one, the active records for
        that issue and every repository-wide one (issue null)."""
        if self.mode == "off":
            return []
        sql, args = "SELECT * FROM records WHERE repository = ? AND active = 1", [repository]
        if issue is not None:
            sql, args = sql + " AND (issue = ? OR issue IS NULL)", args + [int(issue)]
        with closing(self._connect()) as db:
            return [as_record(row) for row in db.execute(sql + " ORDER BY id", args)]

    def records(self) -> list[dict]:
        if self.mode == "off":
            return []
        with closing(self._connect()) as db:
            return [as_record(row) for row in db.execute("SELECT * FROM records ORDER BY id")]