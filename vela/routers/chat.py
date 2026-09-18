"""Bots, conversations and the streamed answer.

Bot profiles are configuration, not transcripts: they stay available while chat
history is off, which is why none of the bot routes call `_history_enabled`.
"""

import asyncio
import json

from fastapi import APIRouter, Body, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..app_storage import AppServiceError
from ..assistant import AssistantError
from ..bots import BUILTIN_BOT_ID, SELECTABLE_TOOLS, builtin_profile
from ..errors_http import Conflict, InvalidRequest, NotFound


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    conversationId: str | None = None
    # Identifies one send, so a network retry resumes the same run rather than
    # starting a second one.
    requestId: str | None = Field(default=None, max_length=64)
    # Structured bot ids, resolved by the composer. Names are never trusted for
    # routing: a duplicate display name would otherwise misroute a message.
    recipients: list[str] = Field(default_factory=list, max_length=8)
    # Re-run only these bots, for retrying one that failed.
    only: list[str] = Field(default_factory=list, max_length=4)


class BotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    icon: str = Field(default="sparkle", max_length=40)
    color: str = Field(default="indigo", max_length=20)
    instructions: str = Field(default="", max_length=8000)
    model: str = Field(default="", max_length=120)
    tools: list[str] = Field(default_factory=list, max_length=8)


class BotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=200)
    icon: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=20)
    instructions: str | None = Field(default=None, max_length=8000)
    model: str | None = Field(default=None, max_length=120)
    tools: list[str] | None = Field(default=None, max_length=8)
    archived: bool | None = None


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(min_length=1, max_length=600)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instructions: str = Field(default="", max_length=8000)
    model: str = Field(default="", max_length=120)
    name: str = Field(default="Preview", max_length=60)
    tools: list[str] = Field(default_factory=list, max_length=8)
    message: str = Field(min_length=1, max_length=2000)


class NewConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="direct", pattern="^(direct|room)$")
    botId: str = Field(default="vela", max_length=64)
    title: str | None = Field(default=None, max_length=120)
    purpose: str = Field(default="", max_length=1000)
    mode: str = Field(default="mention", pattern="^(mention|roundtable)$")
    leadBotId: str = Field(default="", max_length=64)
    botIds: list[str] = Field(default_factory=list, max_length=4)


class MembersPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    botIds: list[str] = Field(min_length=2, max_length=4)
    leadBotId: str = Field(default="", max_length=64)


class ConversationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=200)
    archived: bool | None = None
    draft: str | None = Field(default=None, max_length=4000)


class LegacyImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[ChatMessage] = Field(default_factory=list, max_length=500)


