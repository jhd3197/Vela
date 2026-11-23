# Controller mode

A Vela container can run in **controller mode**: token-only access for a
container that one local controller process owns. A controller application
(for example CachiBot, which runs one Vela container per bot — "the bot's
computer") starts the container, publishes its port on the host's
`127.0.0.1` only, and drives it with a per-container bearer token.

People never open this Vela directly. The controller shows the desktop
through its own UI and its own authentication, so controller mode has no
dashboard to sign in to, no Vela password, no phone access and no device
pairing.

## Running it

The entry point is `python -m vela.computer` (the module `vela.computer`,
modelled on the `vela.container` entry point). It listens on
`0.0.0.0:VELA_PORT` (default **7700**) without proxy headers, and sets the
mode flag `VELA_CONTROLLER_MODE=1` that every service reads from `Config` —
one source of truth for whether this Vela is controller-driven.

The controller token comes from a file, never from an environment variable:

- Mount a file containing the token into the container and set
  `VELA_CONTROLLER_TOKEN_FILE` to its path. An inline `VELA_CONTROLLER_TOKEN`
  environment variable is refused at startup, so the token can never appear
  in `docker inspect` output.
- The token must be at least 32 characters with no whitespace or control
  characters (the trailing line break a shell redirect adds is tolerated).
  A missing or unreadable file, or an invalid token, stops the start with a
  plain sentence.
- The token is read once, kept in memory and never logged.

Publish the port on the host's loopback only, and never expose it further:

```
-p 127.0.0.1:7700:7700
```

The listener speaks plain HTTP because it only ever answers the host's
loopback; the port must stay unpublished beyond it. Anyone who can reach the
port still needs the token for every API except health, so keep the token
file readable only by the controller.

## What answers, and what is refused

With `Authorization: Bearer <controller token>` the controller is
owner-equivalent: the token acts as the hub token for every owner route.

Kept working, as in every mode:

- `GET /api/health` — unauthenticated, status and version only.
- The per-app session routes under `/api/app/` — scoped app and agent
  session tokens, so apps in windows and agent runs keep working.
- App icons (`/api/apps/{id}/icon`) and automation webhooks
  (`/api/automations/hooks/...`), which carry their own per-workflow secret.
- App windows under `/apps/` and the agent-host page: agent desktops render
  apps from them. Every other page path answers a short plain-text
  explanation that this Vela is run by a controller — the dashboard's
  assets and bootstrap are not served.

Refused with `403` and a sentence:

- Password login and session bootstrap (`/api/login`, `/api/session`,
  `/api/logout`). There is no person sign-in, and the local bootstrap token
  a desktop Vela hands its own dashboard is not an owner here — the
  container's own loopback is not the owner.
- App lock (everything under `/api/security`).
- Phone access (`/api/phone-access`).
- Device pairing (everything under `/api/devices`).

Repeated bad tokens are throttled like the password login: five attempts
per peer in ten minutes, then `429`.

Self-update refuses to install: the update capability reports
`controller`, so the update API answers that this copy updates a different
way. Replacing the container is the controller's job — it rebuilds the
image, the same as the `container` kind.

## Discovery

`GET /api/controller` (with the token) tells a controller it is talking to
a Vela in controller mode, and how far the protocol has got:

```json
{"mode": "controller", "version": "0.1.19", "protocol": 1,
 "features": {"controller": true}}
```

`features` lists the controller-only capabilities this Vela was built with;
today it carries the mode itself. Later work builds on this mode — a
`computer` image target, a driver API for the controller, a terminal and a
full-desktop capture — and those features will exist only in controller
mode.
