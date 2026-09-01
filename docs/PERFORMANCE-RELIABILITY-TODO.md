# Performance and reliability worklist

Status: all reviewed P1 and P2 items are complete as of 2026-09-01.

## P1 — completed

- [x] Bound storage cleanup to stale Navipod-owned residue, preserve active users/jobs, and run it outside the request.
- [x] Close per-user and Wrapped SQLite connections after every transaction.
- [x] Move blocking image conversion, database-heavy pages, streaming setup, and DNS validation off the async event loop.
- [x] Prevent concurrent admin-statistics and personalized-mix requests from rebuilding the same cache.
- [x] Replace restart-time full-table identity scans and full-library permission walks with incremental/one-time work.
- [x] Disable expensive allocation tracing and historical loudness scans by default.

## P2 — completed

- [x] Reuse bounded SQLAlchemy and HTTP connection pools and close provider clients on shutdown.
- [x] Run independent search/recommendation providers concurrently while isolating provider failures.
- [x] Use indexed FTS or normalized identity columns instead of loading/scanning the full track catalog.
- [x] Remove the per-track query in smart-mix playlist creation.
- [x] Serve stable cache keys, compressed text assets, admin-only admin CSS, and deferred pinned frontend libraries.
- [x] Remove approximately 24 MiB of unused legacy artwork and icons.
- [x] Pin direct Python dependencies and obtain Deno from a versioned official image.
- [x] Split the updater into a smaller image without media/downloader runtimes.
- [x] Add coverage, dead-code, ShellCheck, formatting, lint, and frontend-size regression gates.

## Verification

- 196 Python tests pass.
- Python and JavaScript lint/format checks pass.
- Coverage remains above the enforced 19% non-regression floor.
- All shell entrypoints pass ShellCheck.
- Primary, internal, and domain Compose files validate.
- Concierge and updater images build successfully with Podman.

## Lower-priority follow-ups

- [ ] Raise coverage gradually, focusing on routers and downloader failure paths.
- [ ] Split the largest JavaScript view/player modules when feature work next touches them.
- [ ] Add authenticated browser performance baselines in a deployed test environment.
