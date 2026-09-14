# Runtime dependencies

These are checked-in distribution snapshots from `vela-sdk` and `vela-contracts`.
They let the hub start from a single checkout without npm registry access or
sibling repositories. `versions.json` records their source, version and SHA-256.

Edit the canonical source in the corresponding sibling repository, then run
`python scripts/sync-runtime-assets.py` from the hub and review the resulting diff.
The host bridge belongs to the hub at `web/src/bridge/host.js`.
