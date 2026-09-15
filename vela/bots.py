"""Custom bot profiles: saved identity, instructions, model and allowed tools.

A bot is configuration, not an installed app, an account or a running process.
Its instructions personalise behaviour; they never grant a capability. The
authoritative profile always comes from this store — a client may say which bot
to use, never what that bot is allowed to do.

Retention: profiles are settings, not transcripts. They survive `chat_history`
being turned off and are untouched by `ConversationStore.purge()`, which is why
they live in their own tables rather than beside the messages.

Concepts (profile fields, assisted drafting, room roles) are adapted from
CachiBot's `cachibot/models/bot.py` and `services/bot_creation_service.py`,
both MIT-licensed, copyright 2025 jhd3197. No source was copied: Vela's storage,
validation and execution paths are its own.
"""

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone

from .app_storage import AppServiceError

#: The assistant Vela has always shipped. Reserved: it is a virtual profile, not
#: a row, so it can never be edited, archived, deleted or duplicated away.
BUILTIN_BOT_ID = "vela"
BUILTIN_BOT_NAME = "Vela"

#: The only tools a custom bot may ever be given. They are the existing
#: read-only hub tools; nothing here can write, install, or reach app data.
SELECTABLE_TOOLS = ("list_apps", "app_status", "app_logs", "engine_status")

MAX_BOTS = 60
MAX_NAME = 60
MAX_DESCRIPTION = 200
MAX_INSTRUCTIONS = 8_000
MAX_MODEL = 120

#: Icon/colour are presentation only. A closed set keeps the dashboard coherent
#: and stops arbitrary strings reaching the UI.
ICONS = (
    "sparkle", "pen-nib", "compass", "magnifying-glass", "code", "chat-circle",
    "lightbulb", "notebook", "flask", "megaphone", "shield-check", "graph",
)
COLORS = ("indigo", "violet", "teal", "amber", "rose", "slate", "emerald", "sky")

_NAME_RE = re.compile(r"^[^\W_][\w .\-']*$", re.UNICODE)

#: Names that would make a room instruction ambiguous.
RESERVED_NAMES = ("all", "everyone", "here", BUILTIN_BOT_NAME.lower())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def builtin_profile() -> dict:
    """The built-in assistant as a profile, so callers need no special case."""
    return {
        "id": BUILTIN_BOT_ID,
        "name": BUILTIN_BOT_NAME,
        "description": "Answers questions about this hub and the apps on it.",
        "icon": "sparkle",
        "color": "indigo",
        "instructions": "",
        "model": "",
        "tools": list(SELECTABLE_TOOLS),
        "revision": 1,
        "archived": False,
        "deleted": False,
        "builtin": True,
        "createdAt": None,
        "updatedAt": None,
    }


class _Connection:
    """`with store.connection() as db` — one transaction, always closed."""

    def __init__(self, path):
        self.path = path
        self.db = None

    def __enter__(self):
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.__enter__()
        return self.db

    def __exit__(self, *exc):
        try:
            self.db.__exit__(*exc)
        finally:
            self.db.close()
        return False


