"""Durable Ask conversations.

Access scope: Vela is a single-user personal server. Every request carrying the
hub bearer is the same person, so conversations are owned by that person and
there is no per-user partition to invent. An unknown or archived-then-deleted id
is simply not found.

Retention: governed by the existing `chat_history` setting. When it is off no
durable record is written and `purge()` removes what was stored. Stored
transcripts are bounded (`MAX_CONVERSATIONS`, `MAX_MESSAGES`, `MAX_CONTENT`) and
are deliberately separate from the bounded context the model is given.
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .app_storage import AppServiceError

MAX_CONVERSATIONS = 200
MAX_MESSAGES = 400
MAX_CONTENT = 64_000
MAX_TITLE = 120
MAX_DRAFT = 4_000
DEFAULT_TITLE = "New conversation"
LEGACY_IMPORT_KEY = "legacy_transcript_import"


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

    def create(self, title: str | None = None) -> dict:
        conversation_id = str(uuid.uuid4())
        stamp = _now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (conversation_id, (title or DEFAULT_TITLE)[:MAX_TITLE], stamp, stamp),
            )
            self._prune_conversations(db)
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
        return {
            "id": row["id"],
            "title": row["title"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "archived": bool(row["archived"]),
            "messageCount": counts["total"],
            "preview": " ".join((preview["content"] if preview else "").split())[:140],
        }

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
            db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))

    def purge(self) -> None:
        """Remove every stored conversation, for retention being turned off."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM messages")
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
    ) -> dict:
        if role not in ("user", "assistant"):
            raise AppServiceError(400, "role must be user or assistant")
        stamp = _now()
        message_id = str(uuid.uuid4())
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
                " interrupted, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    conversation_id,
                    seq,
                    role,
                    content[:MAX_CONTENT],
                    json.dumps(tools or []),
                    1 if interrupted else 0,
                    stamp,
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
            db.execute("DELETE FROM conversations WHERE id=?", (row["id"],))

    def _prune_messages(self, db, conversation_id: str) -> None:
        db.execute(
            "DELETE FROM messages WHERE conversation_id=? AND seq <= ("
            " SELECT max(seq) - ? FROM messages WHERE conversation_id=?)",
            (conversation_id, MAX_MESSAGES, conversation_id),
        )
