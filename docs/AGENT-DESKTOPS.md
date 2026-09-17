# Agent desktops

A desktop can be set up so an agent works in it while you use another one. It
gets its own browser on the computer Vela runs on, it can use only what you
allow, and your own windows are not part of it.

This page covers what it may touch: the apps, the websites, the files, and what
happens when something needs you.

## Setting one up

Open the rail's desktop menu and choose **Agent**. A desktop that is still yours
shows a short setup with four questions — which model, which apps, which
websites, and what happens when it wants to change something.

Vela refuses to start a desktop that could not work. A missing model server, or
a model that cannot call tools, is said plainly next to the choice.

## Websites

Approving a website is two decisions, so the setup asks two questions.

**Reading only** is the default. The agent can open the site and read it.
Anything that would change something there — signing in, sending a form,
placing an order — stops and asks you to take over.

**Ask me before it sends anything** opens a second path. When the agent is about
to send something, the request is held before it leaves your computer and you
are shown what it is: the address, the method and the *names* of the fields. You
never see the values, because a prompt gets screenshotted and a password in one
is a password in a screenshot. Saying yes allows that exact request and no other:
change the address, a field name or a byte of what is being sent and it is a
different request that has to be asked about again.

Some requests are never offered as a question. A background beacon, a request in
a shape Vela cannot describe, anything where there is no honest sentence to put
in front of you — those stop and ask for a person instead of being summarized
incorrectly.

Approving a site to read is not approving it to act. Nothing here claims that a
website is harmless because only GET requests are allowed; plenty of sites change
things on a GET. What Vela promises is narrower and true: **it will not cause
what it cannot describe.**

### Signing in

Vela will not attempt a sign-in for you and will not try to get past a challenge
that exists to tell people from programs. When a task reaches one it stops and
says so, and the Agent window offers **Take over**. You sign in yourself, in the
same window, with your own hands; passwords and verification codes never pass
through the model or the task log. Say **Carry on** afterwards and the task looks
at the page again before it does anything.

### Keeping a sign-in

By default a desktop forgets its website sign-ins when its browser closes. Turn
on **Keep website sign-ins on this desktop** if you want them to survive, then
use **Keep them now** in the Agent window's Files panel after signing in.

What is kept belongs to that desktop alone. Two agent desktops looking at the
same site are not signed in as each other, and Vela never reads or imports your
own browser's profile. **Erase** removes it — from the file and from the browser
that is open, not just the one that starts next.

## Files

The file picker is in the dashboard, in your window. An agent never opens one.
It is told which files exist, by name and id, and can attach one of them to a
page that is asking for a file. There is no tool that takes a path, so there is
nothing to point somewhere else.

- **Add a file** stages one for the desktop. Up to 25 MB each.
- Anything the agent downloads from an approved site appears in the same list,
  with where it came from. Up to 100 MB each.
- One task's files are limited to 250 MB in total.

A file is stored under a name Vela generates, in a folder Vela owns. The name a
website suggested is kept as a label and never used as a location — so a
suggested `../../report.csv` is shown as what it was and stored as something
else. Nothing is extracted, nothing is run, and nothing is overwritten.

Files expire after seven days, and you can remove one at any time. Deleting a
desktop removes its files; it does not touch the data your installed apps have
saved.

Staged files and kept sign-ins are **not** in Vela's backups. A backup covers
your settings, your desktops and your apps' data; it is not a copy of a
half-finished transfer or of a browser session.

## When something was sent and nobody knows what happened

If a request goes out and the answer never arrives, Vela says so rather than
guessing. The task ends with **Outcome unknown**, and the Files panel shows what
was sent with an **I checked** button.

Vela will not send it again on its own, and nothing else on that desktop will
send the same thing until you say you have checked. This is deliberate: an order
placed twice is worse than an order nobody is sure about.

One honest limitation. When a connection dies before any reply arrives, the
browser itself may resend the request at a level below anything Vela can
intercept — so the site may have received it more than once even though Vela
allowed it once. That is exactly why the outcome is reported as unknown and why
checking is a person's job.

## What is never available

- No path, directory or file picker reaches the agent.
- No shell, no arbitrary script, no raw network request.
- Nothing about your other desktops, your settings, your approvals or your own
  windows is visible to it.
- Vela never reads your clipboard, and never asks to.
