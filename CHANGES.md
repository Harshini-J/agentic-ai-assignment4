# CHANGES.md

Three lines per assignment: what it added, what it removed, what it constrained.
Removals are logged like additions.

## Assignment 1
- **Added:** a bounded tool loop; four ways to triage an issue (a direct call, a workflow, an agent, and a hybrid of the two); a grounded success check that reads the tracker; a results script
- **Removed:** nothing
- **Constrained:** every agent run to three bounds checked in code (steps, tokens, wall clock); success to what the tracker holds for the run's ID

## Assignment 2
- **Added:** a plan written as data before the issue is read; a validator with four checks; an executor that runs only the plan's steps; one checkpoint that may revise the plan; a second pass before the comment; detectors and a diff over traces; a progress file and a compaction
- **Removed:** the model's choice of the next tool, in the planned way
- **Constrained:** every tool call to a step of a validated plan; revisions to steps not yet done; the comment to drafts that pass the second pass

## Assignment 3
- **Added:** five rewritten tool definitions with examples, among them open_pr; a boundary check before every call; errors that name the fix; harness-derived idempotency keys, a check before every write attempt and a read-back after a timeout; the call record; a fail-closed hook
- **Removed:** free-text tool descriptions and free-form filters; protocol detail in anything the model sees
- **Constrained:** every write to be safe to retry, and to a person when its outcome cannot be read back; the comment to the last write, after the PR

## Assignment 4
- **Added:** a checkpointer (five fields, no conversation, saved synchronously in SQLite) and resume; a store with provenance, merge, supersede and eviction; a write policy written before the store; a store read before the first search and a decision written at the end of a completed run; switches for a maintainer's note, a compaction and a pinned note
- **Removed:** state that lived only in the process: a crash lost the plan, the completed calls and the findings, and a new session started with nothing
- **Constrained:** every write to follow a sync checkpoint (C1 before the PR, C2 before the comment); every record to name its source, who stated it and when; the checkpoint to hold no conversation; a maintainer's rule to be followed, and RepoMind's own memory to be a lead to check
