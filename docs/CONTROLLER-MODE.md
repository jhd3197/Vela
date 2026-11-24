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

## The computer image

The Dockerfile in the Vela repository builds this Vela as the `computer`
target; the regular server image stays the default target, unchanged:

```bash
docker build --target computer -t vela-computer .
```

A controller application (CachiBot) builds the image on the user's machine
on first use — Docker Engine on Linux, Docker Desktop on Windows and macOS —
rather than downloading it. The image builds and runs on **linux/amd64 and
linux/arm64**: Apple Silicon and other ARM machines need no emulation, and
Playwright ships Chromium for both. The only build argument is the optional
`VELA_VERSION`, which labels the image; OCI labels carry the title, the
source, that version and `vela.controller.protocol=2`, the same protocol
`GET /api/controller` reports.

The image carries exactly what one bot's computer needs: the controller-mode
entry point, the pinned browser worker (`playwright-core` from the lockfile,
Chromium and the system libraries it links against) with a Node 22 runtime to
run it. It runs as a fixed unprivileged uid/gid (**10001**) and ships no
compiler, no curl and no language toolchains — a bot installs its own tools
into `/workspace` later, and the image stays lean.

```bash
docker run -d --name bot-computer \
  -p 127.0.0.1:7700:7700 \
  -v /path/to/controller-token:/run/secrets/vela-controller-token:ro \
  -v bot-computer-data:/data \
  -v bot-computer-workspace:/workspace \
  --memory=2g --cpus=2 --pids-limit=512 \
  vela-computer
```

- The port publishes on the host's loopback only, as above; the same
  listener answers inside the container on 7700 (`EXPOSE 7700`).
- The token file mounts **read-only** at the path
  `VELA_CONTROLLER_TOKEN_FILE` names. The image defaults that variable to
  `/run/secrets/vela-controller-token`, so mounting at exactly that path is
  all a run needs. There is no token build argument and no token environment
  variable.
- `/data` (`VELA_DATA_DIR`) and `/workspace` are volumes, owned by the image
  user. Named volumes keep a bot's data and its own tools across image
  rebuilds; the controller replaces the container, not the volumes.
- `--memory`, `--cpus` and `--pids-limit` bound one bot's computer so a busy
  browser cannot crowd the host or its sibling bots. The values above are a
  reasonable start for one bot.
- Agent desktops launch Chromium with its own sandbox on, deliberately — the
  worker never passes `--no-sandbox`. A host that cannot run that sandbox
  gets a runtime refusal it can act on, not an unsandboxed browser.

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
{"mode": "controller", "version": "0.1.19", "protocol": 2,
 "features": {"controller": true, "driver": true}}
```

`features` lists the controller-only capabilities this Vela was built with.
`driver` marks the driven-run API below; it exists only in controller mode,
and the routes answer `404` everywhere else. Later work builds on this mode —
a terminal and a full-desktop capture — and those features will exist only in
controller mode; the `computer` image target above is the image they ship in.

## Driving a desktop step by step

`protocol: 2` adds the **driver API**: a way for the controller's own agent
(CachiBot's, say) to drive an agent desktop **one tool call at a time**, while
Vela keeps enforcing everything it enforces for its own model loop — tool-name
and argument validation, site policy, approvals, budgets, effect receipts,
take-over, retention and the concurrency limits. The controller decides; Vela
executes. A run Vela's own loop would carry out is unchanged by any of this.

All routes are under `/api/desktops/{id}` and require the controller token;
outside controller mode they answer `404`.

### The tool catalogue

```
GET /api/desktops/{id}/driven-runs/tools
```

```json
{"tools": [{"type": "function",
            "function": {"name": "desktop.observe",
                         "description": "Look at a view and get its current controls and text.",
                         "parameters": {"type": "object", "properties": {...}}}}]}
```

The same thirteen tools, descriptions and argument schemas Vela's own loop
offers its model (`tool_definitions` over the declared `AgentTools` surface),
so the controller can register them as its own tools and never drift from what
Vela will actually accept. Ending a run is **not** a tool: a `task.finish`
step only records a proposal and returns it; the run is ended with the finish
route below.

### Starting a run

```
POST /api/desktops/{id}/driven-runs
{"title": "Answer the support mail",
 "instruction": "optional context, for the record",
 "clientRequestId": "optional, idempotency key"}
