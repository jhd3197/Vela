# Automations

An automation is a small set of steps Vela runs for you: a trigger, then the
things that should happen. **Automations** in the rail lists the ones you have
saved and opens the visual editor for each.

They run on the Vela server, so they keep working while the browser is closed.
They do not run while Vela is not running — see [While Vela is off](#while-vela-is-off).

## Build one

1. Open **Automations → New automation** and give it a name.
2. Choose how it starts. Every automation has exactly one trigger:
   - **Run manually** — only when you press Run.
   - **On a schedule** — repeating, in a timezone you choose.
   - **On a web request** — when an authenticated request reaches its Vela address.
3. Press the **+** below a step to add the next one, and fill in its settings on
   the right.
4. Changes save as you make them. The header shows *Saving…* and then *Saved*;
   if a save fails it says so and nothing is silently lost.
5. Press **Run** to try it. The panel on the right shows each step in order, what
   it produced and how long it took.
6. Press **Turn on** when you want its trigger to start it by itself.

**Run** is unavailable until the automation can actually run. The panel lists
what is missing: a trigger, a step that is not connected to it, a setting with no
value, or a permission you have not allowed yet.

## The steps Vela offers

| Step | What it does |
| --- | --- |
| Run manually / On a schedule / On a web request | Starts the automation |
| Text | Builds a piece of text from the previous step |
| Value as JSON text | Turns a value into JSON text |
| Only if | Sends the value down one of two branches by comparing one field |
| Bring branches together | Joins values arriving from several branches |
| Wait | Pauses for up to five minutes |
| Note in the run log | Writes the incoming value into this run's log |
| Send a notification | Publishes through your configured notification server |
| Wait for your approval | Pauses until you approve or reject it in Vela |
| *App name*: *action* | Asks an installed app to do one thing it offers |

Use `{{field}}` in a text box to bring in a value from the previous step, or
`{{steps.step_name.field}}` from an earlier one. A field that is not there
renders as nothing rather than failing.

Vela deliberately does not offer steps that run code you type, call arbitrary web
addresses, or talk to a language model. Those are separate capabilities with
their own permission and credential questions, and they are not part of this
feature.

## Letting an automation change an app

A step like **Notes: Create note** appears for every installed app that offers an
action. Before it can run, the editor shows exactly what it would do — which app,
which action, which inputs, and how many steps use it — and you press **Allow**.

- Permission belongs to that one automation, that one action, and the app as it
  is installed right now.
- Editing the step so it sends different inputs, adding another step that calls
  the same action, updating the app, or reinstalling it all end that permission.
  Vela shows the request again and waits for you.
- **Remove** takes the permission away. The next call stops, including a call in
  a run that is already going.

Running the same automation twice deliberately does the action twice — that is
what running it again means. Retrying *within* one run does not: Vela keeps a
receipt for each step of each run, so a repeat of the same step returns the
original result instead of writing again.

## Schedules

A schedule repeats every so many minutes, hours, days or weeks. Daily and weekly
schedules run at a time you set, in a timezone you name.

- The time follows the wall clock. A 09:00 daily run stays at 09:00 across clock
  changes.
- On the day clocks go forward, a time inside the missing hour runs once, at the
  next real moment. On the day clocks go back, a time that happens twice runs
  once, on the first.
- One automation has at most one run going at a time.

## Web requests

Turning on an automation that starts from a web request gives it an address and a
secret. Send a POST to that address with the secret in the
`X-Vela-Automation-Secret` header and a JSON body; Vela replies with a run id and
runs it in the background.

The address works wherever Vela already listens — Vela does not open anything to
the internet for it, and its settings for network access and HTTPS are unchanged.
An identical body sent twice is refused, so include something unique such as a
request id. **New web address** issues a fresh secret and stops the old one; the
secret is shown once.

## Approvals

A **Wait for your approval** step pauses the run. It appears in the run panel with
your own Approve and Reject buttons, and it stays there until you decide, even if
Vela restarts in between.

Approving continues the exact version of the automation the run started on, not
whatever the draft looks like now. It does not widen what the automation may do:
permissions are checked again before anything further happens, and a permission
removed while the run waited stops it.

## Runs

Every run records its outcome, its steps and how long each took.

- **Finished** — every step completed.
- **Failed** — a step failed. The reason is on that step.
- **Cancelled** — you stopped it. Steps that had already finished were **not**
  undone; Vela stopped the ones that had not started.
- **Interrupted** — Vela stopped while it was going. Vela cannot say how it
  ended, so it does not guess. Check the app it writes to before running it again.
- **Took too long** — it passed its ten-minute limit.

By default a run log records *what shape* a step produced — "text, 12
characters" — rather than the value itself, because run logs are kept on disk.
The **Note in the run log** step is the exception: choosing it is how you ask to
see a value. Vela keeps the most recent 200 runs per automation.

## While Vela is off

Automations need the Vela server running. If the computer is asleep or Vela is
closed:

- Scheduled times that pass are **skipped**, not queued up. Vela does not fire a
  burst of them when it starts again; it notes how many were missed in its log.
- A run that was going when Vela stopped is marked **Interrupted**.
- Web requests are simply not received.

## Notifications

The **Send a notification** step publishes through whatever notification server is
set in **Settings**. Vela's notifications use ntfy, which means the title and
message go to that server — your own, or a hosted one. The automations page and
the step both name the destination. Without a configured server, the step fails
with a message saying so rather than pretending to send.

## Copying and moving automations

**Duplicate** and **Export** copy the steps only. A duplicate and an import both
arrive as drafts with no permissions, no schedule and no web address, and you
review anything they ask for before turning them on. An exported file never
contains a secret. An automation that uses a step this Vela does not have — for
example an app that is not installed — cannot be imported.

## Requirements

Automations run in a small engine that ships inside the Vela server download.
Nothing extra to install. If a Vela build is missing it, the automations page says
so plainly instead of failing a run halfway. Contributors building from source run
`python scripts/setup-automation-worker.py` once; see
[the developer guide](DEVELOPMENT.md#automations).
