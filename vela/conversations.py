"""Durable Ask conversations.

Access scope: Vela is a single-user personal server. Every request carrying the
hub bearer is the same person, so conversations are owned by that person and
there is no per-user partition to invent. An unknown or archived-then-deleted id
is simply not found.

Retention: governed by the existing `chat_history` setting. When it is off no
durable record is written and `purge()` removes what was stored. Stored
transcripts are bounded (`MAX_CONVERSATIONS`, `MAX_MESSAGES`, `MAX_CONTENT`) and
are deliberately separate from the bounded context the model is given.

Kinds: a conversation is either a `direct` chat bound to exactly one bot, or a
`room` with two to four bot members and a turn-taking mode. Everything that
existed before bots is a direct chat with the built-in assistant, which is what
the schema migration writes.
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .app_storage import AppServiceError
from .bots import BUILTIN_BOT_ID, BUILTIN_BOT_NAME

MAX_CONVERSATIONS = 200
MAX_MESSAGES = 400
MAX_CONTENT = 64_000
MAX_TITLE = 120
MAX_DRAFT = 4_000
MAX_PURPOSE = 1_000
DEFAULT_TITLE = "New conversation"
LEGACY_IMPORT_KEY = "legacy_transcript_import"

#: A room is a small group on purpose. Below two there is nobody to talk to;
#: above four a personal machine spends longer generating than the reader spends
#: reading.
MIN_ROOM_BOTS = 2
MAX_ROOM_BOTS = 4

ROOM_MODES = ("mention", "roundtable")

#: Current schema version, tracked with `PRAGMA user_version`. Migrations are
#: transactional and repeatable: running them again on a current database is a
#: no-op, so a downgrade-then-upgrade cannot double-apply them.
SCHEMA_VERSION = 1


def _now() -> str:
    # Microsecond precision: two conversations created in the same second still
    # have a stable, meaningful order in the history list.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _title_from(text: str) -> str:
    line = " ".join(text.split())
    if len(line) <= MAX_TITLE:
        return line or DEFAULT_TITLE
    return line[: MAX_TITLE - 1].rstrip() + "…"


class ConversationStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0,
                    draft TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tools TEXT NOT NULL DEFAULT '[]',
                    interrupted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_by_conversation
                    ON messages (conversation_id, seq);
                CREATE TABLE IF NOT EXISTS chat_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                """
            )
        self.migrate()

    # ---- schema migration ----------------------------------------------

    def migrate(self) -> int:
        """Bring the database to `SCHEMA_VERSION`. Safe to call repeatedly.

        Every step runs inside one transaction and is guarded by what the
        database actually contains, not only by the recorded version, so a
        half-applied upgrade from an interrupted run still converges.
        """
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version >= SCHEMA_VERSION:
                return version
            db.execute("BEGIN IMMEDIATE")
            self._migrate_to_1(db)
            db.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
        return SCHEMA_VERSION

    def _columns(self, db, table: str) -> set:
        return {row["name"] for row in db.execute("PRAGMA table_info(%s)" % table).fetchall()}

    def _migrate_to_1(self, db) -> None:
        """Bots, rooms, message attribution and run records.

        Existing conversations become direct chats with the built-in assistant.
        Their ids, titles, drafts, archive state, messages and interruption
        markers are untouched — only the new columns are filled in.
        """
        conversation_columns = self._columns(db, "conversations")
        for name, ddl in (
            ("kind", "TEXT NOT NULL DEFAULT 'direct'"),
            ("bot_id", "TEXT NOT NULL DEFAULT '%s'" % BUILTIN_BOT_ID),
            ("purpose", "TEXT NOT NULL DEFAULT ''"),
            ("mode", "TEXT NOT NULL DEFAULT 'mention'"),
            ("lead_bot_id", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in conversation_columns:
                db.execute("ALTER TABLE conversations ADD COLUMN %s %s" % (name, ddl))

        message_columns = self._columns(db, "messages")
        for name, ddl in (
            ("sender_kind", "TEXT NOT NULL DEFAULT ''"),
            ("bot_id", "TEXT NOT NULL DEFAULT ''"),
            ("bot_name", "TEXT NOT NULL DEFAULT ''"),
            ("model", "TEXT NOT NULL DEFAULT ''"),
            ("run_id", "TEXT NOT NULL DEFAULT ''"),
            ("state", "TEXT NOT NULL DEFAULT 'complete'"),
        ):
            if name not in message_columns:
                db.execute("ALTER TABLE messages ADD COLUMN %s %s" % (name, ddl))

        # Backfill attribution for transcripts written before bots existed. The
        # model is left empty on purpose: those turns never recorded one, and an
        # invented value would be worse than an honest blank.
        db.execute("UPDATE messages SET sender_kind='user' WHERE sender_kind='' AND role='user'")
        db.execute(
            "UPDATE messages SET sender_kind='bot', bot_id=?, bot_name=?"
            " WHERE sender_kind='' AND role='assistant'",
            (BUILTIN_BOT_ID, BUILTIN_BOT_NAME),
        )
        db.execute(
            "UPDATE messages SET state='interrupted' WHERE interrupted=1 AND state='complete'"
        )

        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_bots (
                conversation_id TEXT NOT NULL,
                bot_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (conversation_id, bot_id)
            );
            CREATE INDEX IF NOT EXISTS conversation_bots_order
                ON conversation_bots (conversation_id, position);
            CREATE TABLE IF NOT EXISTS chat_runs (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                request_key TEXT,
                status TEXT NOT NULL,
                current_bot_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS chat_runs_by_conversation
                ON chat_runs (conversation_id, created_at DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS chat_runs_request
                ON chat_runs (conversation_id, request_key)
                WHERE request_key IS NOT NULL;
            """
        )

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    # ---- conversations -------------------------------------------------

    def create(
        self,
        title: str | None = None,
        *,
        kind: str = "direct",
        bot_id: str = BUILTIN_BOT_ID,
        purpose: str = "",
        mode: str = "mention",
        lead_bot_id: str = "",
        bot_ids: list | None = None,
    ) -> dict:
        """Start a direct chat with one bot, or a room with two to four.

        Callers pass bot ids that they have already resolved against the bot
        store; this store owns ordering and membership, not whether a profile
        is usable.
        """
        if kind not in ("direct", "room"):
            raise AppServiceError(400, "kind must be direct or room")
        members: list = []
        if kind == "room":
            if mode not in ROOM_MODES:
                raise AppServiceError(400, "mode must be mention or roundtable")
            members = list(dict.fromkeys(bot_ids or []))
            if not MIN_ROOM_BOTS <= len(members) <= MAX_ROOM_BOTS:
                raise AppServiceError(
                    400, "a room needs %d to %d bots" % (MIN_ROOM_BOTS, MAX_ROOM_BOTS)
                )
            if not lead_bot_id:
                lead_bot_id = members[0]
            if lead_bot_id not in members:
                raise AppServiceError(400, "the lead has to be one of the room's bots")

        conversation_id = str(uuid.uuid4())
        stamp = _now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at, kind, bot_id,"
                " purpose, mode, lead_bot_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    (title or DEFAULT_TITLE)[:MAX_TITLE],
                    stamp,
                    stamp,
                    kind,
                    "" if kind == "room" else bot_id,
                    purpose[:MAX_PURPOSE],
                    mode if kind == "room" else "mention",
                    lead_bot_id if kind == "room" else "",
                ),
            )
            for position, member in enumerate(members):
                db.execute(
                    "INSERT INTO conversation_bots (conversation_id, bot_id, position)"
                    " VALUES (?, ?, ?)",
                    (conversation_id, member, position),
                )
            self._prune_conversations(db)
        return self.get(conversation_id)

    def members(self, conversation_id: str) -> list:
        """Room membership in response order."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT bot_id FROM conversation_bots WHERE conversation_id=? ORDER BY position",
                (conversation_id,),
            ).fetchall()
        return [row["bot_id"] for row in rows]

    def set_members(self, conversation_id: str, bot_ids: list, lead_bot_id: str = "") -> dict:
        """Replace a room's membership and lead in one transaction."""
        members = list(dict.fromkeys(bot_ids or []))
        if not MIN_ROOM_BOTS <= len(members) <= MAX_ROOM_BOTS:
            raise AppServiceError(
                400, "a room needs %d to %d bots" % (MIN_ROOM_BOTS, MAX_ROOM_BOTS)
            )
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, conversation_id)
            if row["kind"] != "room":
                raise AppServiceError(400, "that conversation is not a room")
            lead = lead_bot_id or row["lead_bot_id"]
            if lead not in members:
                lead = members[0]
            db.execute(
                "DELETE FROM conversation_bots WHERE conversation_id=?", (conversation_id,)
            )
            for position, member in enumerate(members):
                db.execute(
                    "INSERT INTO conversation_bots (conversation_id, bot_id, position)"
                    " VALUES (?, ?, ?)",
                    (conversation_id, member, position),
                )
            db.execute(
                "UPDATE conversations SET lead_bot_id=?, updated_at=? WHERE id=?",
                (lead, _now(), conversation_id),
            )
        return self.get(conversation_id)

    def _row(self, db, conversation_id: str):
        row = db.execute(
            "SELECT * FROM conversations WHERE id=?", (conversation_id,)
        ).fetchone()
        if row is None:
            raise AppServiceError(404, "conversation not found")
        return row

    def _summary(self, db, row) -> dict:
        counts = db.execute(
            "SELECT count(*) AS total FROM messages WHERE conversation_id=?", (row["id"],)
        ).fetchone()
        preview = db.execute(
            "SELECT content FROM messages WHERE conversation_id=? ORDER BY seq DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        summary = {
            "id": row["id"],
            "title": row["title"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "archived": bool(row["archived"]),
            "messageCount": counts["total"],
            "preview": " ".join((preview["content"] if preview else "").split())[:140],
            "kind": row["kind"],
            "botId": row["bot_id"],
        }
        if row["kind"] == "room":
            summary["purpose"] = row["purpose"]
            summary["mode"] = row["mode"]
            summary["leadBotId"] = row["lead_bot_id"]
            summary["botIds"] = [
                member["bot_id"]
                for member in db.execute(
                    "SELECT bot_id FROM conversation_bots WHERE conversation_id=?"
                    " ORDER BY position",
                    (row["id"],),
                ).fetchall()
            ]
        return summary

    def exists(self, conversation_id: str) -> bool:
        with self.connection() as db:
            return (
                db.execute(
                    "SELECT 1 FROM conversations WHERE id=?", (conversation_id,)
                ).fetchone()
                is not None
            )

    def get(self, conversation_id: str) -> dict:
        with self.connection() as db:
            row = self._row(db, conversation_id)
            messages = db.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY seq",
                (conversation_id,),
            ).fetchall()
            return {
                **self._summary(db, row),
                "draft": row["draft"],
                "messages": [
                    {
                        "id": message["id"],
                        "role": message["role"],
                        "content": message["content"],
                        "tools": json.loads(message["tools"]),
                        "interrupted": bool(message["interrupted"]),
                        "createdAt": message["created_at"],
                        "senderKind": message["sender_kind"] or message["role"],
                        "botId": message["bot_id"],
                        "botName": message["bot_name"],
                        # Empty means the model was never recorded, which is the
                        # honest answer for anything written before bots existed.
                        "model": message["model"],
                        "runId": message["run_id"],
                        "state": message["state"],
                    }
                    for message in messages
                ],
            }

    def browse(self, query: str = "", archived: bool = False, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit or 50), MAX_CONVERSATIONS))
        with self.connection() as db:
            if query.strip():
                like = f"%{query.strip().lower()}%"
                rows = db.execute(
                    """
                    SELECT DISTINCT c.* FROM conversations c
                    LEFT JOIN messages m ON m.conversation_id = c.id
                    WHERE c.archived=? AND (lower(c.title) LIKE ? OR lower(m.content) LIKE ?)
                    ORDER BY c.updated_at DESC, c.rowid DESC LIMIT ?
                    """,
                    (1 if archived else 0, like, like, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM conversations WHERE archived=? ORDER BY updated_at DESC, rowid DESC"
                    " LIMIT ?",
                    (1 if archived else 0, limit),
                ).fetchall()
            return [self._summary(db, row) for row in rows]

    def update(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        archived: bool | None = None,
        draft: str | None = None,
    ) -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._row(db, conversation_id)
            if title is not None:
                cleaned = " ".join(title.split())[:MAX_TITLE]
                if not cleaned:
                    raise AppServiceError(400, "title cannot be empty")
                db.execute(
                    "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                    (cleaned, _now(), conversation_id),
                )
            if archived is not None:
                db.execute(
                    "UPDATE conversations SET archived=?, updated_at=? WHERE id=?",
                    (1 if archived else 0, _now(), conversation_id),
                )
            # A draft is a local edit, not activity: it must not reorder history.
            if draft is not None:
                db.execute(
                    "UPDATE conversations SET draft=? WHERE id=?",
                    (draft[:MAX_DRAFT], conversation_id),
                )
        return self.get(conversation_id)

    def delete(self, conversation_id: str) -> None:
        """Permanent deletion. Archiving is a separate, reversible operation."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._row(db, conversation_id)
            db.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
            db.execute("DELETE FROM conversation_bots WHERE conversation_id=?", (conversation_id,))
            db.execute("DELETE FROM chat_runs WHERE conversation_id=?", (conversation_id,))
            db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))

    def purge(self) -> None:
        """Remove every stored conversation, for retention being turned off.

        This covers every table that can hold transcript content, rooms and run
        records included. Bot profiles are settings and are deliberately not
        touched: the user is told that separately.
        """
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM messages")
            db.execute("DELETE FROM conversation_bots")
            db.execute("DELETE FROM chat_runs")
            db.execute("DELETE FROM conversations")

    # ---- messages ------------------------------------------------------

    def append(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        tools: list | None = None,
        interrupted: bool = False,
        bot_id: str = "",
        bot_name: str = "",
        model: str = "",
        run_id: str = "",
        state: str = "",
        message_id: str | None = None,
    ) -> dict:
        """Append one message.

        Attribution is written with the message rather than derived later: a bot
        can be renamed or deleted afterwards, and the transcript still has to say
        who actually answered.
        """
        if role not in ("user", "assistant"):
            raise AppServiceError(400, "role must be user or assistant")
        stamp = _now()
        message_id = message_id or str(uuid.uuid4())
        sender_kind = "user" if role == "user" else "bot"
        if role == "assistant" and not bot_id:
            bot_id, bot_name = BUILTIN_BOT_ID, bot_name or BUILTIN_BOT_NAME
        state = state or ("interrupted" if interrupted else "complete")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, conversation_id)
            seq = (
                db.execute(
                    "SELECT coalesce(max(seq), 0) AS top FROM messages WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()["top"]
                + 1
            )
            db.execute(
                "INSERT INTO messages (id, conversation_id, seq, role, content, tools,"
                " interrupted, created_at, sender_kind, bot_id, bot_name, model, run_id, state)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    conversation_id,
                    seq,
                    role,
                    content[:MAX_CONTENT],
                    json.dumps(tools or []),
                    1 if interrupted else 0,
                    stamp,
                    sender_kind,
                    bot_id,
                    bot_name,
                    model,
                    run_id,
                    state,
                ),
            )
            title = row["title"]
            if role == "user" and title == DEFAULT_TITLE:
                title = _title_from(content)
                db.execute(
                    "UPDATE conversations SET title=? WHERE id=?", (title, conversation_id)
                )
            db.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (stamp, conversation_id)
            )
            self._prune_messages(db, conversation_id)
        return {"id": message_id, "seq": seq, "createdAt": stamp, "title": title}

    def set_message_state(self, message_id: str, state: str, content: str | None = None) -> None:
        """Settle a stored message after the fact, for stop/fail/restart."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if content is None:
                db.execute(
                    "UPDATE messages SET state=?, interrupted=? WHERE id=?",
                    (state, 1 if state in ("interrupted", "stopped", "failed") else 0, message_id),
                )
            else:
                db.execute(
                    "UPDATE messages SET state=?, interrupted=?, content=? WHERE id=?",
                    (
                        state,
                        1 if state in ("interrupted", "stopped", "failed") else 0,
                        content[:MAX_CONTENT],
                        message_id,
                    ),
                )

    def context(self, conversation_id: str, limit: int) -> list[dict]:
        """The bounded slice of the stored transcript given to the model.

        Kept separate from the transcript itself: the stored conversation is
        what the person sees, this is only what the model is told about.
        """
        with self.connection() as db:
            rows = db.execute(
                "SELECT role, content FROM messages WHERE conversation_id=?"
                " ORDER BY seq DESC LIMIT ?",
                (conversation_id, max(0, limit)),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    def room_context(self, conversation_id: str, limit: int) -> list[dict]:
        """The shared room transcript, with every speaker named.

        A bot reads peer replies as attributed conversation data. Naming the
        speaker is what stops one bot's output from looking like the user's
        instruction or like its own earlier turn.
        """
        with self.connection() as db:
            rows = db.execute(
                "SELECT role, content, sender_kind, bot_name FROM messages"
                " WHERE conversation_id=? ORDER BY seq DESC LIMIT ?",
                (conversation_id, max(0, limit)),
            ).fetchall()
        context = []
        for row in reversed(rows):
            if row["sender_kind"] == "bot" and row["bot_name"]:
                context.append(
                    {"role": "assistant", "content": "%s: %s" % (row["bot_name"], row["content"])}
                )
            else:
                context.append({"role": row["role"], "content": row["content"]})
        return context

    # ---- runs ----------------------------------------------------------

    def start_run(self, conversation_id: str, request_key: str | None = None) -> dict:
        """Open a run, refusing a second live one in the same conversation.

        A repeated `request_key` returns the existing run instead of starting
        another, so a network retry of the same send cannot produce two turns.
        """
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._row(db, conversation_id)
            if request_key:
                existing = db.execute(
                    "SELECT * FROM chat_runs WHERE conversation_id=? AND request_key=?",
                    (conversation_id, request_key),
                ).fetchone()
                if existing is not None:
                    return {"id": existing["id"], "status": existing["status"], "duplicate": True}
            live = db.execute(
                "SELECT id FROM chat_runs WHERE conversation_id=? AND status='running'",
                (conversation_id,),
            ).fetchone()
            if live is not None:
                raise AppServiceError(409, "This conversation is already answering. Stop it first.")
            run_id = str(uuid.uuid4())
            stamp = _now()
            db.execute(
                "INSERT INTO chat_runs (id, conversation_id, request_key, status, created_at,"
                " updated_at) VALUES (?, ?, ?, 'running', ?, ?)",
                (run_id, conversation_id, request_key, stamp, stamp),
            )
            self._prune_runs(db, conversation_id)
        return {"id": run_id, "status": "running", "duplicate": False}

    def update_run(self, run_id: str, *, status: str | None = None, current_bot_id: str | None = None) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if status is not None:
                db.execute(
                    "UPDATE chat_runs SET status=?, updated_at=? WHERE id=?",
                    (status, _now(), run_id),
                )
            if current_bot_id is not None:
                db.execute(
                    "UPDATE chat_runs SET current_bot_id=?, updated_at=? WHERE id=?",
                    (current_bot_id, _now(), run_id),
                )

    def active_run(self, conversation_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM chat_runs WHERE conversation_id=? AND status='running'"
                " ORDER BY created_at DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "conversationId": row["conversation_id"],
            "status": row["status"],
            "currentBotId": row["current_bot_id"],
            "createdAt": row["created_at"],
        }

    def interrupt_stale_runs(self) -> int:
        """Mark runs left running by a stopped server as interrupted.

        Called at startup. A process that died mid-answer cannot come back and
        finish it, so the honest state is interrupted, not still running.
        """
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id FROM chat_runs WHERE status='running'").fetchall()
            for row in rows:
                db.execute(
                    "UPDATE chat_runs SET status='interrupted', updated_at=? WHERE id=?",
                    (_now(), row["id"]),
                )
                db.execute(
                    "UPDATE messages SET state='interrupted', interrupted=1"
                    " WHERE run_id=? AND state IN ('queued', 'responding')",
                    (row["id"],),
                )
        return len(rows)

    def _prune_runs(self, db, conversation_id: str) -> None:
        stale = db.execute(
            "SELECT id FROM chat_runs WHERE conversation_id=? ORDER BY created_at DESC"
            " LIMIT -1 OFFSET 50",
            (conversation_id,),
        ).fetchall()
        for row in stale:
            db.execute("DELETE FROM chat_runs WHERE id=?", (row["id"],))

    # ---- one-time legacy import ---------------------------------------

    def legacy_import_done(self) -> bool:
        with self.connection() as db:
            return (
                db.execute(
                    "SELECT 1 FROM chat_meta WHERE key=?", (LEGACY_IMPORT_KEY,)
                ).fetchone()
                is not None
            )

    def import_legacy(self, messages: list[dict]) -> dict:
        """Import a browser-held transcript once. Repeats are no-ops.

        The marker is recorded even for an empty transcript, so a second tab or
        a retry cannot create a duplicate conversation.
        """
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM chat_meta WHERE key=?", (LEGACY_IMPORT_KEY,)
            ).fetchone():
                return {"imported": False, "conversationId": None}
            db.execute(
                "INSERT INTO chat_meta (key, value) VALUES (?, ?)",
                (LEGACY_IMPORT_KEY, _now()),
            )
        usable = [
            message
            for message in messages
            if isinstance(message, dict)
            and message.get("role") in ("user", "assistant")
            and isinstance(message.get("content"), str)
            and message["content"].strip()
        ][-MAX_MESSAGES:]
        if not usable:
            return {"imported": False, "conversationId": None}
        first_user = next((m["content"] for m in usable if m["role"] == "user"), "")
        conversation = self.create(_title_from(first_user) if first_user else "Imported chat")
        for message in usable:
            self.append(
                conversation["id"],
                message["role"],
                message["content"],
                tools=message.get("tools") if isinstance(message.get("tools"), list) else [],
                interrupted=bool(message.get("interrupted")),
            )
        return {"imported": True, "conversationId": conversation["id"]}

    # ---- bounds --------------------------------------------------------

    def _prune_conversations(self, db) -> None:
        stale = db.execute(
            "SELECT id FROM conversations ORDER BY updated_at DESC, rowid DESC LIMIT -1 OFFSET ?",
            (MAX_CONVERSATIONS,),
        ).fetchall()
        for row in stale:
            db.execute("DELETE FROM messages WHERE conversation_id=?", (row["id"],))
            db.execute("DELETE FROM conversation_bots WHERE conversation_id=?", (row["id"],))
            db.execute("DELETE FROM chat_runs WHERE conversation_id=?", (row["id"],))
            db.execute("DELETE FROM conversations WHERE id=?", (row["id"],))

    def _prune_messages(self, db, conversation_id: str) -> None:
        db.execute(
            "DELETE FROM messages WHERE conversation_id=? AND seq <= ("
            " SELECT max(seq) - ? FROM messages WHERE conversation_id=?)",
            (conversation_id, MAX_MESSAGES, conversation_id),
        )
