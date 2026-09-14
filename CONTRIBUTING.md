# Contributing to Vela

Contributions to Vela Server, its dashboard, documentation and app ecosystem
are welcome. Start with [developer setup](docs/DEVELOPMENT.md) and the
[repository map](docs/REPOSITORIES.md) to find the right repository.

## Report an issue or suggest a feature

Use [Vela issues](https://github.com/jhd3197/vela/issues) for reproducible bugs
and feature proposals. Include your Vela version, operating system, steps to
reproduce, expected behavior and actual behavior. Remove credentials and
personal app data from logs. For vulnerabilities, use [SECURITY.md](SECURITY.md).

## Make a change

1. Fork the repository and create a focused branch from its default branch.
2. Make the change in the appropriate repository. App behavior belongs with
   that app; shared host behavior belongs here.
3. Add or update meaningful tests for changed behavior. Use temporary Vela
   data directories so verification does not alter your installed apps.
4. Update affected documentation and `CHANGELOG.md` under `Unreleased`.
5. Open a pull request describing the problem, the resulting behavior and the
   checks you ran. Include screenshots for visible dashboard changes.

Core checks, from the hub root:

```bash
python -m unittest discover -s tests
node --test tests/bridge.test.mjs
npm --prefix web run build
```

See [testing](docs/TESTING.md) for browser and server-download checks. Docs-only
changes need link and content checks, not an application rebuild. Do not commit
secrets, local data, build output or the ignored `plans/` directory.

## Changelog and releases

Add concise entries under `Unreleased` using `Added`, `Changed`, `Deprecated`,
`Removed`, `Fixed` or `Security`. Describe what changes for a user or contributor,
not the sequence of implementation steps. Combine related entries and avoid
duplicating notes for the same unfinished feature. Internal changes with no
user or contributor impact may omit an entry with a short explanation in the PR.

Release preparation moves the relevant entries into a version section once
the maintainer has established the release version and date. Local artifacts
and version bumps must not be described as already published releases.

## Credits and license

Vela uses the [MIT license](LICENSE). Preserve existing copyright notices and
identify the origin and license of any third-party code or assets you add.
Accepted contributions can be credited in [CONTRIBUTORS.md](CONTRIBUTORS.md)
with a link to the relevant work. AI-assisted contributions receive the same
review and verification as other changes; their author remains responsible
for the submitted work.

Agents should read [AGENTS.md](AGENTS.md). Claude uses [CLAUDE.md](CLAUDE.md),
which imports the same instructions.