```

`201` with the run: `{"id", "kind": "driven", "state": "running", "title", ...}`.
The run is recorded and emits events like any other run, but it is never
handed to Vela's model loop and never queued. One desktop carries one active
run; a second create while one is live answers `409`, and if both shared run
slots are busy the create answers `429`. Retrying with the same
`clientRequestId` returns the run the first call created — even after it has
ended — so a retried create never starts a second run.

### Steps

```
POST /api/desktops/{id}/driven-runs/{runId}/steps
{"tool": "desktop.observe", "arguments": {"viewId": "v1"}, "clientStepId": "42"}
```

`200` with exactly one of these answers:

- `{"result": ...}` — the call ran; the result is the tool's own result.
- `{"refusal": {"code", "detail", "retryable"}}` — the call was refused by the
  same checks the model loop answers to: an unknown tool name
  (`unknown_tool`), a site or app the policy denies (`not_allowed`), a stale
  observation (`stale_observation`, retryable), a person at the keyboard
  (`person_driving`, retryable), and the rest of the loop's vocabulary.
- `{"approval": {"requestId", "summary", "expiresAt"}}` — the change needs the
  owner's approval. The run waits; the `summary` is the approval prompt's own
  words and never carries secret values.
- `{"needsPerson": {"reason", "detail", "viewId"}}` — the step asked for a
  person (a sign-in, a challenge). The run is paused; it continues only when
  someone says carry on (see below).

Every answer also carries `"budget"`, the run's counters as that step
answered. Steps are idempotent on `clientStepId`: a retried POST returns the
recorded answer — the same body and the same counters, never a second
execution of the call; the same id with a different call is a `409`. The
run's live counters are on its record: `GET /api/desktops/{id}/tasks/{runId}`.

### Approvals: ask, wait, read back

When a step returns `approval`, show the `summary` through the controller's
own UI and let the owner answer with the ordinary route — the same one a
person clicking in Vela uses:

```
POST /api/desktops/{id}/approvals/{requestId}
{"decision": "approve"}        # or "deny"; optionally scopeFuture
```

Then read the step back — the read is the settling call:

```
GET /api/desktops/{id}/driven-runs/{runId}/steps/{clientStepId}
```

While the owner is deciding this returns the pending `approval`. The first
read after the answer settles the step: `approved` re-executes the exact same
call with the grant behind it and returns its `result` (or, if the asking
click had already touched the page, a result telling the controller to look
again first); `denied` settles as a `not_approved` refusal. Until the step is
settled, new steps are refused with `409` — one call at a time, always. Poll
the read at about a second; approvals expire on their own, and an expired
approval settles the step as an `expired` refusal.

### People

- `needsPerson` pauses the run. It resumes through the ordinary task control
  route: `POST /api/desktops/{id}/tasks/{runId}/control {"action": "resume"}`
  (releasing a take-over lease never resumes on its own). After a resume the
  controller should observe again before acting — the screen may have moved.
- If a person takes over (`POST /api/desktops/{id}/takeover`), steps answer
  with a `person_driving` refusal until control is released and the run
  resumed. Taking over stops a driven run exactly as it stops Vela's own.

### Ending a run

```
POST /api/desktops/{id}/driven-runs/{runId}/finish
{"outcome": "succeeded", "summary": "what happened, in a sentence"}
```

`outcome` is `succeeded` or `failed`. A `succeeded` finish is checked against
the same unanswered-request rule the loop checks: if something was sent to a
website and no answer came back, the run ends `outcome_unknown`, not
succeeded, because claiming success that cannot be established is how a thing
gets sent twice.

```
POST /api/desktops/{id}/driven-runs/{runId}/cancel
```

stops the run where it is. What it did stays done; its grants are revoked and
its queue advances, as with any other run.

### Watching and restarts

`GET /api/desktops/{id}/events` is the activity line: driven runs emit the
same events as loop runs (`task.starting`, `task.running` with `"driver":
true`, `step.started`, `step.finished`, `approval.waiting`, `approval.resolved`,
`task.attention`, `task.paused`, `task.succeeded`, ...) with the same numbered
cursor. Step records themselves live in memory: a restart marks a driven run
`interrupted` like any run that was in flight — the controller learns that
from the run record (`GET /api/desktops/{id}/tasks/{runId}`), never from a
step, and a driven run is never resumed or auto-restarted.
