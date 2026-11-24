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
source, that version and `vela.controller.protocol=4`, the same protocol
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
{"mode": "controller", "version": "0.1.19", "protocol": 4,
 "features": {"controller": true, "driver": true, "workspace": true,
              "editor": true, "terminal": true}}
```

`features` lists the controller-only capabilities this Vela was built with.
`driver` marks the driven-run API below; `workspace` and `editor` mark the
workspace files and the plain-text editor described in their own section
below; `terminal` marks the terminal: a shell inside this computer, described
in its own section. All exist only in controller mode, and their routes
answer `404` everywhere else; `terminal` additionally needs a POSIX pty, so
it is advertised only where one exists — the `computer` image always has one.
A full-desktop capture is the remaining planned capability in this mode.

## Driving a desktop step by step

`protocol: 2` added the **driver API**: a way for the controller's own agent
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

The same tools, descriptions and argument schemas Vela's own loop offers its
model (`tool_definitions` over the declared `AgentTools` surface), so the
controller can register them as its own tools and never drift from what Vela
will actually accept. In controller mode that surface includes the five
terminal tools below. Ending a run is **not** a tool: a `task.finish` step
only records a proposal and returns it; the run is ended with the finish
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

## A terminal inside the computer

`protocol: 3` adds the **terminal**: a real shell inside the bot's own
computer. The container is the sandbox — the image ships no compiler and no
toolchains precisely so a bot installs its own into `/workspace` — so a shell
there is the feature, not a breach. On an ordinary Vela the no-shell rule
stands exactly as it always has: agent desktops there have no terminal, the
tools are not listed, and calling them is an `unknown_tool` refusal. Nothing
in this section applies outside controller mode, and a Vela without a POSIX
pty (the mode can in principle run elsewhere) reports no `terminal` feature
and refuses `terminal_open` with a plain sentence.

### The tools

The catalogue (`GET .../driven-runs/tools`) lists them only in controller
mode; a step calls them like any other tool. All take `terminalId` (from
`terminal_open`) and refuse names from another desktop.

- `terminal_open {"title"?, "cwd"?}` — a new terminal: a window on the
  desktop, a shell (`bash` where present, else `sh`) on a pseudo-terminal
  behind it, and an environment rooted at the workspace. `cwd` must stay
  inside the workspace and is created if missing. A desktop holds at most
  **4** terminals; a fifth is refused with a sentence.
- `terminal_run {"terminalId", "command", "timeoutSeconds"?, "waitFor"?}` —
  one command line (one line, at most 4000 bytes). `waitFor` is `"exit"`
  (default), `"output"` or `"none"`. The answer carries `exitCode` (null when
  it has not finished), `running`, `output` and `cursor`. With `"exit"`, Vela
  appends a marker the shell prints with the command's status, so the answer
  carries a real exit code; with `"output"` the call returns the moment
  anything new appears; with `"none"` the line is sent and the call returns
  at once — **the command keeps running**, which is what a dev server wants.
  `timeoutSeconds` is capped at **30**; on timeout the answer says
  `running: true` with what has appeared so far.
- `terminal_read {"terminalId", "sinceCursor"?}` — the output since a cursor
  (0 = everything kept), with the new `cursor` to ask from next and `dropped:
  true` when the bounded scrollback cut output before it.
- `terminal_write {"terminalId", "text"? | "keys"?}` — type text, or send one
  named key: `ctrl-c`, `ctrl-d`, `ctrl-z`, `ctrl-l`, `enter`, `tab`,
  `escape`, `backspace`, `up`, `down`, `left`, `right`. Ctrl-C reaches the
  foreground process group, so it stops what is actually running.
- `terminal_close {"terminalId"}` — kills the shell and everything it
  started, and closes the window.

Output answers are capped at **16 KB** and longer output comes back as head
and tail around an `…[output cut]…` marker, so a huge answer never pretends
to be complete. The scrollback itself is bounded at **256 KB** per terminal;
reads that reach past the cut are told `dropped: true`.

### The environment

The shell's environment is built, never inherited — nothing of Vela's
process environment (the controller token's path, session keys, proxies)
reaches it:

- `cwd`: `/workspace` (or the `cwd` given, inside it)
- `HOME=/workspace/.home`
- `PATH` starting `/workspace/.local/bin`, then the system paths
- `PREFIX=/workspace/.local` and `NPM_CONFIG_PREFIX=/workspace/.local`, so
  `npm install -g` and friends land there
- `PYTHONUSERBASE=/workspace/.local` and `PIP_USER=1`, so `pip install --user`
  lands there too
- `TMPDIR=/workspace/.tmp`

Because `/workspace` is a volume, all of it survives the container sleeping
or being replaced.

### Watching it, and a person at the keyboard

A terminal is a view, watched like any other. `GET
/api/desktops/{id}/views/{viewId}/frame` answers for it with a **text frame**
instead of a picture: the recent screen — the last 60 lines of scrollback
with terminal control sequences stripped — plus a `revision` that rises with
every change and a cursor position. Poll it the way the dashboard polls a
window's frame. (It is text, not an emulator: a full-screen program will not
draw faithfully in it; `terminal_read` is the faithful stream.)

Input from a person goes through the same take-over rules as every other
view: `POST .../takeover` holds the run (`person_driving` refusals answer its
steps), and `POST .../takeover/{leaseId}/input` with `{"kind": "text",
"viewId", "text"}` or `{"kind": "key", "viewId", "key": "ctrl-c"}` types into
the terminal. Releasing the lease never resumes the run by itself, exactly as
elsewhere.

### What lives, what dies

- Closing the terminal's **window** (`DELETE .../views/{viewId}`) kills the
  shell, and closing the terminal kills the window. They are one thing.
- Terminals die with their **desktop**: on delete, on turning the agent off,
  when Vela quiesces for a restore, and when Vela stops.
- **Cancelling a run kills the terminals that run started** — the shell and
  its processes are part of what the cancellation means. A run that merely
  *ended* leaves its terminals alone: the dev server it started is a thing
  the next run may need. Terminals opened by hand (`runId: null`) are never
  killed by a run ending.
- A restart of Vela kills the processes with it (the shell's tty closes);
  terminal windows do not survive a restart, and none are remembered.

```json
POST /api/desktops/{id}/driven-runs/{runId}/steps
{"tool": "terminal_open", "arguments": {"title": "dev"}, "clientStepId": "1"}
→ {"result": {"terminalId": "…", "viewId": "…", "cwd": "/workspace", "shell": "/bin/bash", …}}

