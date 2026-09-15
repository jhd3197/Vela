"""Shared rooms: a finite, ordered conversation between two to four bots.

A room run is deliberately bounded. One user message selects a set of
responders, each answers exactly once, in order, and the run ends. Nothing a bot
writes can schedule another turn — mentions are read from the person's message
only — so a room cannot talk to itself.

What a bot sees is the bounded shared transcript with every speaker named. What
a bot may *do* is still whatever its own profile allows, re-checked at the moment
of each tool call. A peer's reply is information, never authority.

Turn selection and room roles are adapted from CachiBot's
`cachibot/services/room_orchestrator.py` and `api/room_websocket.py`
(MIT, copyright 2025 jhd3197). The execution model here is Vela's own: one
server-owned sequential run over POST + SSE, rather than a WebSocket task group.
"""

import asyncio
import re
import uuid

from .app_storage import AppServiceError
from .assistant import AssistantError, HISTORY_LIMIT

#: Every selected bot answers once. This is the hard ceiling on one run's model
#: turns, independent of what the transcript contains.
MAX_RESPONDERS = 4

#: Wall-clock limit for a whole room run, and for one responder inside it.
RUN_TIMEOUT_SECONDS = 300
RESPONDER_TIMEOUT_SECONDS = 120

#: How many room runs may execute at once across the whole server. One
#: conversation still never has two.
MAX_CONCURRENT_RUNS = 3

#: Peers are quoted into the context, so a room turn reads more history than a
#: direct chat to keep the thread coherent.
ROOM_HISTORY_LIMIT = HISTORY_LIMIT + 8

_MENTION_RE = re.compile(r"(?:^|\s)@([\w][\w.\-']*)", re.UNICODE)

ALL_TOKEN = "all"


def _normalize(name: str) -> str:
    return " ".join(str(name or "").split()).lower()


def parse_mentions(text: str) -> list:
    """The `@tokens` in a person's message, in order, without duplicates."""
    return list(dict.fromkeys(match.group(1) for match in _MENTION_RE.finditer(text or "")))


