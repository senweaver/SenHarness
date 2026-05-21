# `backend/scripts/` layout

Scripts here are **operational helpers**, not test code. Tests live in
`backend/tests/` and run under pytest in CI; these files are manual
runs or deploy hooks. Mixing them caused confusion in V0, so V1
organises the folder by intent:

| Subdir | Contents | When to run |
|---|---|---|
| `ops/` | Container entry-points, KEK rotation, backup helpers. | Called by docker-compose / K8s / release scripts. Must be safe in production. |
| `dev/` | Seed data, local demo bootstrappers, fixture generators. | Developer convenience. May be destructive; never called automatically. |
| `probes/` | Diagnostic probes against running systems (shield runs, approval dry-fires, sandbox smoke tests). | Run ad-hoc when investigating a specific layer. Mostly read-only. |
| `legacy/` | Milestone verification scripts from V0 (`d1_verify_*.py`, `a1_*`, `b2_c2_*`, etc.). They encoded useful assertions about D1–D21 outputs but predate the pytest suite — kept for historical reference only. | Nothing routinely calls these; port the still-useful assertions into `backend/tests/integration/` as needs arise, then delete from here. |

## Migration schedule

V1 moved the D-series / A-series / B-series / FU-series scripts to
`legacy/` wholesale. During V2 the plan is:

1. Walk `legacy/` top-down. For each script, identify the
   *invariants* it checks (not the one-time "did D14 ship" checks).
2. Port those invariants as pytest integration tests.
3. Delete the legacy file.

**Don't add to `legacy/`.** New verification goes straight to
`backend/tests/integration/`.
