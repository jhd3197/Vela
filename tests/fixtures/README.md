# Hub compatibility fixtures

`chat-fixture/` is a hub-owned bridge test app. `apps.zip` contains frozen
pre-split app versions for migration, release and action regression tests;
`scripts/fixture_apps.py` extracts them to a temporary directory per process.
`catalog/` pins the Health 1.1.0 release used by browser release tests.

These snapshots intentionally do not follow edits to sibling app repositories.
Canonical app source is in `vela-health`, `vela-meals`, `vela-notes`, `vela-ollama`,
`vela-finance`, `vela-hello` and `vela-system-info`. Update compatibility fixtures
deliberately when adopting a new app contract. A hub checkout needs no sibling
repositories to run its regression tests.
