# SenHarness developer docs

Engineering reference for the SenHarness codebase. Read in this order
when you're new; once you know your way around, jump to whichever page
matches your task.

| When you want to…                                          | Read                                                                                 |
|------------------------------------------------------------|--------------------------------------------------------------------------------------|
| Understand the six Harness layers + request flow           | [architecture.md](architecture.md)                                                   |
| Understand why we built it this way                         | [harness-engineering.md](harness-engineering.md)                                     |
| Run the stack (dev or prod) + sandbox / reliability tuning | [deployment.md](deployment.md) (production) · [quickstart.md](quickstart.md) (10-min local) |
| Add an endpoint / tool / channel / kernel / skill / connector | [adding-features.md](adding-features.md)                                          |
| Follow the layered code conventions + async pitfalls       | [patterns.md](patterns.md)                                                           |
| Write tests (unit + integration)                            | [testing.md](testing.md)                                                             |
| Find a make target or CLI command                           | [commands.md](commands.md)                                                           |
| Dig into the skill subsystem (lifecycle / versions / hub / curator / verifier / lineage) | [skills.md](skills.md)                |
| Dig into runtime + sessions + memory + jobs (judge / curator / evolver / reflection / lineage replay) | [runtime-and-jobs.md](runtime-and-jobs.md) |
| Plug something external (channels / MCP / KB connectors / plugins / protocol gateway) or read governance (approval / notification / evaluation / retention / quota / settings / profiles) | [extensions-and-governance.md](extensions-and-governance.md) |

## What is NOT in `docs/`

* **Milestone summaries / changelogs** — git history is the source of
  truth. We do **not** keep per-milestone summary markdown.
* **User-facing copy** — frontend i18n strings live in
  `frontend/messages/<locale>.json`. Backend error codes are stable
  keys; the frontend maps them to localized copy.
* **Operator runbook for a specific deployment** — that goes in your
  internal wiki / runbook, not here. This folder is the upstream
  reference for the codebase itself.

## Adding a new doc

Don't, unless the topic genuinely doesn't fit any of the existing
thematic files. The cross-cutting rule from `AGENTS.md` — *new work
edits a section inside one of these files; new top-level docs require
human review* — exists because the previous "one doc per feature" style
created 100+ overlapping pages that drifted from code.

When in doubt: edit the relevant H2 section inside the closest existing
file and reference code paths with full `backend/...` / `frontend/...`
links so a reader can jump to the source.
