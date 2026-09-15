"""Run with: python -m unittest discover -s tests. Uses disposable data only."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_bootstrap = tempfile.TemporaryDirectory(prefix="vela-chat-bootstrap-")
os.environ.setdefault("VELA_DATA_DIR", _bootstrap.name)

from fastapi.testclient import TestClient

from vela.api import create_app
from vela.app_storage import AppServiceError
from vela.assistant import Assistant, HISTORY_LIMIT
from vela.config import Config
from vela.conversations import ConversationStore, MAX_CONVERSATIONS


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-chat-store-")
        self.addCleanup(self.temp.cleanup)
        self.store = ConversationStore(Path(self.temp.name) / "chat.sqlite")

    def test_first_question_titles_the_conversation(self):
        conversation = self.store.create()
        self.assertEqual(conversation["title"], "New conversation")
        self.store.append(conversation["id"], "user", "  Why did Notes stop   syncing? ")
        self.assertEqual(self.store.get(conversation["id"])["title"], "Why did Notes stop syncing?")
        # A later question must not rewrite a title the person can rename.
        self.store.append(conversation["id"], "user", "And the database?")
        self.assertEqual(self.store.get(conversation["id"])["title"], "Why did Notes stop syncing?")

    def test_separate_conversations_keep_their_own_messages(self):
        first = self.store.create()["id"]
        second = self.store.create()["id"]
        self.store.append(first, "user", "one")
        self.store.append(second, "user", "two")
        self.assertEqual([m["content"] for m in self.store.get(first)["messages"]], ["one"])
        self.assertEqual([m["content"] for m in self.store.get(second)["messages"]], ["two"])

    def test_context_is_bounded_and_ordered(self):
        conversation = self.store.create()["id"]
        for index in range(HISTORY_LIMIT + 6):
            self.store.append(conversation, "user", f"question {index}")
        context = self.store.context(conversation, HISTORY_LIMIT)
        self.assertEqual(len(context), HISTORY_LIMIT)
        self.assertEqual(context[-1]["content"], f"question {HISTORY_LIMIT + 5}")
        # The stored transcript keeps more than the model is told.
        self.assertGreater(len(self.store.get(conversation)["messages"]), len(context))

    def test_archive_is_reversible_and_distinct_from_deletion(self):
        conversation = self.store.create()["id"]
        self.store.append(conversation, "user", "keep me")
        self.store.update(conversation, archived=True)
        self.assertEqual(self.store.browse(), [])
        self.assertEqual([c["id"] for c in self.store.browse(archived=True)], [conversation])
        self.store.update(conversation, archived=False)
        self.assertEqual([c["id"] for c in self.store.browse()], [conversation])
        self.store.delete(conversation)
        with self.assertRaises(AppServiceError):
            self.store.get(conversation)

    def test_search_matches_titles_and_message_bodies(self):
        first = self.store.create()["id"]
        self.store.append(first, "user", "Storage question")
        second = self.store.create()["id"]
        self.store.append(second, "user", "Something else")
        self.store.append(second, "assistant", "The disk is nearly full")
        self.assertEqual({c["id"] for c in self.store.browse(query="storage")}, {first})
        self.assertEqual({c["id"] for c in self.store.browse(query="nearly full")}, {second})
        self.assertEqual(self.store.browse(query="no such text"), [])

    def test_drafts_survive_a_reload_without_reordering_history(self):
        older = self.store.create()["id"]
        self.store.append(older, "user", "older")
        newer = self.store.create()["id"]
        self.store.append(newer, "user", "newer")
        self.store.update(older, draft="half-typed question")
        self.assertEqual(self.store.get(older)["draft"], "half-typed question")
        self.assertEqual([c["id"] for c in self.store.browse()], [newer, older])

    def test_legacy_import_runs_once_even_when_retried(self):
        transcript = [
            {"role": "user", "content": "Imported question"},
            {"role": "assistant", "content": "Imported answer"},
            {"role": "system", "content": "ignored"},
            {"role": "user", "content": "   "},
        ]
        first = self.store.import_legacy(transcript)
        self.assertTrue(first["imported"])
        second = self.store.import_legacy(transcript)
        self.assertFalse(second["imported"])
        self.assertEqual(len(self.store.browse()), 1)
        messages = self.store.get(first["conversationId"])["messages"]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertTrue(self.store.legacy_import_done())

    def test_conversation_count_is_bounded(self):
        for index in range(MAX_CONVERSATIONS + 3):
            self.store.create(f"conversation {index}")
        self.assertEqual(len(self.store.browse(limit=MAX_CONVERSATIONS)), MAX_CONVERSATIONS)

    def test_purge_removes_everything(self):
        conversation = self.store.create()["id"]
        self.store.append(conversation, "user", "sensitive")
        self.store.purge()
        self.assertEqual(self.store.browse(), [])
        with self.assertRaises(AppServiceError):
            self.store.get(conversation)


class _StubSettings:
    def __init__(self, history=True):
        self.values = {"chat_history": history, "chat_model": "test-model"}

    def get(self, key, default=None):
        return self.values.get(key, default)


class AssistantContinuityTests(unittest.TestCase):
    """The stored transcript, not a process-local cache, feeds the model."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-chat-assistant-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = Config(root / "data", root / "apps", ROOT / "web/dist")
        self.config.ensure_dirs()
        self.store = ConversationStore(root / "data" / "chat.sqlite")
        self.settings = _StubSettings()
        self.seen = []

    def _assistant(self, answer="An answer"):
        assistant = Assistant(self.settings, None, None, self.config, self.store)

        async def turn(messages, emit):
            self.seen.append(messages)
            emit({"text": answer})
            return {"role": "assistant", "content": answer}

        assistant._model_turn = turn
        return assistant

    def run_turn(self, assistant, conversation_id, text):
        events = []
        result = asyncio.run(assistant.run("test", conversation_id, text, events.append))
        return result, events

    def test_a_new_process_still_answers_with_the_earlier_context(self):
        assistant = self._assistant()
        conversation_id, events = self.run_turn(assistant, None, "First question")
        self.assertTrue(any(event.get("conversationId") for event in events))
        # A fresh Assistant stands in for a restarted server.
        restarted = self._assistant()
        self.run_turn(restarted, conversation_id, "Second question")
        sent = [m["content"] for m in self.seen[-1] if m["role"] in ("user", "assistant")]
        self.assertIn("First question", sent)
        self.assertIn("Second question", sent)

    def test_an_unknown_conversation_is_refused_rather_than_silently_replaced(self):
        assistant = self._assistant()
        with self.assertRaises(Exception):
            self.run_turn(assistant, "not-a-real-conversation", "Hello")
        self.assertEqual(self.store.browse(), [])

    def test_retention_off_writes_nothing(self):
        self.settings.values["chat_history"] = False
        assistant = self._assistant()
        conversation_id, events = self.run_turn(assistant, None, "Transient question")
        self.assertTrue(conversation_id)
        self.assertEqual(self.store.browse(), [])
        self.assertFalse(self.store.exists(conversation_id))

    def test_a_failed_turn_keeps_the_partial_answer(self):
        assistant = self._assistant()

        async def failing(messages, emit):
            emit({"text": "Partly wri"})
            raise RuntimeError("stream ended")

        assistant._model_turn = failing
        with self.assertRaises(RuntimeError):
            self.run_turn(assistant, None, "Question that fails")
        stored = self.store.get(self.store.browse()[0]["id"])["messages"]
        self.assertEqual([m["role"] for m in stored], ["user", "assistant"])
        self.assertEqual(stored[-1]["content"], "Partly wri")
        self.assertTrue(stored[-1]["interrupted"])


class ConversationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-chat-api-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        config = Config(root / "data", root / "apps", ROOT / "web/dist")
        config.ensure_dirs()
        self.client = TestClient(create_app(config))
        self.addCleanup(self.client.close)
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def test_conversations_require_the_hub_bearer(self):
        self.assertEqual(self.client.get("/api/chat/conversations").status_code, 401)

    def test_create_read_rename_archive_and_delete(self):
        created = self.client.post("/api/chat/conversations", headers=self.hub)
        self.assertEqual(created.status_code, 201, created.text)
        conversation_id = created.json()["id"]

        renamed = self.client.patch(
            f"/api/chat/conversations/{conversation_id}",
            headers=self.hub,
            json={"title": "Storage review"},
        )
        self.assertEqual(renamed.json()["title"], "Storage review")

        self.client.patch(
            f"/api/chat/conversations/{conversation_id}",
            headers=self.hub,
            json={"archived": True},
        )
        listed = self.client.get("/api/chat/conversations", headers=self.hub).json()
        self.assertEqual(listed["conversations"], [])
        archived = self.client.get(
            "/api/chat/conversations", headers=self.hub, params={"archived": True}
        ).json()
        self.assertEqual([c["id"] for c in archived["conversations"]], [conversation_id])

        removed = self.client.delete(
            f"/api/chat/conversations/{conversation_id}", headers=self.hub
        )
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(
            self.client.get(
                f"/api/chat/conversations/{conversation_id}", headers=self.hub
            ).status_code,
            404,
        )

    def test_an_inaccessible_id_is_not_found(self):
        response = self.client.get(
            "/api/chat/conversations/../../etc/passwd", headers=self.hub
        )
        self.assertIn(response.status_code, (404, 400))

    def test_turning_history_off_deletes_stored_conversations(self):
        conversation_id = self.client.post("/api/chat/conversations", headers=self.hub).json()["id"]
        self.client.patch("/api/settings", headers=self.hub, json={"chat_history": False})
        listed = self.client.get("/api/chat/conversations", headers=self.hub).json()
        self.assertEqual(listed, {"conversations": [], "enabled": False})
        # With retention off the endpoints refuse rather than pretend to save.
        self.assertEqual(
            self.client.post("/api/chat/conversations", headers=self.hub).status_code, 409
        )
        self.client.patch("/api/settings", headers=self.hub, json={"chat_history": True})
        self.assertEqual(
            self.client.get(
                f"/api/chat/conversations/{conversation_id}", headers=self.hub
            ).status_code,
            404,
        )

    def test_legacy_import_is_repeat_safe_over_http(self):
        payload = {"messages": [{"role": "user", "content": "Old question"}]}
        first = self.client.post(
            "/api/chat/conversations/import", headers=self.hub, json=payload
        ).json()
        self.assertTrue(first["imported"])
        second = self.client.post(
            "/api/chat/conversations/import", headers=self.hub, json=payload
        ).json()
        self.assertFalse(second["imported"])
        listed = self.client.get("/api/chat/conversations", headers=self.hub).json()
        self.assertEqual(len(listed["conversations"]), 1)


if __name__ == "__main__":
    unittest.main()