class BotStore:
    """CRUD and lifecycle for custom bot profiles.

    Shares the conversation database so room membership refers to bots that
    demonstrably exist, but owns its own tables and its own bounds.
    """

    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS bots (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    icon TEXT NOT NULL DEFAULT 'sparkle',
                    color TEXT NOT NULL DEFAULT 'indigo',
                    instructions TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    tools TEXT NOT NULL DEFAULT '[]',
                    revision INTEGER NOT NULL DEFAULT 1,
                    archived INTEGER NOT NULL DEFAULT 0,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS bots_by_updated
                    ON bots (deleted, archived, updated_at DESC);
                """
            )

    def connection(self):
        return _Connection(self.path)

    # ---- validation ----------------------------------------------------

    def _clean(self, fields: dict, *, partial: bool) -> dict:
        out: dict = {}

        if "name" in fields or not partial:
            name = " ".join(str(fields.get("name") or "").split())[:MAX_NAME]
            if not name:
                raise AppServiceError(400, "a bot needs a name")
            if not _NAME_RE.match(name):
                raise AppServiceError(400, "a bot name has to start with a letter or number")
            if name.lower() in RESERVED_NAMES:
                raise AppServiceError(400, "that name is reserved — choose another one")
            out["name"] = name

        if "description" in fields:
            out["description"] = " ".join(str(fields.get("description") or "").split())[
                :MAX_DESCRIPTION
            ]
        if "instructions" in fields:
            out["instructions"] = str(fields.get("instructions") or "").strip()[:MAX_INSTRUCTIONS]
        if "model" in fields:
            out["model"] = str(fields.get("model") or "").strip()[:MAX_MODEL]
        if "icon" in fields:
            icon = str(fields.get("icon") or "sparkle")
            out["icon"] = icon if icon in ICONS else "sparkle"
        if "color" in fields:
            color = str(fields.get("color") or "indigo")
            out["color"] = color if color in COLORS else "indigo"
        if "tools" in fields:
            raw = fields.get("tools") or []
            if not isinstance(raw, (list, tuple)):
                raise AppServiceError(400, "tools must be a list")
            unknown = [t for t in raw if t not in SELECTABLE_TOOLS]
            if unknown:
                raise AppServiceError(400, "unknown tool: " + str(unknown[0]))
            # Stored in the canonical order so a saved profile is comparable.
            out["tools"] = [t for t in SELECTABLE_TOOLS if t in raw]
        if "archived" in fields:
            out["archived"] = bool(fields.get("archived"))

        if not partial:
            out.setdefault("description", "")
            out.setdefault("instructions", "")
            out.setdefault("model", "")
            out.setdefault("icon", "sparkle")
            out.setdefault("color", "indigo")
            # A new bot starts with no tools. Granting one is always deliberate.
            out.setdefault("tools", [])
        return out

    # ---- reads ---------------------------------------------------------

    def _shape(self, row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "icon": row["icon"],
            "color": row["color"],
            "instructions": row["instructions"],
            "model": row["model"],
            "tools": json.loads(row["tools"]),
            "revision": row["revision"],
            "archived": bool(row["archived"]),
            "deleted": bool(row["deleted"]),
            "builtin": False,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def list(self, *, archived: bool | None = False) -> list[dict]:
        """Live profiles. A deleted bot is never listed, only resolved."""
        with self.connection() as db:
            if archived is None:
                rows = db.execute(
                    "SELECT * FROM bots WHERE deleted=0 ORDER BY archived, updated_at DESC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM bots WHERE deleted=0 AND archived=? ORDER BY updated_at DESC",
                    (1 if archived else 0,),
                ).fetchall()
        return [self._shape(row) for row in rows]

    def get(self, bot_id: str) -> dict:
        """Resolve any bot id, including the built-in one. Raises if unknown."""
        if bot_id == BUILTIN_BOT_ID:
            return builtin_profile()
        with self.connection() as db:
            row = db.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
        if row is None:
            raise AppServiceError(404, "bot not found")
        return self._shape(row)

    def resolve(self, bot_id: str) -> dict | None:
        """Like `get`, but returns None instead of raising.

        Used where a missing bot is an expected state — a transcript whose bot
        was deleted still has to render its history.
        """
        try:
            return self.get(bot_id)
        except AppServiceError:
            return None

    def usable(self, bot_id: str) -> dict:
        """The profile to actually run with, refusing anything unavailable."""
        profile = self.get(bot_id)
        if profile["deleted"]:
            raise AppServiceError(409, "That bot was deleted. Start a new chat with another bot.")
        if profile["archived"]:
            raise AppServiceError(409, "That bot is archived. Restore it or choose another bot.")
        return profile

    # ---- writes --------------------------------------------------------

    def create(self, fields: dict) -> dict:
        clean = self._clean(fields, partial=False)
        bot_id = str(uuid.uuid4())
        stamp = _now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            live = db.execute("SELECT count(*) AS n FROM bots WHERE deleted=0").fetchone()["n"]
            if live >= MAX_BOTS:
                raise AppServiceError(409, "This hub keeps at most %d bots." % MAX_BOTS)
            db.execute(
                "INSERT INTO bots (id, name, description, icon, color, instructions, model,"
                " tools, revision, archived, deleted, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, ?, ?)",
                (
                    bot_id, clean["name"], clean["description"], clean["icon"], clean["color"],
                    clean["instructions"], clean["model"], json.dumps(clean["tools"]),
                    stamp, stamp,
                ),
            )
        return self.get(bot_id)

    def update(self, bot_id: str, fields: dict) -> dict:
        if bot_id == BUILTIN_BOT_ID:
            raise AppServiceError(400, "The built-in Vela assistant cannot be edited.")
        clean = self._clean(fields, partial=True)
        if not clean:
            return self.get(bot_id)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
            if row is None or row["deleted"]:
                raise AppServiceError(404, "bot not found")
            columns = {
                "name": "name", "description": "description", "icon": "icon", "color": "color",
                "instructions": "instructions", "model": "model", "archived": "archived",
            }
            sets, values = [], []
            for key, column in columns.items():
                if key in clean:
                    sets.append(column + "=?")
                    values.append(int(clean[key]) if key == "archived" else clean[key])
            if "tools" in clean:
                sets.append("tools=?")
                values.append(json.dumps(clean["tools"]))
            # Every accepted edit advances the revision, so a run that snapshotted
            # an earlier one can say which configuration it actually used.
            sets.append("revision=revision+1")
            sets.append("updated_at=?")
            values.append(_now())
            values.append(bot_id)
            db.execute("UPDATE bots SET " + ", ".join(sets) + " WHERE id=?", values)
        return self.get(bot_id)

    def duplicate(self, bot_id: str) -> dict:
        """Copy identity and instructions only.

        Tool grants, history and credentials are deliberately not copied: a copy
        starts with no permissions, so duplicating can never widen access.
        """
        source = self.get(bot_id)
        base = source["name"][: MAX_NAME - 8]
        existing = {bot["name"] for bot in self.list(archived=None)}
        name = base + " copy"
        counter = 2
        while name in existing:
            name = "%s copy %d" % (base, counter)
            counter += 1
        return self.create(
            {
                "name": name,
                "description": source["description"],
                "icon": source["icon"],
                "color": source["color"],
                "instructions": source["instructions"],
                "model": source["model"],
                "tools": [],
            }
        )

    def delete(self, bot_id: str) -> None:
        """Soft delete.

        The profile row is kept so historical messages keep a real name to show.
        Deletion removes the bot from use, never from the transcript.
        """
        if bot_id == BUILTIN_BOT_ID:
            raise AppServiceError(400, "The built-in Vela assistant cannot be deleted.")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT 1 FROM bots WHERE id=? AND deleted=0", (bot_id,)).fetchone()
            if row is None:
                raise AppServiceError(404, "bot not found")
            db.execute(
                "UPDATE bots SET deleted=1, archived=1, updated_at=? WHERE id=?", (_now(), bot_id)
            )

    # ---- execution-time authorisation ---------------------------------

    def authorized_tools(self, bot_id: str) -> tuple:
        """The tools this bot may call, read fresh at the moment of the call.

        Deliberately not taken from the run's snapshot: revoking a tool has to
        take effect on the next call, not on the next conversation.
        """
        profile = self.resolve(bot_id)
        if profile is None or profile["deleted"] or profile["archived"]:
            return ()
        return tuple(t for t in profile["tools"] if t in SELECTABLE_TOOLS)