{"tool": "terminal_run",
 "arguments": {"terminalId": "…", "command": "npm run dev",
               "timeoutSeconds": 5, "waitFor": "output"}, "clientStepId": "2"}
→ {"result": {"exitCode": null, "running": true, "output": "> app@1.0.0 dev\n> vite dev\n", …}}
```

## Workspace files and a plain-text editor

`protocol: 4` adds the bot's **workspace** as files, and a plain-text
**editor** for one of them. On an ordinary Vela nothing in this section
exists: there is no workspace share, no editor view, the tools are not
listed, and every route answers `404` — the no-paths rule for agents stands
exactly as it always has (see docs/AGENT-DESKTOPS.md).

### The Workspace share

In controller mode the Files app serves a built-in share, `workspace`
(labelled **Workspace**), rooted at the configured workspace directory
(`/workspace` in the image; `VELA_WORKSPACE_DIR` overrides it). It answers
the existing routes:

```
GET /api/files                       → shares, including {"id": "workspace", …}
GET /api/files/workspace?path=…      → the entries at a folder
GET /api/files/workspace/download?…  → the bytes
DELETE /api/files/workspace?path=…   → to the trash, like every delete
```

Everything the Files app enforces applies unchanged: `..`, an absolute path,
a drive letter and a symlink pointing out all refuse with `403 That path is
outside the share.`, deletes go to the same thirty-day trash, uploads stream
under the same cap. The workspace volume is the bot's; Vela adds nothing but
the door.

### The tools

The catalogue (`GET .../driven-runs/tools`) lists them only in controller
mode; a step calls them like any other tool. All paths are workspace-relative.

- `workspace_list {"path"?}` — the entries at a folder (default the
  workspace root), in the Files app's shape: folders first, then by name.
- `workspace_read {"path", "offset"?, "limit"?}` — a UTF-8 text file's
  content with its `revision` (the file's content hash). `offset` and
  `limit` slice in characters; a read answers at most 16 KB of text, and
  longer files come back as head and tail around an `…[output cut]…` marker
  with `truncated: true`, so a long answer never pretends to be complete.
  Files up to 4 MB read this way; bigger ones are reachable through the
  terminal's `head`/`tail`/`sed` the way a shell always has. Bytes that are
  not UTF-8, or that carry NULs, refuse as `unsupported` — the editor and
  the read tools are for text, and saying so beats guessing.
- `workspace_write {"path", "content", "expectedRevision"?}` — replace a
  file atomically (a temporary name beside it, then a rename, so a crash
  mid-save never leaves a half-written file). The save is capped at 1 MB,
  and the file's parent folder must exist. `expectedRevision` is the
  `revision` a `workspace_read` answered: when somebody saved first, the
  save is refused with a `conflict` — *somebody changed this file since it
  was read; look at it again* — never a silent overwrite. Without one the
  write is taken as given, which is what creating a file means.
- `editor_open {"path"}` — a plain-text editor window for the file on this
  desktop, or the one already open brought forward: one file, one window.
  The file is checked whole before the window exists — there, a file, UTF-8
  text, at most 1 MB — and the result carries the `viewId` and the file's
  `revision`.

### The editor window

The editor is a view, watched like any other. `GET
/api/desktops/{id}/views/{viewId}/frame` answers for it with a **text
frame**: the file's current content, its `revision`, and its workspace
`path`. The revision rises with every save, whoever made it — a run's
`workspace_write`, the controller's PUT below, or a person at the keyboard —
so a watcher polling the frame sees the file move. A person edits through
the same take-over rules as every other view: `POST .../takeover` holds the
run (`person_driving` refusals answer its steps), and
`POST .../takeover/{leaseId}/input` with `{"kind": "text", "viewId",
"text"}` saves the file's whole new text — the lease is the guard, so the
one writer is asked no revision. Closing the window (`DELETE
.../views/{viewId}`) closes the editor, and editor windows die with their
desktop: on delete, on turning the agent off, on restore quiesce and on
shutdown. Unlike a terminal an editor keeps no process, so a run ending
leaves its editor windows alone; the files were on disk all along.

### The controller's routes

For the controller itself, outside any run:

```
POST /api/desktops/{id}/editor            {"path": "notes/todo.txt"}
→ 201 {"viewId": "…", "path": "notes/todo.txt", "title": "todo.txt",
       "revision": "…", …}

GET /api/desktops/{id}/editor/content?path=notes/todo.txt
→ 200 {"path": "notes/todo.txt", "content": "…", "revision": "…", "size": 42}

PUT /api/desktops/{id}/editor/content
{"path": "notes/todo.txt", "content": "…", "expectedRevision": "…"}
→ 200 {"path": "notes/todo.txt", "revision": "…", "bytes": 42}
```

The PUT is the same write the tool makes: atomic, capped, and refusing a
stale `expectedRevision` with `409` and a sentence instead of landing on
somebody's edit. All three routes require the controller token and answer
`404` outside controller mode.

```json
{"tool": "workspace_read", "arguments": {"path": "notes/todo.txt"}, "clientStepId": "1"}
→ {"result": {"path": "notes/todo.txt", "content": "- [ ] water the plants\n",
               "revision": "9f1c…", "size": 27, "truncated": false}}

{"tool": "workspace_write",
 "arguments": {"path": "notes/todo.txt", "content": "- [x] water the plants\n",
               "expectedRevision": "9f1c…"}, "clientStepId": "2"}
→ {"result": {"path": "notes/todo.txt", "revision": "72bd…", "bytes": 28}}
```
