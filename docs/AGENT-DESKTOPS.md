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

## More than one at a time

Vela runs two agent desktops at once on one computer. A third is refused with a
sentence rather than admitted into a machine that then starts swapping, and a
task waiting for one of the two says it is waiting rather than showing
*Starting* for ten minutes.

Each desktop carries one task at a time and keeps its own queue, its own limits
and its own window in front. Nothing one desktop does reaches another: a window
on one is not addressable from the other, and a task on one cannot change which
window the other has selected.

## When a task does not finish cleanly

A task that failed, was interrupted by a restart, or sent something nobody could
confirm leaves a question you have not looked at yet. The queue on that desktop
**holds** and says why, instead of starting the next task on top of it and
turning one problem into a row of them.

**Carry on** starts the queue again, and so does giving the desktop something
new to do — both of those are you looking at it, which is what the hold was
waiting for.

A result that did not succeed also says how far it actually got: the last step
there is a receipt for, read from what happened rather than from how the task
described it.

## Trying again

**Try again** on a finished task queues the same instruction as a **new** task.
It is never the old one carrying on — what that one did, it did, and there is no
state to resume into: the page has moved and anything it sent has been sent. The
record says which task it came from.

A desktop with something unaccounted for will not repeat it. Say you have
checked first.

## When an app changes underneath it

Installing an update, removing an app or upgrading one takes its authority with
it: every permission an agent desktop held against that app is dropped, and any
question waiting about it can no longer be answered. Windows stay open and say
they need reopening — you chose to have them, and closing them to tidy up is not
Vela's decision to make.

## Windows

Every window has a title bar with three controls that do three different things.
**Minimize** puts it away and nothing else — the app keeps running, its session
stays open, and an agent working in it carries on. **Maximize** fills the desk.
**Close** is the only one that ends the window, and the only one that asks about
unsaved work.

Drag a title bar to the left or right edge and Vela shows the half the window
would take; let go and it goes there. The other half keeps whatever it had, or
becomes an empty pane that offers to be filled. A window moved from one side to
the other is moved — it never ends up showing in both.

The divider between two panes can be dragged, moved with the arrow keys, and
sent back to equal halves with Home or a double-click. The **⋯** menu on any
title bar does everything a drag does: move left, move right, swap the panes,
even them up, leave split view.

On a narrow screen a split is kept but drawn one pane at a time, full width,
with the rail switching between them. Widen the window and both panes come back
exactly as they were.

## The way a window moves

Minimizing and restoring an agent's window play a short warp toward its entry in
the rail: 480 milliseconds, and the same motion in both directions.

The full warp — the picture of the window bending as it goes — happens where
Vela has an actual picture of the window to bend, which today means a window the
agent's own browser is rendering, because Vela's server already takes authorized
pictures of those for the viewer. An ordinary app on your own desktop runs in a
frame this page is not allowed to read the pixels of. That is a browser rule
rather than a missing feature, and Vela will not ask for screen recording or
weaken an app's isolation to get around it for an animation.

Those windows travel in person instead: the window itself moves to its rail icon
and shrinks into it, over the same 480 milliseconds. It is a plainer motion,
because bending is something you do to an image and there is no image — but
nothing is read, nothing is captured, and the window is never taken down and
rebuilt to do it, so the app inside keeps running with whatever you had typed
still in it.

If you have asked your system for reduced motion, there is no warp at all —
windows go straight where they are going. Everything else behaves identically;
the motion is decoration over a decision that has already happened, which is
also why minimizing never pauses a task, closes a session or stops an agent.

## What is kept, and for how long

Four different things pile up while a desktop works, and they have four
different lifetimes.

| | How long | In a backup? |
| --- | --- | --- |
| Pictures of a window | 2 minutes | No |
| Records that something happened | 1 day | No |
| Files | 7 days | No |
| Tasks and their activity | While you keep history | Yes |

A picture of a window exists to be looked at now — by you, watching, or by the
animation when a window is put away. Nothing keeps one.

A *record that something happened* is how Vela knows whether a change went
through, so an unanswered one can be checked rather than repeated. It holds a
fingerprint and an outcome, not what was sent; it cannot be used to reconstruct
a conversation you chose not to keep.

Turning off **Keep history** in Settings stops tasks being written down at all,
and removes what was already there — including the activity under each one.

Vela sweeps expired things when it starts and periodically after that. If you
want to watch it happen, **Settings → Health** has an agent files check with a
clean-up button.

## Backups

A backup covers your settings, your desktops and what your apps have saved. It
is not a copy of a browser session: a sign-in this desktop was keeping, a file
staged for a task and a picture of a window are all outside it, because a backup
is something people copy to other disks and hand to other people.

Restoring one **stops every agent desktop first**. Their browsers close, every
permission they held is dropped and every task that was running is reported as
interrupted afterwards. Nothing is resumed: what a task already did, it did, and
the data underneath it has just been replaced by an older version of itself.

## When something is wrong

**Settings → Health** has three checks for this feature:

- **Agent desktops** — whether one could start here at all, and against which
  browser build. If the answer is no, it says what to install.
- **Agent desktop files** — how much room staged files are using, and whether
  the clean-up is working. It has a button that runs it.
- **Agent browsers** — how many are open, and whether any is open for a desktop
  that is no longer an agent's.

A support bundle from **Settings → Health** includes those numbers and nothing
else about this feature. No task wording, no results, no websites you approved,
no sign-in, no file, no picture.

## What is never available

- No path, directory or file picker reaches the agent.
- No shell, no arbitrary script, no raw network request.
- Nothing about your other desktops, your settings, your approvals or your own
  windows is visible to it.
- Vela never reads your clipboard, and never asks to.
