# Repository map

For ecosystem development, clone the related repositories beside `vela` in
a working directory of your choice.
The hub owns the Python engine and React interface. Its root has no `packages/`,
`projects/` or app product directories.

| Repository | Owns | Local validation |
| --- | --- | --- |
| `vela` | Python engine, React hub, host bridge, compatibility tests | `python -m unittest discover -s tests`; `node --test tests/bridge.test.mjs`; `npm --prefix web run build` |
| `vela-contracts` | Manifest v2 JSON Schema; `@vela/contracts` 0.5.0 | `npm pack --dry-run` |
| `vela-sdk` | Public browser bridge client; `@vela/sdk` 0.5.0 | `npm pack --dry-run` |
| `vela-templates` | Canonical starter source and connection recipes | Generate an app with the sibling CLI and import it in Vela |
| `vela-create-app` | App generator; `@vela/create-app` 0.4.0 | `npm pack`; run the packed CLI in a fresh directory |
| `vela-health` | Health app and release builder | `python release.py` |
| `vela-notes` | Notes app and release builder | `python release.py` |
| `vela-meals` | Meals app and release builder | `python release.py`; verify its Notes action in the hub |
| `vela-ollama` | Ollama connection app and release builder | `python release.py`; configure an existing Ollama service in the hub |
| `vela-finance` | Legacy Finance app | `python release.py` produces a source archive; v2 migration still needed |
| `vela-hello` | Legacy Hello Vela example | Same legacy limitation |
| `vela-system-info` | Legacy native System Info example | Same legacy limitation |
| `vela-apps` | Pinned catalog metadata and release archives | Build an app release, update the index, review/import it in the hub |

The GitHub owner is **[jhd3197](https://github.com/jhd3197)**. The hub repository
is [`jhd3197/vela`](https://github.com/jhd3197/vela); sibling repositories use the
same owner and the names above.

## Work locally

A standalone hub starts with an empty Library. Import a v2 app folder/ZIP from
its sibling repository, or select the local release catalog before starting:

```powershell
$env:VELA_CATALOG = "$PWD\..\vela-apps\index.json"
python -m vela
```

For trusted source development, including legacy apps:

```powershell
python scripts/prepare-dev-apps.py
$env:VELA_APPS_DIR = "$PWD\.local\apps"
python -m vela
```

This copies app sources into an ignored development directory. To refresh, use
a new `--destination` and point `VELA_APPS_DIR` there. Installed apps and data
remain under `~/.vela` or the configured `VELA_DATA_DIR`.

The hub vendors only the public SDK script and manifest schema under
`vela/assets/`, with versions and hashes. After reviewing dependency changes,
run `python scripts/sync-runtime-assets.py` and rerun the hub tests. The CLI
similarly checks in a starter snapshot from `vela-templates`, so packing and
running it never silently reads a neighboring checkout.

## Build and verify package artifacts

Run `npm pack --pack-destination ./dist` in each npm repository after creating
its `dist` directory. Then, from `vela`, run:

```powershell
python scripts/test-package-artifacts.py
```

This integration check opens the sibling tarballs, confirms the SDK excludes
the host bridge, and runs the generator outside the repositories in all three
view modes. Ordinary hub tests do not need those tarballs or sibling clones.