def router(assistant, bots, conversations, rooms, settings) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["chat"])

    # Conversation id -> the task currently answering it, so a reloaded page can
    # stop a run it no longer holds the stream for.
    _live_runs: dict[str, asyncio.Task] = {}

    def _history_enabled() -> None:
        if not settings.get("chat_history"):
            raise Conflict(
                "Chat history is turned off in Settings, so conversations are not saved.",
                code="chat.history_off",
            )

    def _sse(body, *, conversation_id: str | None = None, settle=None) -> StreamingResponse:
        """Run `body(emit)` in a task and stream what it emits as SSE.

        Losing the stream cancels the task: an answer nobody is reading should
        not keep a local model busy. What was generated up to that point is
        already stored, so a reload shows it as interrupted rather than lost.
        """
        queue: asyncio.Queue = asyncio.Queue()

        def emit(event: dict) -> None:
            queue.put_nowait(event)

        async def run() -> None:
            outcome = "complete"
            try:
                await body(emit)
            except AssistantError as exc:
                outcome = "failed"
                emit({"error": str(exc)})
            except AppServiceError as exc:
                outcome = "failed"
                emit({"error": exc.detail})
            except asyncio.CancelledError:
                outcome = "stopped"
                raise
            except Exception:
                outcome = "failed"
                emit({"error": "The assistant failed unexpectedly. Please retry."})
            finally:
                if settle is not None:
                    settle(outcome)
                if conversation_id:
                    _live_runs.pop(conversation_id, None)
                queue.put_nowait(None)

        task = asyncio.create_task(run())
        if conversation_id:
            _live_runs[conversation_id] = task

        async def stream():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if event is None:
                        break
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                task.cancel()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @api.get("/ai/status")
    async def ai_status() -> dict:
        return await assistant.status()

    @api.get("/bots")
    def list_bots(archived: bool = False) -> dict:
        return {
            "builtin": builtin_profile(),
            "bots": bots.list(archived=archived),
            "tools": list(SELECTABLE_TOOLS),
        }

    @api.post("/bots", status_code=201)
    def create_bot(payload: BotPayload) -> dict:
        return bots.create(payload.model_dump())

    @api.get("/bots/{bot_id}")
    def read_bot(bot_id: str) -> dict:
        return bots.get(bot_id)

    @api.patch("/bots/{bot_id}")
    def patch_bot(bot_id: str, payload: BotPatch) -> dict:
        return bots.update(bot_id, payload.model_dump(exclude_unset=True))

    @api.post("/bots/{bot_id}/duplicate", status_code=201)
    def duplicate_bot(bot_id: str) -> dict:
        return bots.duplicate(bot_id)

    @api.delete("/bots/{bot_id}", status_code=204)
    def delete_bot(bot_id: str) -> Response:
        bots.delete(bot_id)
        return Response(status_code=204)

    @api.post("/bots/draft")
    async def draft_instructions(payload: DraftRequest) -> dict:
        """Optional model-assisted drafting.

        Failure here is never fatal: the editor keeps working and the person
        writes the instructions themselves, which is the path that always works.
        """
        try:
            return {"ok": True, "instructions": await assistant.draft_instructions(payload.purpose)}
        except AssistantError as exc:
            return {"ok": False, "instructions": "", "error": str(exc)}
        except Exception:
            return {
                "ok": False,
                "instructions": "",
                "error": "Could not draft instructions. Write them yourself and save.",
            }

    @api.post("/bots/preview")
    async def preview_bot(payload: PreviewRequest, request: Request) -> StreamingResponse:
        """Answer one message with an unsaved profile.

        Transient by construction: no conversation is created, nothing is
        stored, and the preview is given no tools whatever the editor shows.
        """
        client_id = request.client.host if request.client else "local"
        profile = {
            **builtin_profile(),
            "id": "preview",
            "name": payload.name or "Preview",
            "instructions": payload.instructions,
            "model": payload.model,
            "tools": [],
        }
        return _sse(
            lambda emit: assistant.preview(client_id, profile, payload.message, emit)
        )

    @api.get("/chat/conversations")
    def list_conversations(query: str = "", archived: bool = False, limit: int = 50) -> dict:
        if not settings.get("chat_history"):
            return {"conversations": [], "enabled": False}
        return {
            "conversations": conversations.browse(query=query, archived=archived, limit=limit),
            "enabled": True,
        }

    @api.post("/chat/conversations", status_code=201)
    def create_conversation(payload: NewConversation | None = Body(None)) -> dict:
        _history_enabled()
        payload = payload or NewConversation()
        if payload.kind == "room":
            # Membership is validated against the store before the room exists,
            # so a room can never be created around a bot that is not usable.
            for bot_id in payload.botIds:
                bots.usable(bot_id)
            return conversations.create(
                payload.title,
                kind="room",
                purpose=payload.purpose,
                mode=payload.mode,
                lead_bot_id=payload.leadBotId,
                bot_ids=payload.botIds,
            )
        bots.usable(payload.botId)
        return conversations.create(payload.title, kind="direct", bot_id=payload.botId)

    @api.put("/chat/conversations/{conversation_id}/members")
    def set_members(conversation_id: str, payload: MembersPayload) -> dict:
        _history_enabled()
        for bot_id in payload.botIds:
            bots.usable(bot_id)
        return conversations.set_members(conversation_id, payload.botIds, payload.leadBotId)

    @api.get("/chat/conversations/{conversation_id}/run")
    def read_run(conversation_id: str) -> dict:
        _history_enabled()
        return {"run": conversations.active_run(conversation_id)}

    @api.delete("/chat/conversations/{conversation_id}/run", status_code=200)
    def cancel_run(conversation_id: str) -> dict:
        """Stop whatever this conversation is doing.

        A reloaded page has no stream to abort, so the run is settled here and
        the in-flight task, if this process still owns one, is cancelled too.
        """
        _history_enabled()
        run = conversations.active_run(conversation_id)
        if run is None:
            return {"stopped": False}
        conversations.update_run(run["id"], status="stopped")
        task = _live_runs.pop(conversation_id, None)
        if task is not None and not task.done():
            task.cancel()
        return {"stopped": True, "runId": run["id"]}

    @api.post("/chat/conversations/import")
    def import_conversation(payload: LegacyImport) -> dict:
        _history_enabled()
        return conversations.import_legacy([m.model_dump() for m in payload.messages])

    @api.get("/chat/conversations/{conversation_id}")
    def read_conversation(conversation_id: str) -> dict:
        _history_enabled()
        return conversations.get(conversation_id)

    @api.patch("/chat/conversations/{conversation_id}")
    def patch_conversation(conversation_id: str, payload: ConversationPatch) -> dict:
        _history_enabled()
        return conversations.update(
            conversation_id,
            title=payload.title,
            archived=payload.archived,
            draft=payload.draft,
        )

    @api.delete("/chat/conversations/{conversation_id}", status_code=204)
    def delete_conversation(conversation_id: str) -> Response:
        _history_enabled()
        conversations.delete(conversation_id)
        return Response(status_code=204)

    @api.post("/chat")
    async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
        if (
            not payload.messages
            or payload.messages[-1].role != "user"
            or not payload.messages[-1].content.strip()
        ):
            raise InvalidRequest("messages must end with a user message",
                                 code="chat.no_user_message")
        text = payload.messages[-1].content.strip()
        client_id = request.client.host if request.client else "local"
        keep = bool(settings.get("chat_history"))

        conversation = None
        if payload.conversationId and keep:
            try:
                conversation = conversations.get(payload.conversationId)
            except AppServiceError as exc:
                raise NotFound("conversation not found",
                               code="chat.conversation_unknown") from exc

        is_room = bool(conversation and conversation.get("kind") == "room")

        # One durable run per send. A repeated requestId is the same send
        # arriving twice, and must not produce a second set of answers.
        run_id = ""
        if keep and conversation is not None:
            opened = conversations.start_run(conversation["id"], payload.requestId)
            if opened.get("duplicate"):
                raise Conflict(
                    "That message was already sent. Reload to see the answer.",
                    code="chat.duplicate_send",
                )
            run_id = opened["id"]

        async def body(emit) -> None:
            if is_room:
                # Refuse an unusable room before anything is written down, so a
                # rejected send leaves no orphan message in the transcript.
                rooms.check_ready(conversation["id"])
                # Retrying one failed bot answers the question already stored;
                # it must not append the same question a second time.
                if not payload.only:
                    stored = conversations.append(conversation["id"], "user", text)
                    emit({"conversationId": conversation["id"], "title": stored["title"],
                          "runId": run_id})
                else:
                    emit({"conversationId": conversation["id"], "runId": run_id})
                await rooms.run(
                    client_id, conversation, text, emit,
                    recipients=payload.recipients, keep=True, run_id=run_id,
                    only=payload.only or None,
                )
            else:
                bot_id = (conversation or {}).get("botId") or BUILTIN_BOT_ID
                await assistant.run(
                    client_id, payload.conversationId, text, emit,
                    bot_id=bot_id, run_id=run_id,
                )

        def settle(status: str) -> None:
            if keep and run_id:
                conversations.update_run(run_id, status=status)

        return _sse(body, conversation_id=(conversation or {}).get("id"), settle=settle)

    return api
