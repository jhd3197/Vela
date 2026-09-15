# Bots and rooms

Ask comes with the built-in Vela assistant, which knows about this hub. You can
also create your own bots — saved instructions, a model, and whatever you choose
to let them read — and put a few of them in a room to work on something
together.

Everything runs on your own machine through the model server Ask already uses.

## The three ideas

| | What it is |
| --- | --- |
| **Bot** | A saved identity: a name, instructions, a model, and what it may read. |
| **Chat** | A conversation with one bot. A bot can have as many chats as you like. |
| **Room** | A shared conversation with two to four bots that take turns. |

A bot is configuration, not a program. It does not run in the background, has no
account, and cannot install or change anything.

## Create a bot

1. Open **Ask** and choose the **Bots** tab in the side panel.
2. Select **New bot**.
3. Give it a name, and either start from one of the Writer / Planner / Reviewer
   examples or write the instructions yourself.
4. Optionally describe what you want in the "Describe what this bot should do"
   box and select **Draft instructions**. Vela asks the local model to write a
   first draft, which you can then edit. This is optional — if the model is
   unavailable, write the instructions yourself and save; that always works.
5. Pick a model, or leave it on the server default.
6. Choose what it may read (see below). New bots can read nothing.
7. Try it out under **Try it**. The preview answers with the instructions above
   and no tools, and nothing it produces is saved.
8. Select **Create bot**.

To chat with it, select it in the Bots tab, or use **New chat** and pick it.

### What a bot can read

A new bot has no access to this hub at all. In the editor you can allow any of
the four read-only things the built-in assistant can already do:

- see the app list
- check one app's status
- read an app's logs
- check the hub's totals and storage

There is nothing else to grant in this release. A bot cannot write to an app,
read app data, run commands, open files, or reach the network. Instructions
never grant access — only these checkboxes do, and Vela re-checks them at the
moment a bot actually tries to use one.

Read-only still means real information. If you allow log reading and then put
that bot in a room, what it reports is visible to everyone else in that room.

### Editing, duplicating, archiving and deleting

- **Editing** affects future answers. A reply already being written finishes
  with the instructions it started with.
- **Duplicating** copies the name, description, icon and instructions. It
  deliberately does not copy what the original was allowed to read — a copy
  starts with nothing, so duplicating can never widen access.
- **Archiving** hides a bot without touching its chats. Restore it any time.
- **Deleting** keeps every chat the bot already answered, and those answers
  still show its name. You just cannot send it anything new. A chat whose bot
  was deleted will not quietly switch to another assistant; start a new chat
  instead.

## Create a room

A room is a shared conversation with two to four of your bots.

1. In Ask, choose the **Rooms** tab, then **New room**. You need at least two of
   your own bots first.
2. Name the room and, if it helps, say what it is for. Every bot in the room is
   told this.
3. Pick two to four bots and choose how they take turns:
   - **Mention or lead** — only the bots you `@mention` answer. With no mention,
     the lead answers alone.
   - **Roundtable** — every bot answers once, in order, and each one sees the
     replies that came before it.
4. Choose the lead, and select **Create room**.

### Talking to a room

Type `@` to mention someone. The picker lists the room's **Bots** first and your
installed **Apps** below, labelled, so the two are never confused. App mentions
keep their existing meaning: they add the app ID to your message and do not make
anything answer.

- `@Writer revise the opening` — only Writer answers.
- `@Writer @Reviewer` — both answer, in the room's order.
- `@all` — every available bot answers.
- No mention — the lead answers, or everyone does in roundtable mode.

The composer says who the message will reach before you send it, and the list
above the answers shows who is writing and who is still waiting.

Each selected bot answers exactly once. If a bot writes `@someone` in its own
reply, nothing happens — only your messages decide who speaks, so a room can
never talk to itself.

### Stopping, failures and retries

**Stop** cancels the whole run, including bots that had not started yet.
Whatever was already written is kept.

If one bot fails, the others carry on and you get a **Retry** button for just
that bot. Retrying reruns only the bot that failed; replies that already
succeeded are left alone.

If a room drops below two available bots — because one was deleted or archived —
it will not run until you fix it. Vela shows an **Edit room** action to change
who is in it.

## Privacy and history

- Bots and rooms are **settings**. They are kept even when chat history is
  turned off in Settings → Chat & privacy.
- Transcripts follow the existing chat history setting. With history off,
  nothing a room or chat produces is written down — no messages, drafts, partial
  answers or previews.
- Turning history off, or purging it, clears room messages, membership records
  and run records along with everything else. Your bot definitions survive; they
  are configuration, not conversation.
- A bot only ever sees the room it is in and the chat it is in. It cannot read
  your other conversations, and one bot cannot read another's private chats.
- Bots are not people or accounts. Vela remains a single-owner hub, and bots get
  no credentials of their own.

## Limits

- Two to four bots per room.
- One answer per selected bot, per message.
- One run at a time per conversation; up to three across the whole server.
- A room run stops after five minutes, and any one bot after two.

These keep a personal computer responsive while a local model is working.

## Existing chats

Every conversation from before bots existed is still there, at the same address,
with its history, drafts and archive state intact. They are now direct chats
with the built-in Vela assistant. Old answers show that Vela wrote them; they do
not claim a model, because that was never recorded at the time.
