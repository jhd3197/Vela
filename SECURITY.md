# Security

Vela is in pre-release development. Fixes are made on the current development
branch; no stable support window has been announced.

## Report a vulnerability

Please report exploitable issues privately through
[GitHub's private vulnerability reporting](https://github.com/jhd3197/vela/security/advisories/new)
when it is enabled for this repository. If that option is unavailable, use the
[maintainer's website](https://juandenis.com) to request a private reporting
channel before sending exploit details. Do not put credentials or private app
data in a public issue.

Include the affected version, operating system, steps to reproduce, impact and
whether the server was accessed locally or over HTTPS. A response or fix date
is not guaranteed; the maintainer will coordinate disclosure after triage.

## Current boundaries

The default server binds to loopback. Access from another device requires a
password and trusted HTTPS configuration. Browser apps using manifest v2 are
sandboxed and receive scoped operations; installing an app does not grant it
access to other apps' data. Legacy v1 apps and native processes run with the
server user's trust and privileges. They are not operating-system sandboxes.

Connected web apps use a separate boundary: the browser loads an existing HTTPS
service on a different hostname, preserving that service's own authentication and
storage. A script-free wrapper restricts frame navigation to the configured
origin. The service receives no Vela bridge or bearer token. A different hostname
is required because cookies are shared between ports on the same hostname.
The service retains its own network access and security policies; connecting it
does not make it a manifest-v2 sandboxed package. The hub does not proxy arbitrary
URLs or bypass upstream embedding restrictions. Use browser fallback for services
whose authentication or frame policies prevent embedding.

Review app permissions and release sources before installing. See the
[server guide](docs/SERVER.md) and [app contract](docs/CONTRACT.md) for the
implemented access, storage and release boundaries.
