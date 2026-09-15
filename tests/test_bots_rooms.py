"""Custom bots and shared rooms.

Run with: python -m unittest discover -s tests. Uses disposable data only and a
deterministic fake model stream, so nothing here needs Ollama running.
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_bootstrap = tempfile.TemporaryDirectory(prefix="vela-bots-bootstrap-")
os.environ.setdefault("VELA_DATA_DIR", _bootstrap.name)

from fastapi.testclient import TestClient

from vela.api import create_app
from vela.app_storage import AppServiceError
from vela.assistant import Assistant, AssistantError, BotRun
from vela.bots import BUILTIN_BOT_ID, BotStore
from vela.config import Config
from vela.conversations import SCHEMA_VERSION, ConversationStore
from vela.rooms import Rooms


class _StubSettings:
    def __init__(self, **values):
        self.values = {"chat_history": True, "chat_model": "test-model", **values}

    def get(self, key, default=None):
        return self.values.get(key, default)


def _temp_root(prefix):
    temp = tempfile.TemporaryDirectory(prefix=prefix)
    return temp, Path(temp.name)


class BotStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp, root = _temp_root("vela-bots-store-")
        self.addCleanup(self.temp.cleanup)
        self.bots = BotStore(root / "chat.sqlite")

    def test_a_new_bot_starts_with_no_tools(self):
        bot = self.bots.create({"name": "Writer", "instructions": "Draft copy."})
        self.assertEqual(bot["tools"], [])
        self.assertEqual(bot["revision"], 1)
        self.assertFalse(bot["builtin"])

    def test_only_the_four_read_only_tools_can_be_granted(self):
        with self.assertRaises(AppServiceError) as caught:
            self.bots.create({"name": "Sneaky", "tools": ["run_shell"]})
        self.assertEqual(caught.exception.status, 400)
        allowed = self.bots.create({"name": "Reader", "tools": ["app_logs", "list_apps"]})
        # Stored in the canonical order, not the order they were requested in.
        self.assertEqual(allowed["tools"], ["list_apps", "app_logs"])

    def test_the_builtin_assistant_cannot_be_edited_or_deleted(self):
        builtin = self.bots.get(BUILTIN_BOT_ID)
        self.assertTrue(builtin["builtin"])
        self.assertEqual(len(builtin["tools"]), 4)
        with self.assertRaises(AppServiceError):
            self.bots.update(BUILTIN_BOT_ID, {"name": "Hijacked"})
        with self.assertRaises(AppServiceError):
            self.bots.delete(BUILTIN_BOT_ID)

    def test_reserved_names_are_refused(self):
        for name in ("all", "All", "everyone", "Vela"):
            with self.assertRaises(AppServiceError, msg=name):
                self.bots.create({"name": name})

    def test_duplicating_copies_instructions_but_never_tool_grants(self):
        source = self.bots.create(
            {"name": "Reviewer", "instructions": "Be blunt.", "tools": ["list_apps"]}
        )
        copy = self.bots.duplicate(source["id"])
        self.assertEqual(copy["instructions"], "Be blunt.")
        self.assertEqual(copy["tools"], [])
        self.assertNotEqual(copy["id"], source["id"])
        self.assertEqual(copy["name"], "Reviewer copy")
        # A second copy has to get its own name rather than collide.
        self.assertEqual(self.bots.duplicate(source["id"])["name"], "Reviewer copy 2")

    def test_editing_advances_the_revision(self):
        bot = self.bots.create({"name": "Planner"})
        updated = self.bots.update(bot["id"], {"instructions": "Plan carefully."})
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["instructions"], "Plan carefully.")

    def test_deleting_keeps_the_profile_resolvable_for_old_messages(self):
        bot = self.bots.create({"name": "Gone"})
        self.bots.delete(bot["id"])
        self.assertEqual(self.bots.list(), [])
        # The name survives, so a transcript can still say who answered.
        self.assertEqual(self.bots.resolve(bot["id"])["name"], "Gone")
        self.assertTrue(self.bots.resolve(bot["id"])["deleted"])
        with self.assertRaises(AppServiceError):
            self.bots.usable(bot["id"])

    def test_authorized_tools_reads_the_live_grant_not_a_snapshot(self):
        bot = self.bots.create({"name": "Watcher", "tools": ["app_logs"]})
        self.assertEqual(self.bots.authorized_tools(bot["id"]), ("app_logs",))
        self.bots.update(bot["id"], {"tools": []})
        self.assertEqual(self.bots.authorized_tools(bot["id"]), ())
        # A deleted bot is authorised for nothing at all.
        self.bots.update(bot["id"], {"tools": ["app_logs"]})
        self.bots.delete(bot["id"])
        self.assertEqual(self.bots.authorized_tools(bot["id"]), ())


class MigrationTests(unittest.TestCase):
    """An database written before bots existed has to survive untouched."""

    def _legacy_database(self, path):
        db = sqlite3.connect(path)
        db.executescript(
            """
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
                draft TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE messages (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, seq INTEGER NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL, tools TEXT NOT NULL DEFAULT '[]',
                interrupted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            );
            CREATE TABLE chat_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO conversations VALUES
                ('conv-1', 'Old chat', '2026-01-01', '2026-01-02', 1, 'half typed');
            INSERT INTO messages VALUES
                ('m-1', 'conv-1', 1, 'user', 'Why did it stop?', '[]', 0, '2026-01-01'),
                ('m-2', 'conv-1', 2, 'assistant', 'It ran out of disk.', '[]', 0, '2026-01-01'),
                ('m-3', 'conv-1', 3, 'assistant', 'Partly wri', '[]', 1, '2026-01-01');
            """
        )
        db.commit()
        db.close()

    def setUp(self):
        self.temp, self.root = _temp_root("vela-bots-migrate-")
        self.addCleanup(self.temp.cleanup)
        self.path = self.root / "chat.sqlite"
        self._legacy_database(self.path)

    def test_an_old_database_migrates_without_losing_anything(self):
        store = ConversationStore(self.path)
        conversation = store.get("conv-1")
        self.assertEqual(conversation["id"], "conv-1")
        self.assertEqual(conversation["title"], "Old chat")
        self.assertEqual(conversation["draft"], "half typed")
        self.assertTrue(conversation["archived"])
        self.assertEqual(len(conversation["messages"]), 3)
        # It becomes a direct chat with the built-in assistant.
        self.assertEqual(conversation["kind"], "direct")
        self.assertEqual(conversation["botId"], BUILTIN_BOT_ID)

    def test_old_answers_are_attributed_without_inventing_a_model(self):
        store = ConversationStore(self.path)
        messages = store.get("conv-1")["messages"]
        self.assertEqual(messages[0]["senderKind"], "user")
        self.assertEqual(messages[1]["senderKind"], "bot")
        self.assertEqual(messages[1]["botId"], BUILTIN_BOT_ID)
        self.assertEqual(messages[1]["botName"], "Vela")
        # No model was ever recorded for these turns, so none is shown.
        self.assertEqual(messages[1]["model"], "")
        # An interrupted answer keeps saying so under the new state column.
        self.assertEqual(messages[2]["state"], "interrupted")
        self.assertTrue(messages[2]["interrupted"])

    def test_migrating_twice_changes_nothing(self):
        first = ConversationStore(self.path)
        before = first.get("conv-1")
        self.assertEqual(first.migrate(), SCHEMA_VERSION)
        # A second store over the same file re-runs the constructor's migration.
        second = ConversationStore(self.path)
        self.assertEqual(second.migrate(), SCHEMA_VERSION)
        self.assertEqual(second.get("conv-1"), before)
        self.assertEqual(len(second.get("conv-1")["messages"]), 3)


class _FakeModel:
    """A deterministic stand-in for the streaming model.

    Answers are looked up by the bot's name, so a test can prove which profile
    actually ran without depending on any real model.
    """

    def __init__(self, answers=None, fail_for=(), tool_calls=None):
        self.answers = answers or {}
        self.fail_for = set(fail_for)
        self.tool_calls = tool_calls or {}
        self.calls = []

    def install(self, assistant):
        async def turn(bot, available, messages, emit):
            self.calls.append(
                {
                    "bot": bot.name,
                    "botId": bot.id,
                    "model": bot.model,
                    "system": messages[0]["content"] if messages else "",
                    "messages": [dict(m) for m in messages],
                    "tools": [t.name for t in available],
                }
            )
            if bot.name in self.fail_for:
                raise AssistantError("The model could not run for " + bot.name + ".")
            pending = self.tool_calls.get(bot.name)
            if pending and len([c for c in self.calls if c["bot"] == bot.name]) == 1:
                return {"role": "assistant", "content": "", "tool_calls": pending}
            answer = self.answers.get(bot.name, "Answer from " + bot.name)
            emit({"text": answer})
            return {"role": "assistant", "content": answer}

        assistant._model_turn = turn
        return assistant


class _Harness:
    """Store, bot store, assistant and room service over one disposable file."""

    def __init__(self, case, **settings):
        temp, root = _temp_root("vela-bots-run-")
        case.addCleanup(temp.cleanup)
        self.root = root
        self.config = Config(root / "data", root / "apps", ROOT / "web/dist")
        self.config.ensure_dirs()
        path = root / "data" / "chat.sqlite"
        self.store = ConversationStore(path)
        self.bots = BotStore(path)
        self.settings = _StubSettings(**settings)
        self.assistant = Assistant(
            self.settings, None, None, self.config, self.store, bots=self.bots
        )
        self.rooms = Rooms(self.store, self.bots, self.assistant)

    def model(self, **kwargs):
        self.fake = _FakeModel(**kwargs)
        self.fake.install(self.assistant)
        return self.fake

    def room(self, bot_ids, mode="mention", lead="", purpose=""):
        return self.store.create(
            "Team", kind="room", mode=mode, lead_bot_id=lead or bot_ids[0],
            bot_ids=bot_ids, purpose=purpose,
        )

    def send(self, conversation, text, **kwargs):
        events = []
        run_id = kwargs.pop("run_id", "run-1")
        asyncio.run(
            self.rooms.run(
                "test", conversation, text, events.append, run_id=run_id, **kwargs
            )
        )
        return events


class DirectChatTests(unittest.TestCase):
    def setUp(self):
        self.h = _Harness(self)

    def test_each_bot_answers_with_its_own_instructions_and_model(self):
        fake = self.h.model()
        writer = self.h.bots.create(
            {"name": "Writer", "instructions": "Write warmly.", "model": "writer-model"}
        )
        planner = self.h.bots.create(
            {"name": "Planner", "instructions": "Plan tersely.", "model": "planner-model"}
        )
        first = self.h.store.create(kind="direct", bot_id=writer["id"])["id"]
        second = self.h.store.create(kind="direct", bot_id=planner["id"])["id"]
        asyncio.run(self.h.assistant.run("test", first, "Hello", lambda e: None,
                                         bot_id=writer["id"]))
        asyncio.run(self.h.assistant.run("test", second, "Hello", lambda e: None,
                                         bot_id=planner["id"]))
        self.assertIn("Write warmly.", fake.calls[0]["system"])
        self.assertNotIn("Plan tersely.", fake.calls[0]["system"])
        self.assertEqual(fake.calls[0]["model"], "writer-model")
        self.assertIn("Plan tersely.", fake.calls[1]["system"])
        self.assertNotIn("Write warmly.", fake.calls[1]["system"])
        self.assertEqual(fake.calls[1]["model"], "planner-model")

    def test_a_blank_model_falls_back_to_the_server_default(self):
        fake = self.h.model()
        bot = self.h.bots.create({"name": "Default", "model": ""})
        conversation = self.h.store.create(kind="direct", bot_id=bot["id"])["id"]
        asyncio.run(self.h.assistant.run("test", conversation, "Hi", lambda e: None,
                                         bot_id=bot["id"]))
        self.assertEqual(fake.calls[0]["model"], "test-model")

    def test_a_bot_with_no_tools_is_offered_none(self):
        fake = self.h.model()
        bot = self.h.bots.create({"name": "Quiet", "tools": []})
        conversation = self.h.store.create(kind="direct", bot_id=bot["id"])["id"]
        asyncio.run(self.h.assistant.run("test", conversation, "Hi", lambda e: None,
                                         bot_id=bot["id"]))
        self.assertEqual(fake.calls[0]["tools"], [])
        self.assertIn("You have no tools", fake.calls[0]["system"])

    def test_the_builtin_assistant_keeps_all_four_tools(self):
        fake = self.h.model()
        conversation = self.h.store.create(kind="direct", bot_id=BUILTIN_BOT_ID)["id"]
        asyncio.run(self.h.assistant.run("test", conversation, "Hi", lambda e: None))
        self.assertEqual(
            sorted(fake.calls[0]["tools"]),
            ["app_logs", "app_status", "engine_status", "list_apps"],
        )

    def test_a_revoked_tool_is_refused_at_the_moment_of_the_call(self):
        """The snapshot decided what to offer; the live grant decides what runs."""
        bot = self.h.bots.create({"name": "Reader", "tools": ["list_apps"]})
        fake = self.h.model(
            tool_calls={"Reader": [{"function": {"name": "list_apps", "arguments": {}}}]}
        )
        conversation = self.h.store.create(kind="direct", bot_id=bot["id"])["id"]
        snapshot = self.h.assistant.snapshot(bot["id"])
        self.assertEqual(snapshot.tools, ("list_apps",))
        # Revoked after the snapshot, before the call.
        self.h.bots.update(bot["id"], {"tools": []})
        with self.assertRaises(AssistantError) as caught:
            asyncio.run(
                self.h.assistant.turn(snapshot, [{"role": "user", "content": "go"}],
                                      lambda e: None)
            )
        self.assertIn("not allowed to use list_apps", str(caught.exception))

    def test_answers_are_stored_with_their_attribution(self):
        self.h.model()
        bot = self.h.bots.create({"name": "Writer", "model": "writer-model"})
        conversation = self.h.store.create(kind="direct", bot_id=bot["id"])["id"]
        asyncio.run(self.h.assistant.run("test", conversation, "Hello", lambda e: None,
                                         bot_id=bot["id"], run_id="run-9"))
        stored = self.h.store.get(conversation)["messages"]
        self.assertEqual(stored[-1]["botId"], bot["id"])
        self.assertEqual(stored[-1]["botName"], "Writer")
        self.assertEqual(stored[-1]["model"], "writer-model")
        self.assertEqual(stored[-1]["runId"], "run-9")
        self.assertEqual(stored[-1]["state"], "complete")

    def test_attribution_survives_renaming_and_deleting_the_bot(self):
        self.h.model()
        bot = self.h.bots.create({"name": "Writer"})
        conversation = self.h.store.create(kind="direct", bot_id=bot["id"])["id"]
        asyncio.run(self.h.assistant.run("test", conversation, "Hello", lambda e: None,
                                         bot_id=bot["id"]))
        self.h.bots.update(bot["id"], {"name": "Renamed"})
        self.h.bots.delete(bot["id"])
        stored = self.h.store.get(conversation)["messages"]
        # The message says who actually answered at the time.
        self.assertEqual(stored[-1]["botName"], "Writer")

    def test_an_unavailable_bot_cannot_silently_become_another_one(self):
        bot = self.h.bots.create({"name": "Gone"})
        self.h.bots.delete(bot["id"])
        with self.assertRaises(AppServiceError) as caught:
            self.h.assistant.profile_for(bot["id"])
        self.assertEqual(caught.exception.status, 409)


class RoomSelectionTests(unittest.TestCase):
    def setUp(self):
        self.h = _Harness(self)
        self.writer = self.h.bots.create({"name": "Writer"})
        self.reviewer = self.h.bots.create({"name": "Reviewer"})
        self.planner = self.h.bots.create({"name": "Planner"})
        self.ids = [self.writer["id"], self.reviewer["id"], self.planner["id"]]

    def test_mention_mode_without_mentions_asks_only_the_lead(self):
        room = self.h.room(self.ids, mode="mention", lead=self.reviewer["id"])
        self.assertEqual(self.h.rooms.select(room, "What next?"), [self.reviewer["id"]])

    def test_roundtable_asks_everyone_in_room_order(self):
        room = self.h.room(self.ids, mode="roundtable")
        self.assertEqual(self.h.rooms.select(room, "What next?"), self.ids)

    def test_an_explicit_selection_overrides_the_mode_default(self):
        room = self.h.room(self.ids, mode="roundtable")
        chosen = self.h.rooms.select(room, "hi", recipients=[self.planner["id"]])
        self.assertEqual(chosen, [self.planner["id"]])

    def test_all_addresses_every_active_member(self):
        room = self.h.room(self.ids, mode="mention")
        self.assertEqual(self.h.rooms.select(room, "@all please weigh in"), self.ids)
        self.assertEqual(self.h.rooms.select(room, "hi", recipients=["all"]), self.ids)

    def test_typed_names_resolve_and_keep_the_rooms_order(self):
        room = self.h.room(self.ids, mode="mention")
        # Typed out of order; answered in the room's order.
        self.assertEqual(
            self.h.rooms.select(room, "@Planner and @Writer, thoughts?"),
            [self.writer["id"], self.planner["id"]],
        )

    def test_a_duplicate_display_name_is_refused_rather_than_guessed(self):
        twin = self.h.bots.create({"name": "Writer 2"})
        self.h.bots.update(twin["id"], {"name": "Writer"})
        room = self.h.room([self.writer["id"], twin["id"]], mode="mention")
        with self.assertRaises(AppServiceError) as caught:
            self.h.rooms.select(room, "@Writer take this")
        self.assertEqual(caught.exception.status, 409)

    def test_a_bot_outside_the_room_is_refused(self):
        outsider = self.h.bots.create({"name": "Outsider"})
        room = self.h.room(self.ids)
        with self.assertRaises(AppServiceError) as caught:
            self.h.rooms.select(room, "hi", recipients=[outsider["id"]])
        self.assertEqual(caught.exception.status, 400)

    def test_an_app_mention_is_left_alone_and_does_not_route(self):
        room = self.h.room(self.ids, mode="mention", lead=self.writer["id"])
        # @notes is an app, not a member: the lead still answers.
        self.assertEqual(
            self.h.rooms.select(room, "check @notes for me"), [self.writer["id"]]
        )

    def test_a_room_below_two_available_bots_must_be_repaired(self):
        room = self.h.room(self.ids)
        self.h.bots.delete(self.reviewer["id"])
        self.h.bots.delete(self.planner["id"])
        with self.assertRaises(AppServiceError) as caught:
            self.h.rooms.select(room, "hello")
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("at least two", caught.exception.detail)

    def test_addressing_an_unavailable_member_is_refused(self):
        room = self.h.room(self.ids)
        self.h.bots.delete(self.planner["id"])
        with self.assertRaises(AppServiceError) as caught:
            self.h.rooms.select(room, "hi", recipients=[self.planner["id"]])
        self.assertEqual(caught.exception.status, 409)

    def test_a_deleted_member_still_shows_in_the_roster_as_unavailable(self):
        room = self.h.room(self.ids)
        self.h.bots.delete(self.planner["id"])
        roster = self.h.rooms.roster(room["id"])
        self.assertEqual(len(roster), 3)
        gone = [m for m in roster if m["id"] == self.planner["id"]][0]
        self.assertFalse(gone["available"])
        self.assertEqual(gone["name"], "Planner")


class RoomRunTests(unittest.TestCase):
    def setUp(self):
        self.h = _Harness(self)
        self.writer = self.h.bots.create({"name": "Writer", "instructions": "Draft."})
        self.reviewer = self.h.bots.create({"name": "Reviewer", "instructions": "Critique."})
        self.planner = self.h.bots.create({"name": "Planner", "instructions": "Plan."})
        self.ids = [self.writer["id"], self.reviewer["id"], self.planner["id"]]

    def _run(self, room, text, **kwargs):
        self.h.store.append(room["id"], "user", text)
        return self.h.send(room, text, **kwargs)

    def test_a_roundtable_answers_once_each_in_order(self):
        fake = self.h.model()
        room = self.h.room(self.ids, mode="roundtable")
        events = self._run(room, "Draft an announcement")
        self.assertEqual([call["bot"] for call in fake.calls],
                         ["Writer", "Reviewer", "Planner"])
        completed = [e for e in events if e.get("state") == "complete"]
        self.assertEqual([e["botName"] for e in completed],
                         ["Writer", "Reviewer", "Planner"])
        stored = self.h.store.get(room["id"])["messages"]
        self.assertEqual([m["botName"] for m in stored if m["senderKind"] == "bot"],
                         ["Writer", "Reviewer", "Planner"])

    def test_a_later_bot_sees_what_the_earlier_ones_said(self):
        fake = self.h.model(answers={"Writer": "Here is the draft."})
        room = self.h.room(self.ids, mode="roundtable")
        self._run(room, "Draft an announcement")
        reviewer_context = json.dumps(fake.calls[1]["messages"])
        self.assertIn("Here is the draft.", reviewer_context)
        # Peers are named, so nobody mistakes a peer's reply for its own.
        self.assertIn("Writer: Here is the draft.", reviewer_context)

    def test_each_responder_gets_only_its_own_instructions(self):
        fake = self.h.model()
        room = self.h.room(self.ids, mode="roundtable")
        self._run(room, "Go")
        self.assertIn("Draft.", fake.calls[0]["system"])
        self.assertNotIn("Critique.", fake.calls[0]["system"])
        self.assertIn("Critique.", fake.calls[1]["system"])
        self.assertNotIn("Draft.", fake.calls[1]["system"])

    def test_a_generated_mention_never_schedules_another_turn(self):
        """A bot writing '@Planner' must not summon anyone."""
        fake = self.h.model(answers={"Writer": "I think @Planner should decide. @all agree?"})
        room = self.h.room(self.ids, mode="mention", lead=self.writer["id"])
        self._run(room, "What should we do?")
        self.assertEqual([call["bot"] for call in fake.calls], ["Writer"])

    def test_one_failed_bot_does_not_stop_the_others(self):
        fake = self.h.model(fail_for=["Reviewer"])
        room = self.h.room(self.ids, mode="roundtable")
        events = self._run(room, "Go")
        states = {e["botName"]: e["state"] for e in events if e.get("state") in
                  ("complete", "failed")}
        self.assertEqual(states["Writer"], "complete")
        self.assertEqual(states["Reviewer"], "failed")
        self.assertEqual(states["Planner"], "complete")
        self.assertEqual([call["bot"] for call in fake.calls],
                         ["Writer", "Reviewer", "Planner"])

    def test_retrying_does_not_store_the_question_again(self):
        """The retry answers the question already in the transcript."""
        fake = self.h.model(fail_for=["Reviewer"])
        room = self.h.room(self.ids, mode="roundtable")
        self._run(room, "Go")
        questions = [
            m for m in self.h.store.get(room["id"])["messages"] if m["senderKind"] == "user"
        ]
        self.assertEqual(len(questions), 1)
        fake.fail_for.clear()
        # The retry path deliberately does not append the user message again.
        self.h.send(room, "Go", only=[self.reviewer["id"]], run_id="run-2")
        questions = [
            m for m in self.h.store.get(room["id"])["messages"] if m["senderKind"] == "user"
        ]
        self.assertEqual(len(questions), 1)

    def test_retrying_one_bot_does_not_rerun_the_others(self):
        fake = self.h.model(fail_for=["Reviewer"])
        room = self.h.room(self.ids, mode="roundtable")
        self._run(room, "Go")
        fake.fail_for.clear()
        fake.calls.clear()
        self.h.send(room, "Go", only=[self.reviewer["id"]], run_id="run-2")
        self.assertEqual([call["bot"] for call in fake.calls], ["Reviewer"])

    def test_events_carry_the_run_message_and_bot_they_belong_to(self):
        self.h.model()
        room = self.h.room(self.ids, mode="roundtable")
        events = self._run(room, "Go", run_id="run-7")
        streamed = [e for e in events if "botId" in e]
        self.assertTrue(streamed)
        for event in streamed:
            self.assertEqual(event["runId"], "run-7")
            self.assertIn(event["botId"], self.ids)
            self.assertTrue(event["messageId"])
        # Each responder gets its own message id.
        ids = {e["botId"]: e["messageId"] for e in streamed}
        self.assertEqual(len(set(ids.values())), 3)

    def test_the_queue_is_announced_before_anyone_answers(self):
        self.h.model()
        room = self.h.room(self.ids, mode="roundtable")
        events = self._run(room, "Go")
        queued = [e for e in events if e.get("room")][0]
        self.assertEqual([r["name"] for r in queued["room"]["responders"]],
                         ["Writer", "Reviewer", "Planner"])
        self.assertTrue(all(r["state"] == "queued" for r in queued["room"]["responders"]))

    def test_a_room_bot_is_told_peers_are_not_authority(self):
        fake = self.h.model()
        room = self.h.room(self.ids, mode="roundtable", purpose="Ship the release")
        self._run(room, "Go")
        system = fake.calls[0]["system"]
        self.assertIn("never expand what you", system)
        self.assertIn("Ship the release", system)

    def test_a_private_chat_is_never_pulled_into_a_room(self):
        fake = self.h.model()
        private = self.h.store.create(kind="direct", bot_id=self.writer["id"])["id"]
        self.h.store.append(private, "user", "SECRET-PASSPHRASE-XYZ")
        self.h.store.append(private, "assistant", "Noted.", bot_id=self.writer["id"],
                            bot_name="Writer")
        room = self.h.room(self.ids, mode="roundtable")
        self._run(room, "Go")
        for call in fake.calls:
            self.assertNotIn("SECRET-PASSPHRASE-XYZ", json.dumps(call["messages"]))


class RunRecordTests(unittest.TestCase):
    def setUp(self):
        self.h = _Harness(self)

    def test_a_second_run_in_one_conversation_is_refused(self):
        conversation = self.h.store.create()["id"]
        self.h.store.start_run(conversation)
        with self.assertRaises(AppServiceError) as caught:
            self.h.store.start_run(conversation)
        self.assertEqual(caught.exception.status, 409)

    def test_the_same_request_id_returns_the_same_run(self):
        conversation = self.h.store.create()["id"]
        first = self.h.store.start_run(conversation, "send-1")
        self.assertFalse(first["duplicate"])
        second = self.h.store.start_run(conversation, "send-1")
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["id"], second["id"])

    def test_a_finished_run_frees_the_conversation(self):
        conversation = self.h.store.create()["id"]
        run = self.h.store.start_run(conversation)
        self.h.store.update_run(run["id"], status="complete")
        self.assertIsNone(self.h.store.active_run(conversation))
        self.h.store.start_run(conversation)

    def test_a_restart_marks_unfinished_runs_interrupted(self):
        conversation = self.h.store.create()["id"]
        run = self.h.store.start_run(conversation)
        self.h.store.append(conversation, "assistant", "half", run_id=run["id"],
                            state="responding")
        self.assertEqual(self.h.store.interrupt_stale_runs(), 1)
        self.assertIsNone(self.h.store.active_run(conversation))
        stored = self.h.store.get(conversation)["messages"]
        self.assertEqual(stored[-1]["state"], "interrupted")
        self.assertTrue(stored[-1]["interrupted"])

    def test_stopping_a_room_run_keeps_the_partial_answer(self):
        writer = self.h.bots.create({"name": "Writer"})
        reviewer = self.h.bots.create({"name": "Reviewer"})
        room = self.h.room([writer["id"], reviewer["id"]], mode="roundtable")

        async def slow(bot, available, messages, emit):
            emit({"text": "Started writ"})
            await asyncio.sleep(10)
            return {"role": "assistant", "content": "never"}

        self.h.assistant._model_turn = slow

        async def stop_midway():
            task = asyncio.create_task(
                self.h.rooms.run("test", room, "Go", lambda e: None, run_id="run-1")
            )
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(stop_midway())
        stored = self.h.store.get(room["id"])["messages"]
        self.assertEqual(stored[-1]["content"], "Started writ")
        self.assertEqual(stored[-1]["state"], "stopped")
        # The queued second responder never ran.
        self.assertEqual(len([m for m in stored if m["senderKind"] == "bot"]), 1)


class RetentionTests(unittest.TestCase):
    def test_history_off_writes_no_room_content_but_keeps_the_bots(self):
        h = _Harness(self, chat_history=False)
        fake = h.model()
        writer = h.bots.create({"name": "Writer"})
        reviewer = h.bots.create({"name": "Reviewer"})
        room = h.room([writer["id"], reviewer["id"]], mode="roundtable")
        asyncio.run(
            h.rooms.run("test", room, "Go", lambda e: None, keep=False, run_id="")
        )
        self.assertEqual([c["bot"] for c in fake.calls], ["Writer", "Reviewer"])
        # Nothing the run produced was written down.
        self.assertEqual(h.store.get(room["id"])["messages"], [])
        # The bots themselves are settings and survive.
        self.assertEqual(len(h.bots.list()), 2)

    def test_purging_clears_rooms_members_and_runs_but_not_bots(self):
        h = _Harness(self)
        h.model()
        writer = h.bots.create({"name": "Writer"})
        reviewer = h.bots.create({"name": "Reviewer"})
        room = h.room([writer["id"], reviewer["id"]], mode="roundtable")
        h.store.append(room["id"], "user", "Go")
        h.store.start_run(room["id"], "send-1")
        h.store.purge()
        with h.store.connection() as db:
            for table in ("messages", "conversations", "conversation_bots", "chat_runs"):
                count = db.execute("SELECT count(*) AS n FROM " + table).fetchone()["n"]
                self.assertEqual(count, 0, table)
        self.assertEqual(len(h.bots.list()), 2)


class BotApiTests(unittest.TestCase):
    def setUp(self):
        self.temp, root = _temp_root("vela-bots-api-")
        self.addCleanup(self.temp.cleanup)
        self.config = Config(root / "data", root / "apps", ROOT / "web/dist")
        self.config.ensure_dirs()
        self.raw = TestClient(create_app(self.config))
        self.addCleanup(self.raw.close)
        token = self.raw.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.raw.headers.update({"Authorization": "Bearer " + token})
        self.client = self.raw

    def test_bots_need_the_hub_bearer(self):
        anonymous = TestClient(create_app(self.config))
        self.addCleanup(anonymous.close)
        self.assertEqual(anonymous.get("/api/bots").status_code, 401)

    def test_bots_are_listed_with_the_builtin_and_the_selectable_tools(self):
        body = self.client.get("/api/bots").json()
        self.assertEqual(body["builtin"]["id"], BUILTIN_BOT_ID)
        self.assertEqual(body["bots"], [])
        self.assertEqual(
            body["tools"], ["list_apps", "app_status", "app_logs", "engine_status"]
        )

    def test_create_read_update_duplicate_and_delete(self):
        created = self.client.post(
            "/api/bots", json={"name": "Writer", "instructions": "Draft copy."}
        )
        self.assertEqual(created.status_code, 201)
        bot_id = created.json()["id"]
        self.assertEqual(self.client.get("/api/bots/" + bot_id).json()["name"], "Writer")
        patched = self.client.patch("/api/bots/" + bot_id, json={"tools": ["list_apps"]})
        self.assertEqual(patched.json()["tools"], ["list_apps"])
        copy = self.client.post("/api/bots/" + bot_id + "/duplicate")
        self.assertEqual(copy.status_code, 201)
        self.assertEqual(copy.json()["tools"], [])
        self.assertEqual(self.client.delete("/api/bots/" + bot_id).status_code, 204)
        self.assertNotIn(bot_id, [b["id"] for b in self.client.get("/api/bots").json()["bots"]])

    def test_an_unknown_tool_is_refused_by_the_api(self):
        response = self.client.post("/api/bots", json={"name": "Sneaky", "tools": ["rm_rf"]})
        self.assertEqual(response.status_code, 400)

    def test_bots_remain_available_while_history_is_off(self):
        created = self.client.post("/api/bots", json={"name": "Writer"})
        self.client.patch("/api/settings", json={"chat_history": False})
        listed = self.client.get("/api/bots")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([b["id"] for b in listed.json()["bots"]], [created.json()["id"]])
        # Conversations are gone, though.
        self.assertEqual(self.client.get("/api/chat/conversations").json()["conversations"], [])

    def test_creating_a_room_validates_membership(self):
        writer = self.client.post("/api/bots", json={"name": "Writer"}).json()
        reviewer = self.client.post("/api/bots", json={"name": "Reviewer"}).json()
        created = self.client.post(
            "/api/chat/conversations",
            json={
                "kind": "room",
                "title": "Content team",
                "mode": "roundtable",
                "botIds": [writer["id"], reviewer["id"]],
            },
        )
        self.assertEqual(created.status_code, 201)
        room = created.json()
        self.assertEqual(room["kind"], "room")
        self.assertEqual(room["botIds"], [writer["id"], reviewer["id"]])
        self.assertEqual(room["leadBotId"], writer["id"])

    def test_a_room_needs_at_least_two_bots(self):
        writer = self.client.post("/api/bots", json={"name": "Writer"}).json()
        response = self.client.post(
            "/api/chat/conversations", json={"kind": "room", "botIds": [writer["id"]]}
        )
        self.assertIn(response.status_code, (400, 422))

    def test_a_room_cannot_be_built_around_a_deleted_bot(self):
        writer = self.client.post("/api/bots", json={"name": "Writer"}).json()
        gone = self.client.post("/api/bots", json={"name": "Gone"}).json()
        self.client.delete("/api/bots/" + gone["id"])
        response = self.client.post(
            "/api/chat/conversations",
            json={"kind": "room", "botIds": [writer["id"], gone["id"]]},
        )
        self.assertEqual(response.status_code, 409)

    def test_an_old_conversation_still_creates_as_a_direct_vela_chat(self):
        """The pre-bots request shape keeps working unchanged."""
        created = self.client.post("/api/chat/conversations")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["kind"], "direct")
        self.assertEqual(created.json()["botId"], BUILTIN_BOT_ID)

    def test_membership_can_be_repaired(self):
        ids = [
            self.client.post("/api/bots", json={"name": n}).json()["id"]
            for n in ("Writer", "Reviewer", "Planner")
        ]
        room = self.client.post(
            "/api/chat/conversations", json={"kind": "room", "botIds": ids[:2]}
        ).json()
        updated = self.client.put(
            "/api/chat/conversations/" + room["id"] + "/members",
            json={"botIds": [ids[0], ids[2]], "leadBotId": ids[2]},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["botIds"], [ids[0], ids[2]])
        self.assertEqual(updated.json()["leadBotId"], ids[2])

    def test_a_broken_room_refuses_the_send_without_storing_the_question(self):
        ids = [
            self.client.post("/api/bots", json={"name": n}).json()["id"]
            for n in ("Writer", "Reviewer")
        ]
        room = self.client.post(
            "/api/chat/conversations", json={"kind": "room", "botIds": ids}
        ).json()
        self.client.delete("/api/bots/" + ids[1])
        with self.client.stream(
            "POST",
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Go"}],
                "conversationId": room["id"],
            },
        ) as response:
            body = "".join(response.iter_text())
        self.assertIn("at least two available bots", body)
        # The refused send left nothing behind in the transcript.
        stored = self.client.get("/api/chat/conversations/" + room["id"]).json()
        self.assertEqual(stored["messages"], [])

    def test_the_run_endpoint_reports_and_stops(self):
        room = self.client.post("/api/chat/conversations").json()
        self.assertIsNone(self.client.get(
            "/api/chat/conversations/" + room["id"] + "/run").json()["run"])
        self.assertFalse(self.client.delete(
            "/api/chat/conversations/" + room["id"] + "/run").json()["stopped"])


if __name__ == "__main__":
    unittest.main()