class Rooms:
    """Respondent selection and run execution for shared rooms."""

    def __init__(self, conversations, bots, assistant):
        self._store = conversations
        self._bots = bots
        self._assistant = assistant
        self._gate = asyncio.Semaphore(MAX_CONCURRENT_RUNS)

    # ---- membership ----------------------------------------------------

    def roster(self, conversation_id: str) -> list:
        """Every member of a room, with its current availability.

        A deleted or archived bot stays in the roster so the room can say what
        is wrong, rather than quietly answering with one member fewer.
        """
        members = []
        for bot_id in self._store.members(conversation_id):
            profile = self._bots.resolve(bot_id)
            if profile is None:
                members.append(
                    {
                        "id": bot_id,
                        "name": "Removed bot",
                        "icon": "sparkle",
                        "color": "slate",
                        "available": False,
                        "reason": "This bot no longer exists.",
                    }
                )
                continue
            available = not profile["deleted"] and not profile["archived"]
            members.append(
                {
                    "id": profile["id"],
                    "name": profile["name"],
                    "description": profile["description"],
                    "icon": profile["icon"],
                    "color": profile["color"],
                    "model": profile["model"],
                    "tools": profile["tools"],
                    "available": available,
                    "reason": ""
                    if available
                    else ("This bot was deleted." if profile["deleted"] else "This bot is archived."),
                }
            )
        return members

    def active_members(self, conversation_id: str) -> list:
        return [member for member in self.roster(conversation_id) if member["available"]]

    def check_ready(self, conversation_id: str) -> list:
        """The members that can answer, refusing a room that cannot hold one."""
        active = self.active_members(conversation_id)
        if len(active) < 2:
            raise AppServiceError(
                409,
                "This room needs at least two available bots. Add or restore a bot before sending.",
            )
        return active

    # ---- respondent selection -----------------------------------------

    def select(self, conversation: dict, text: str, recipients=None) -> list:
        """Who answers this message, in order.

        Explicit selections win over the mode's default. Without any, mention
        mode asks the lead alone and roundtable asks everyone. Ambiguity is
        refused rather than guessed at.
        """
        active = self.check_ready(conversation["id"])
        by_id = {member["id"]: member for member in active}
        order = [member["id"] for member in active]

        chosen: list = []
        if recipients:
            for entry in recipients:
                token = str(entry or "").strip()
                if not token:
                    continue
                if token.lower() == ALL_TOKEN:
                    chosen = list(order)
                    break
                if token not in by_id:
                    # Either not in this room, or no longer available. Both are
                    # a refusal: silently dropping a named recipient would send
                    # the message somewhere the person did not choose.
                    if token in set(self._store.members(conversation["id"])):
                        raise AppServiceError(
                            409, "One of the bots you addressed is not available in this room."
                        )
                    raise AppServiceError(400, "That bot is not a member of this room.")
                chosen.append(token)
        else:
            chosen = self._from_text(text, active, conversation)

        # De-duplicate and put them back into the room's own order, so a run is
        # deterministic regardless of the order the mentions were typed in.
        picked = {bot_id for bot_id in chosen}
        ordered = [bot_id for bot_id in order if bot_id in picked]
        if not ordered:
            raise AppServiceError(400, "No available bot was selected to answer.")
        return ordered[:MAX_RESPONDERS]

    def _from_text(self, text: str, active: list, conversation: dict) -> list:
        """Resolve typed `@name` mentions against this room's membership.

        A token that matches no member is left alone: app mentions keep their
        existing meaning and must not be captured as bot routing.
        """
        tokens = parse_mentions(text)
        if not tokens:
            if conversation.get("mode") == "roundtable":
                return [member["id"] for member in active]
            lead = conversation.get("leadBotId") or ""
            if any(member["id"] == lead for member in active):
                return [lead]
            # The lead is gone; the room still works, so the first available
            # member takes the turn rather than the message going nowhere.
            return [active[0]["id"]]

        chosen: list = []
        matched_any = False
        for token in tokens:
            if token.lower() == ALL_TOKEN:
                matched_any = True
                chosen.extend(member["id"] for member in active)
                continue
            if token in {member["id"] for member in active}:
                matched_any = True
                chosen.append(token)
                continue
            hits = [
                member
                for member in active
                if _normalize(member["name"]) == _normalize(token)
                or _normalize(member["name"]).replace(" ", "") == _normalize(token)
            ]
            if len(hits) > 1:
                raise AppServiceError(
                    409,
                    "More than one bot in this room is called '%s'. Pick one from the list."
                    % token,
                )
            if hits:
                matched_any = True
                chosen.append(hits[0]["id"])
        if not matched_any:
            # Every token was something else — an app, most likely. Fall back to
            # the mode's default rather than refusing a perfectly good message.
            if conversation.get("mode") == "roundtable":
                return [member["id"] for member in active]
            lead = conversation.get("leadBotId") or ""
            return [lead] if any(m["id"] == lead for m in active) else [active[0]["id"]]
        return chosen

    # ---- execution -----------------------------------------------------

    async def run(self, client_id: str, conversation: dict, text: str, emit, *,
                  recipients=None, keep: bool = True, run_id: str = "",
                  only: list | None = None) -> None:
        """Run one room turn: each selected bot answers once, in order.

        `only` restricts the run to specific bots, which is how retrying a
        failed responder avoids re-running the ones that already succeeded.
        """
        self._assistant.check_rate(client_id)
        conversation_id = conversation["id"]
        responders = self.select(conversation, text, recipients)
        if only:
            keep_ids = set(only)
            responders = [bot_id for bot_id in responders if bot_id in keep_ids]
            if not responders:
                raise AppServiceError(400, "None of those bots are answering this message.")

        roster = {member["id"]: member for member in self.roster(conversation_id)}
        emit(
            {
                "runId": run_id,
                "room": {
                    "responders": [
                        {"botId": bot_id, "name": roster[bot_id]["name"], "state": "queued"}
                        for bot_id in responders
                    ]
                },
            }
        )

        async with self._gate:
            try:
                await asyncio.wait_for(
                    self._sequence(conversation, responders, roster, emit, keep, run_id),
                    timeout=RUN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                emit({"error": "This room run took too long and was stopped."})
                raise AssistantError("This room run took too long and was stopped.")

    async def _sequence(self, conversation, responders, roster, emit, keep, run_id) -> None:
        conversation_id = conversation["id"]
        purpose = conversation.get("purpose") or ""
        for bot_id in responders:
            member = roster[bot_id]
            message_id = str(uuid.uuid4())
            # Snapshot per responder, immediately before it speaks, so each bot
            # runs with its own configuration and nothing leaks between them.
            try:
                bot = self._assistant.snapshot(bot_id)
            except AppServiceError as exc:
                emit(
                    {
                        "runId": run_id,
                        "messageId": message_id,
                        "botId": bot_id,
                        "botName": member["name"],
                        "state": "failed",
                        "error": exc.detail,
                    }
                )
                continue

            if keep and run_id:
                self._store.update_run(run_id, current_bot_id=bot_id)
            emit(
                {
                    "runId": run_id,
                    "messageId": message_id,
                    "botId": bot_id,
                    "botName": member["name"],
                    "model": bot.model,
                    "state": "responding",
                }
            )

            # Context is rebuilt for every responder, so a bot later in the order
            # genuinely sees what the ones before it just said.
            history = (
                self._store.room_context(conversation_id, ROOM_HISTORY_LIMIT) if keep else []
            )
            messages = [
                {"role": "system", "content": bot.system_prompt(in_room=True, room_purpose=purpose)},
                *history,
            ]

            partial = ""

            def watch(event, _bot_id=bot_id, _name=member["name"], _message_id=message_id,
                      _model=bot.model):
                nonlocal partial
                if isinstance(event.get("text"), str):
                    partial = event["text"]
                emit({
                    **event,
                    "runId": run_id,
                    "messageId": _message_id,
                    "botId": _bot_id,
                    "botName": _name,
                    "model": _model,
                })

            try:
                await asyncio.wait_for(
                    self._assistant.turn(
                        bot,
                        messages,
                        watch,
                        store_to=conversation_id if keep else None,
                        run_id=run_id,
                        in_room=True,
                        message_id=message_id,
                    ),
                    timeout=RESPONDER_TIMEOUT_SECONDS,
                )
                emit(
                    {
                        "runId": run_id,
                        "messageId": message_id,
                        "botId": bot_id,
                        "botName": member["name"],
                        "state": "complete",
                    }
                )
            except asyncio.CancelledError:
                # A stop cancels this responder and everything still queued.
                if keep and partial:
                    self._store.append(
                        conversation_id, "assistant", partial, tools=[], interrupted=True,
                        bot_id=bot_id, bot_name=member["name"], model=bot.model,
                        run_id=run_id, state="stopped", message_id=message_id,
                    )
                emit(
                    {
                        "runId": run_id,
                        "messageId": message_id,
                        "botId": bot_id,
                        "botName": member["name"],
                        "state": "stopped",
                    }
                )
                raise
            except (AssistantError, AppServiceError, asyncio.TimeoutError) as exc:
                # One bot failing does not end the room's turn: the others still
                # have something to say, and the failure is shown where it
                # happened rather than as a whole-run error.
                detail = (
                    "This bot ran out of time."
                    if isinstance(exc, asyncio.TimeoutError)
                    else str(getattr(exc, "detail", exc))
                )
                if keep and partial:
                    self._store.append(
                        conversation_id, "assistant", partial, tools=[], interrupted=True,
                        bot_id=bot_id, bot_name=member["name"], model=bot.model,
                        run_id=run_id, state="failed", message_id=message_id,
                    )
                emit(
                    {
                        "runId": run_id,
                        "messageId": message_id,
                        "botId": bot_id,
                        "botName": member["name"],
                        "state": "failed",
                        "error": detail,
                    }
                )
