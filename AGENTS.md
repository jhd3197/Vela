# Working on Vela

Vela is a personal app server: one computer runs the server, and users manage
their apps from its browser dashboard. Keep installation instructions focused
on that experience. Python/Node setup belongs in `docs/DEVELOPMENT.md`.

## Repository boundaries

- `vela/`: Python/FastAPI engine, services, app storage and runtime assets.
- `web/`: React dashboard and the host side of the app bridge.
- `tests/`: hub regression tests and pinned compatibility fixtures.
- `scripts/`: development, packaging and verification tools.
- `docs/`: current public user and developer documentation.
- Independent apps, SDK, contracts, templates and catalog live in sibling
  repositories. See `docs/REPOSITORIES.md`. Do not recreate a root `packages/`,
  `projects/` or product `apps/` directory.

## Changelog — part of finishing a change

Update `CHANGELOG.md` under `Unreleased` whenever a change affects users,
installation, compatibility, public documentation or contributor workflows.
Use `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed` or `Security`; keep each
category once within the section. Describe the resulting behavior and any
upgrade action in plain language. Group related work into one useful entry.

Do not write a transcript of agent activity, implementation increments, test
logs or speculative features into the changelog. Do not duplicate an existing
entry for the same unfinished change. A purely internal refactor may omit an
entry; explain that briefly in the change summary or PR.

Keep entries under `Unreleased` until a maintainer prepares a release. Never
invent release dates, published tags, contributors or successful verification.
A version bump or local build does not mean a release has been published.

## Public documentation and local plans

Put task plans, implementation history and first-push working notes in the
gitignored `plans/` folder. Create it locally when needed; it is intentionally
absent from fresh clones. Maintain the current next steps there when doing
planned work. Do not force-add plans or link public docs to ignored files.
Keep useful user instructions in `docs/`; archive obsolete notes locally.

Preserve the MIT license and existing copyright notices. Keep donation
destinations and QR images consistent with the maintainer's configured values;
do not substitute payment accounts. Credit only actual Vela contributions in
`CONTRIBUTORS.md`; another project's contributors are not automatically Vela's.

## Implementation and verification

Reuse the existing service boundaries, UI components and CSS. Preserve app
identities, data migrations, capability checks, explicit action grants and
the separation between host and app credentials. Runtime SDK/schema snapshots
are adopted explicitly with `scripts/sync-runtime-assets.py`.

Use disposable data for tests; do not modify a user's installed apps or data
to verify a change. Run checks appropriate to the affected behavior:

```bash
python -m unittest discover -s tests
node --test tests/bridge.test.mjs
npm --prefix web run build
```

For distribution changes, also build and smoke-test the server bundle as
documented in `docs/DEVELOPMENT.md`. Browser checks are in `docs/TESTING.md`.
For docs-only work, verify links, referenced assets and Git exclusions; there
is no need to rebuild the application. Report what was actually verified and
any remaining limitation. Keep local build output out of source commits.
