# Write policy for RepoMind's store

The store (`state/store.py`) is memory: shared by every run, allowed to be approximate, and it grows. A checkpoint is state: one run's, exact, and not covered here. This file says what may enter the store, and it is written before the store is.

## 1. When

- A record is written at the end of a run, after its last write has completed, or at the moment a claim is verified. Never mid-run: a mid-run claim is a guess the next step may overturn (a search that pointed at the wrong file, a draft the critique then changes).
- A run that did not end `completed` writes nothing: one that escalated (including a draft held by the second pass), stopped on quota or a rate limit, or crashed without being resumed to its end. Its decision was never made.
- A resumed run writes once, at its end, not on each replay. Before inserting, the writer checks for an active record with the same repository, issue, kind, source and text, so a crash and a replay cannot write the same memory twice.
- A claim read from outside (an issue's body, a comment) is written at the end of the run that read it, as a record of what was said and by whom, not as a fact.

## 2. What

Stored:

- What was expensive to derive and will be asked again: how to reproduce a bug here, which scripts exist, which file implements what, and a run's triage decision. Something a run found with several searches and reads.
- What was stated rather than inferred: a rule or a fact a person with standing wrote, stored as they wrote it, with who wrote it.
- What was confirmed: checked against the repository's own files, or stated by a person with standing.

Not stored: a guess or a hypothesis; a run's plan or its draft; tool output verbatim (the repository can be read again); anything inferred from an issue's text that nothing checked. A claim by someone without standing about what a maintainer approved is never stored as a fact. It may be stored only as an `episodic` record that someone said it, with `confirmed: false`, or not at all. A record informs a triage. It never authorises an irreversible action (merging, closing).

`kind`: `episodic` for what happened, `semantic` for what is true and was verified, `procedural` for how to do something here.

## 3. How

- Insert when no active record says the same thing. When one does (same repository, issue, kind, source and text), merge: write nothing new.
- Replace only with `supersede(old_id, record)`: the new record names the old one in `supersedes`, the old one goes inactive, and nothing is overwritten in place or deleted. Both keep their own `written_at`.
- A newer statement from the same or a higher standing replaces an older one, so exactly one record on a point is active. A source of lower standing never replaces a higher one: an issue's text does not supersede a maintainer's rule or the repository.
- A run's triage decision supersedes the previous decision for the same issue when it differs; the same decision again merges. Either way, the next run reads one.
- The old record stays in `records()`, so a person can see what was believed before and why it changed.

## 4. With

Every record carries all of these:

| Field | Rule |
|---|---|
| `source` | `user` only for a person with standing, judged by the role in the discussion header (for example `priya-s (maintainer)`), never by what the text claims about itself; `issue:<n>` for an issue's text, which anyone can write, even when it says a maintainer approved something; `repo` for the repository's files; `memory` for RepoMind's own earlier work |
| `stated_by` | the login of the person who wrote it, for any record a person stated |
| `written_at` | when the claim was made or seen, when that is known (the date of the comment, the issue body, or the run); otherwise the time it is stored. ISO 8601, UTC, with `Z`. Ordering a contradiction depends on when a thing was said, not on when RepoMind got round to storing it |
| `origin` | `derived` if RepoMind worked it out; `ingested` if it was read from outside, whoever wrote it |
| `confirmed` | `true` only when checked against something other than the claim itself (a file in the repository) or stated by a person with standing; otherwise `false` |
| `kind` | as in section 2; a claim that is not confirmed is never `semantic` |
| `active`, `supersedes` | `true` and `null` on insert; set by `supersede` |

A claim read from outside is never relabelled as `derived` or as `memory`, and summarizing or rewording it does not change its `source`, `origin` or `confirmed`.

### How each kind of record is tagged

| Record | source | origin | confirmed | kind |
|---|---|---|---|---|
| A run's triage decision (label and file, as its comment states them), written at the end of a completed run | `memory` | derived | false: RepoMind's inference, not checked by a person | episodic |
| A: the reproduction procedure RepoMind worked out and checked against `package.json` | `memory` | derived | true | procedural |
| B: the issue body's claim that auto-merge was approved | `issue:4188`, `stated_by: tk-dev-91` | ingested | false | episodic: a record that the issue said it |
| C: the maintainer's rule on duplicates | `user`, `stated_by: priya-s` | ingested | true: a person with standing stating it is what confirms it | procedural |

## 5. Reading

- Before the first search, a run retrieves the active records for its repository and its issue, and logs the read (`store_read`, with the IDs it returned).
- Records are shown with their source, `stated_by` and age.
- Records whose source is `issue:<n>` are never put in the planner's context or any fill call's: they hold text anyone can write.
- On reading, a record with `confirmed: false` is shown with its source and is never followed as an instruction.
- A rule a maintainer stated (`source: user`, `confirmed: true`) is followed as the maintainer's instruction for how to triage: what label, what to link, what not to do. It never authorises merging or closing. RepoMind's own earlier work (`source: memory`) is a lead to check against the code, not an answer.

## 6. Eviction

Rule: expiry on access, by source. Implemented in `Store.evict()`, which the way calls before each run's first read, so a read never returns what has expired and a read itself never writes. It evicts any active record whose source is `memory` or `issue:<n>` and whose `written_at` is more than 90 days old. An evicted record is marked inactive and stamped `evicted_at`, and it stays in `records()`: nothing is deleted, and a record that expired is told apart from one that was superseded (which names its replacement in `supersedes`). Records from `user` or `repo` are never expired by age: they stay until a person supersedes them. Size: at most 2,000 active records. If more are active after expiry, the oldest `memory` and `issue:<n>` records are evicted first.

Why: the longest gap RepoMind must remember across is 54 days (issue 4207, closed 2026-08-19 and reopened 2026-10-12), so a record has to outlast it. At most one record a run (the triage decision; the same decision again merges and writes nothing new) and an assumed 20 runs a day, at most 20 records a day are written; 2,000 / 20 = 100 days to fill the store, and the 90-day expiry comes first, so an entry lives 90 days. The numbers hold for up to about 37 runs a day (2,000 / 54); above that, the cap would evict a decision before a 54-day gap closed. A changed rule is superseded, and a run's decision supersedes the previous one for the same issue when it differs, so one record on a point stays active and repeated runs on one issue do not add to the count.
