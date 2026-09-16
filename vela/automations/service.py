"""The automation service: workflows, runs, schedules, webhooks and approvals.

Vela owns everything durable. The worker only executes a revision it is handed
and asks for permission to touch anything outside itself.
"""
import asyncio
import contextlib
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from ..app_storage import AppServiceError
from . import blueprints as blueprint_library
from . import catalog as node_catalog
from . import schedules, validate
from .effects import Effects
from .store import Store, now
from .worker import Worker, WorkerUnavailable, availability, describe_runtime

#: How many runs execute at once across every workflow. One workflow still
#: never has two live runs; this only lets independent workflows overlap.
MAX_CONCURRENT_RUNS = 2

#: Wall-clock limit for one run, and how long an effect may wait for Vela.
RUN_DEADLINE_MS = 10 * 60 * 1000

SCHEDULE_TICK_SECONDS = 20

MAX_WEBHOOK_BODY_BYTES = 64 * 1024

STARTER_DOCUMENT = {
    'version': 1,
    'nodes': [{'id': 'trigger', 'type': 'manual-trigger', 'config': {'payload': '{}'}}],
    'edges': [],
    'meta': {},
}


def _digest(document) -> str:
    return hashlib.sha256(validate.canonical(document).encode('utf-8')).hexdigest()


class Automations:
    def __init__(self, config, registry, actions, notifier, settings, *, log=None):
        self.config = config
        self.registry = registry
        self.actions = actions
        self.notifier = notifier
        self.store = Store(config.data_dir / 'automations.sqlite')
        self.effects = Effects(self.store, actions, registry, notifier, settings)
        self._log = log or (lambda message: None)
        self.worker = Worker(on_event=self._on_event, on_effect=self._on_effect, log=self._log)
        self._runners: list[asyncio.Task] = []
        self._scheduler: asyncio.Task | None = None
        # Created in start(), inside the loop that will await it: an asyncio
        # Event binds to the first loop that touches it, and this service is
        # constructed before any loop is running.
        self._wake: asyncio.Event | None = None
        self._contexts: dict[str, dict] = {}
        self._buffers: dict[str, list] = {}
        self._started = False
        # FastAPI runs non-async handlers in a worker thread, so nothing that
        # touches the loop may be called directly from one. Everything goes
        # through `_notify` / `_spawn`, which hop back onto the loop safely.
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------ lifecycle --

    async def start(self):
        if self._started:
            return
        self._started = True
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        recovered = self.store.recover_interrupted(
            'Vela stopped while this run was in progress. Steps that had already finished were '
            'not undone.')
        if recovered:
            self._log(f'automations: {recovered} run(s) marked interrupted after restart')
        self._restore_schedules()
        self._runners = [asyncio.create_task(self._run_loop()) for _ in range(MAX_CONCURRENT_RUNS)]
        self._scheduler = asyncio.create_task(self._schedule_loop())

    async def stop(self):
        self._started = False
        for task in [*self._runners, self._scheduler]:
            if task:
                task.cancel()
        for task in [*self._runners, self._scheduler]:
            if task:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._runners, self._scheduler = [], None
        self._loop, self._wake = None, None
        await self.worker.stop()

    def _notify(self):
        """Wake the run loop from any thread."""
        loop, wake = self._loop, self._wake
        if loop is None or wake is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:
            pass

    def _spawn(self, coroutine):
        """Start background work on the server's loop from any thread."""
        loop = self._loop
        if loop is None or loop.is_closed():
            coroutine.close()
            raise AppServiceError(503, 'Automations are not running on this server.')
        if loop is _running_loop():
            return asyncio.ensure_future(coroutine, loop=loop)
        return asyncio.run_coroutine_threadsafe(coroutine, loop)

    def status(self) -> dict:
        state = availability()
        return {
            **state,
            'worker': self.worker.info.as_dict() if self.worker.info else None,
            'running': self.worker.running,
            'notifications': self.effects.notification_destination(),
            **self.today(),
        }

    def today(self) -> dict:
        """Run counts since this computer's midnight, for the desk's Flows widget.

        'Today' is the user's day, not UTC: a run at 23:00 local should still be
        today's run at 23:30. `queued_at` is stored in UTC, so local midnight is
        converted before it is compared.
        """
        midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        stats = self.store.run_statistics(midnight.astimezone(timezone.utc).isoformat())
        average = stats.get('averageSeconds')
        return {
            'runsToday': stats.get('total', 0),
            'failuresToday': stats.get('failed', 0),
            'averageDurationMs': None if average is None else int(round(average * 1000)),
        }

    # ------------------------------------------------------------- catalog --

    def catalog(self) -> dict:
        return node_catalog.build(self.registry)

    def blueprints(self) -> dict:
        """Starting points, each marked with whether this server can run it."""
        items = blueprint_library.available(self.registry, self.notifier)
        return {'blueprints': [{key: value for key, value in item.items() if key != 'document'}
                               for item in items]}

    def create_from_blueprint(self, blueprint_id, name=None) -> dict:
        blueprint = blueprint_library.find(self.registry, self.notifier, blueprint_id)
        if blueprint is None:
            raise AppServiceError(404, 'That starting point is not available on this server.')
        if not blueprint['available']:
            raise AppServiceError(409, blueprint['requirement'])
        catalog = self.catalog()
        document = validate.check_storable(blueprint['document'], catalog)
        title = (name or blueprint['name'])[:120]
        document['meta']['name'] = title
        # A blueprint is a draft like any other: inactive, with nothing allowed.
        workflow_id = self.store.create_workflow(title, document, _digest(document),
                                                 catalog['version'],
                                                 description=blueprint['description'])
        return self.detail(workflow_id)

    # ----------------------------------------------------------- workflows --

    def create(self, name: str) -> dict:
        catalog = self.catalog()
        document = validate.check_storable(dict(STARTER_DOCUMENT), catalog)
        document['meta']['name'] = name
        workflow_id = self.store.create_workflow(name, document, _digest(document),
                                                 catalog['version'])
        return self.detail(workflow_id)

    def summaries(self, include_archived=False) -> dict:
        catalog = self.catalog()
        items = []
        for workflow in self.store.list_workflows(include_archived):
            items.append(self._summary(workflow, catalog))
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        return {
            'automations': items,
            'statistics': self.store.run_statistics(since),
            'status': self.status(),
        }

    def _summary(self, workflow, catalog):
        revision = workflow['active_revision'] or workflow['draft_revision']
        try:
            stored = self.store.get_revision(workflow['id'], revision)
            document = stored['document']
            problems = validate.executable_problems(document, catalog)
            trigger = validate.trigger_kind(document)
            steps = len(document['nodes'])
        except AppServiceError:
            document, problems, trigger, steps = None, [], None, 0
        runs = self.store.list_runs(workflow['id'], limit=1)
        schedule = self.store.schedule(workflow['id'])
        return {
            'id': workflow['id'],
            'name': workflow['name'],
            'description': workflow['description'],
            'status': workflow['status'],
            'draftRevision': workflow['draft_revision'],
            'activeRevision': workflow['active_revision'],
            'hasUnpublishedChanges': bool(workflow['active_revision']
                                          and workflow['active_revision'] != workflow['draft_revision']),
            'updatedAt': workflow['updated_at'],
            'trigger': trigger,
            'steps': steps,
            'problems': problems,
            'needsAttention': bool(problems) and workflow['status'] in ('active', 'paused'),
            'lastRun': self._run_view(runs[0]) if runs else None,
            'schedule': self._schedule_view(schedule, document),
            'grants': self.effects.review(workflow['id'], document) if document else [],
        }

    def _schedule_view(self, schedule, document):
        if not schedule or not document:
            return None
        config = json.loads(schedule['config'])
        return {
            'summary': schedules.describe(config, schedule['timezone']),
            'timezone': schedule['timezone'],
            'nextRun': schedule['next_due'],
        }

    def detail(self, workflow_id: str) -> dict:
        workflow = self.store.get_workflow(workflow_id)
        catalog = self.catalog()
        draft = self.store.get_revision(workflow_id, workflow['draft_revision'])
        active = (self.store.get_revision(workflow_id, workflow['active_revision'])
                  if workflow['active_revision'] else None)
        summary = self._summary(workflow, catalog)
        return {
            **summary,
            'document': draft['document'],
            'documentRevision': workflow['draft_revision'],
            'draftProblems': validate.executable_problems(draft['document'], catalog),
            'activeDocument': active['document'] if active else None,
            'grants': self.effects.review(workflow_id, draft['document']),
            'webhook': self._webhook_view(workflow_id, draft['document']),
            'createdAt': workflow['created_at'],
        }

    def save(self, workflow_id, expected_revision, document, name=None, description=None) -> dict:
        catalog = self.catalog()
        clean = validate.check_storable(document, catalog)
        if name is not None:
            clean['meta']['name'] = name
        revision = self.store.save_draft(workflow_id, expected_revision, clean, _digest(clean),
                                         catalog['version'], name=name, description=description)
        workflow = self.store.get_workflow(workflow_id)
        # A saved change to a granted step invalidates that grant immediately,
        # rather than at the next run.
        self._prune_stale_grants(workflow_id, clean)
        if workflow['status'] == 'active' and workflow['active_revision'] == revision:
            self._sync_triggers(workflow_id, clean)
        return self.detail(workflow_id)

    def _prune_stale_grants(self, workflow_id, document):
        for review in self.effects.review(workflow_id, document):
            if review.get('stale') or not review.get('available'):
                self.store.revoke_grant(workflow_id, review['app'], review['action'])

    def rename(self, workflow_id, name, description) -> dict:
        workflow = self.store.get_workflow(workflow_id)
        stored = self.store.get_revision(workflow_id, workflow['draft_revision'])
        document = stored['document']
        document['meta']['name'] = name
        self.store.save_draft(workflow_id, workflow['draft_revision'], document, _digest(document),
                              stored['catalog_version'], name=name, description=description)
        self.store.rename(workflow_id, name, description)
        return self.detail(workflow_id)

    def duplicate(self, workflow_id) -> dict:
        workflow = self.store.get_workflow(workflow_id)
        stored = self.store.get_revision(workflow_id, workflow['draft_revision'])
        catalog = self.catalog()
        document = validate.check_storable(stored['document'], catalog)
        name = f'{workflow["name"]} copy'[:120]
        document['meta']['name'] = name
        # A copy starts inactive with no permissions and no schedule of its own.
        new_id = self.store.create_workflow(name, document, _digest(document), catalog['version'],
                                            description=workflow['description'])
        return self.detail(new_id)

    def set_archived(self, workflow_id, archived) -> dict:
        workflow = self.store.get_workflow(workflow_id)
        if archived:
            self._deactivate(workflow_id)
            self.store.set_status(workflow_id, 'archived', active_revision=None)
        elif workflow['status'] == 'archived':
            self.store.set_status(workflow_id, 'draft')
        return self.detail(workflow_id)

    def delete(self, workflow_id):
        self.store.get_workflow(workflow_id)
        self._deactivate(workflow_id)
        self.store.delete_workflow(workflow_id)
        self.actions.forget_automation(workflow_id)

    def export(self, workflow_id) -> dict:
        workflow = self.store.get_workflow(workflow_id)
        stored = self.store.get_revision(workflow_id, workflow['draft_revision'])
        return {
            'vela': {'kind': 'automation', 'exportVersion': 1, 'catalogVersion': node_catalog.CATALOG_VERSION},
            'name': workflow['name'],
            'description': workflow['description'],
            # Permissions, schedules, webhook secrets and run history stay behind
            # on purpose: an imported copy must be reviewed again.
            'document': stored['document'],
        }

    def import_document(self, payload) -> dict:
        if not isinstance(payload, dict):
            raise AppServiceError(422, 'That file is not a Vela automation.')
        envelope = payload.get('vela')
        document = payload.get('document') if isinstance(envelope, dict) else payload
        if isinstance(envelope, dict):
            if envelope.get('kind') != 'automation':
                raise AppServiceError(422, 'That file is not a Vela automation.')
            if envelope.get('exportVersion') != 1:
                raise AppServiceError(422, 'That automation was exported by a newer version of '
                                           'Vela. Update Vela and try again.')
        catalog = self.catalog()
        try:
            clean = validate.check_storable(document, catalog)
        except validate.DocumentError as exc:
            raise AppServiceError(422, exc.detail) from exc
        name = (payload.get('name') if isinstance(payload, dict) else None) \
            or clean['meta'].get('name') or 'Imported automation'
        clean['meta']['name'] = str(name)[:120]
        workflow_id = self.store.create_workflow(str(name)[:120], clean, _digest(clean),
                                                 catalog['version'],
                                                 description=str(payload.get('description') or '')[:500])
        return self.detail(workflow_id)

    # ---------------------------------------------------------- activation --

    def activate(self, workflow_id) -> dict:
        workflow = self.store.get_workflow(workflow_id)
        if workflow['status'] == 'archived':
            raise AppServiceError(409, 'Restore this automation before turning it on.')
        catalog = self.catalog()
        stored = self.store.get_revision(workflow_id, workflow['draft_revision'])
        document = stored['document']
        problems = validate.executable_problems(document, catalog)
        if problems:
            raise AppServiceError(409, problems[0]['detail'])
        for review in self.effects.review(workflow_id, document):
            if not review.get('available'):
                raise AppServiceError(409, review.get('error', 'A step is unavailable.'))
            if not review['granted']:
                raise AppServiceError(403, f'Allow this automation to use '
                                           f'{review.get("appName", review["app"])} · '
                                           f'{review.get("title", review["action"])} first.')
        self.store.freeze_revision(workflow_id, workflow['draft_revision'])
        self.store.set_status(workflow_id, 'active', active_revision=workflow['draft_revision'])
        self._sync_triggers(workflow_id, document)
        self._notify()
        return self.detail(workflow_id)

    def pause(self, workflow_id) -> dict:
        workflow = self.store.get_workflow(workflow_id)
        if workflow['status'] != 'active':
            raise AppServiceError(409, 'That automation is not running.')
        self.store.set_status(workflow_id, 'paused')
        self.store.clear_schedule(workflow_id)
        return self.detail(workflow_id)

    def _deactivate(self, workflow_id):
        self.store.clear_schedule(workflow_id)
        self.store.clear_webhook(workflow_id)
        self.store.revoke_all_grants(workflow_id)

    def _sync_triggers(self, workflow_id, document):
        node = validate.trigger_node(document)
        kind = validate.trigger_kind(document)
        if kind == 'schedule':
            config, label = schedules.schedule_from_node(node)
            due = schedules.next_occurrence(config, label, datetime.now(timezone.utc))
            self.store.save_schedule(workflow_id, node['id'], config, label, due.isoformat())
        else:
            self.store.clear_schedule(workflow_id)
        if kind == 'webhook':
            if not self.store.webhook(workflow_id):
                self.rotate_webhook(workflow_id, node['id'])
        else:
            self.store.clear_webhook(workflow_id)

    def _restore_schedules(self):
        """Re-arm schedules after a restart and report the interval that was missed."""
        moment = datetime.now(timezone.utc)
        for workflow in self.store.list_workflows():
            if workflow['status'] != 'active' or not workflow['active_revision']:
                continue
            schedule = self.store.schedule(workflow['id'])
            if not schedule or not schedule['next_due']:
                continue
            due = datetime.fromisoformat(schedule['next_due'])
            if due > moment:
                continue
            missed = schedules.missed_between(json.loads(schedule['config']), schedule['timezone'],
                                              due, moment)
            if missed:
                self._log(f'automations: skipped {missed} missed run(s) of "{workflow["name"]}" '
                          f'while Vela was not running')

    # ---------------------------------------------------------------- runs --

    def run_now(self, workflow_id, trigger_input=None) -> dict:
        workflow = self.store.get_workflow(workflow_id)
        catalog = self.catalog()
        revision = workflow['active_revision'] or workflow['draft_revision']
        stored = self.store.get_revision(workflow_id, revision)
        problems = validate.executable_problems(stored['document'], catalog)
        if problems:
            raise AppServiceError(409, problems[0]['detail'])
        state = availability()
        if not state['available']:
            raise AppServiceError(503, state['detail'])
        if workflow['status'] == 'archived':
            raise AppServiceError(409, 'Restore this automation before running it.')
        # Vela runs one at a time for each automation. Someone pressing Run is
        # told so straight away rather than having their run queue up silently.
        live = self.store.live_run(workflow_id, ('running', 'waiting'))
        if live:
            raise AppServiceError(
                409,
                'This automation is waiting for your decision on its current run.'
                if live['status'] == 'waiting'
                else 'This automation is already running. Wait for it to finish, or cancel it.')
        # A manual run of an unactivated draft still pins that exact revision, so
        # later edits cannot change what is already queued.
        self.store.freeze_revision(workflow_id, revision)
        run_id = self.store.queue_run(workflow_id, revision, 'manual', trigger_input,
                                      runtime=describe_runtime(self.worker.info))
        self._notify()
        return self.run_detail(run_id)

    def cancel_run(self, run_id) -> dict:
        state = self.store.request_cancel(run_id)
        if state in ('running', 'waiting'):
            self._spawn(self._cancel_worker_run(run_id))
        return self.run_detail(run_id)

    async def _cancel_worker_run(self, run_id):
        found = await self.worker.cancel(run_id)
        if not found:
            # A waiting run holds no worker slot; end it here.
            run = self.store.get_run(run_id)
            if run['status'] in ('waiting', 'queued'):
                self.store.finish_run(run_id, 'cancelled',
                                      'Cancelled. Steps that already finished were not undone.')

    def runs(self, workflow_id=None, limit=50, before=None) -> dict:
        return {'runs': [self._run_view(run) for run in
                         self.store.list_runs(workflow_id, limit, before)]}

    def run_detail(self, run_id) -> dict:
        run = self.store.get_run(run_id)
        view = self._run_view(run)
        view['events'] = self.store.events(run_id)
        view['approvals'] = [self._approval_view(item) for item in self.store.approvals(run_id)]
        try:
            stored = self.store.get_revision(run['workflow_id'], run['revision'])
            view['document'] = stored['document']
        except AppServiceError:
            view['document'] = None
            view['documentUnavailable'] = True
        return view

    def run_events(self, run_id, after=0) -> dict:
        run = self.store.get_run(run_id)
        return {'runId': run_id, 'status': run['status'], 'finishedAt': run['finished_at'],
                'events': self.store.events(run_id, after)}

    def _run_view(self, run):
        return {
            'id': run['id'],
            'workflowId': run['workflow_id'],
            'workflowName': run.get('workflow_name'),
            'revision': run['revision'],
            'status': run['status'],
            'trigger': run['trigger'],
            'queuedAt': run['queued_at'],
            'startedAt': run['started_at'],
            'finishedAt': run['finished_at'],
            'error': run['error'],
            'runtime': json.loads(run['runtime'] or '{}'),
            'cancelRequested': bool(run['cancel_requested']),
        }

    # ----------------------------------------------------------- execution --

    async def _run_loop(self):
        while True:
            try:
                run = self.store.claim_next_run()
                if run is None:
                    wake = self._wake
                    if wake is None:
                        return
                    wake.clear()
                    try:
                        await asyncio.wait_for(wake.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        pass
                    continue
                await self._execute(run)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must survive one bad run
                self._log(f'automations: run loop error: {exc}')
                await asyncio.sleep(1)

    async def _execute(self, run, *, approvals=None, resume=None):
        run_id = run['id']
        try:
            stored = self.store.get_revision(run['workflow_id'], run['revision'])
        except AppServiceError as exc:
            self.store.finish_run(run_id, 'failed', exc.detail)
            return
        document = stored['document']
        self._contexts[run_id] = {'workflowId': run['workflow_id'], 'document': document,
                                  'failure': None}
        self._buffers[run_id] = []
        trigger = json.loads(run['trigger_input']) if run['trigger_input'] else None
        try:
            await self.worker.start()
        except WorkerUnavailable as exc:
            self.store.finish_run(run_id, 'failed', str(exc))
            self._contexts.pop(run_id, None)
            return
        # Record the engine that actually executed this run.
        with self.store.connection() as db:
            db.execute('UPDATE runs SET runtime=? WHERE id=?',
                       (json.dumps({**describe_runtime(self.worker.info),
                                    'catalogVersion': stored['catalog_version']}, sort_keys=True),
                        run_id))
        try:
            result = await self.worker.execute(
                run_id, document, trigger=trigger,
                limits={'deadlineMs': RUN_DEADLINE_MS, 'concurrency': 1},
                approvals=approvals, resume=resume)
        except WorkerUnavailable as exc:
            self._flush(run_id)
            self.store.finish_run(run_id, 'interrupted', str(exc))
            self._contexts.pop(run_id, None)
            return
        finally:
            self._flush(run_id)
        self._settle(run, result)
        self._contexts.pop(run_id, None)
        self._buffers.pop(run_id, None)

    def _settle(self, run, result):
        run_id = run['id']
        status = result.get('status')
        current = self.store.get_run(run_id)
        # Tramo's scheduler reports a completed pass even when a step failed and
        # its branch was skipped. For Vela a failed step is a failed run: saying
        # "finished" over an error would be a false report.
        failure = (self._contexts.get(run_id) or {}).get('failure')
        if status == 'suspended':
            pending = result.get('pending') or []
            for request in pending:
                expires = request.get('expiresAt')
                self.store.record_approval_request(
                    run_id, str(request.get('key') or request.get('nodeId')),
                    str(request.get('nodeId')), str(request.get('message') or 'Approve this step?'),
                    datetime.fromtimestamp(expires / 1000, timezone.utc).isoformat()
                    if expires else None)
            self.store.suspend_run(run_id, result.get('checkpoint'))
            return
        if result.get('cancelled') or current['cancel_requested']:
            self.store.finish_run(run_id, 'cancelled',
                                  'Cancelled. Steps that already finished were not undone.',
                                  result.get('checkpoint'))
            return
        if status == 'timeout':
            self.store.finish_run(run_id, 'timed_out', result.get('error'), result.get('checkpoint'))
            return
        if status == 'interrupted':
            self.store.finish_run(run_id, 'interrupted', result.get('error'), result.get('checkpoint'))
            return
        if failure and result.get('ok'):
            self.store.finish_run(run_id, 'failed', failure, result.get('checkpoint'))
            return
        self.store.finish_run(run_id, 'succeeded' if result.get('ok') else 'failed',
                              result.get('error') or failure, result.get('checkpoint'))

    def _on_event(self, run_id, event):
        buffer = self._buffers.get(run_id)
        if buffer is None:
            return
        context = self._contexts.get(run_id)
        if context is not None:
            if event.get('type') == 'node-error' and not context.get('failure'):
                context['failure'] = str(event.get('error') or 'A step failed.')
            if event.get('type') == 'run-end' and context.get('failure') and event.get('ok'):
                # Keep the stored log consistent with the outcome above.
                event = {**event, 'ok': False, 'error': context['failure']}
        buffer.append(self._redact(event))
        if len(buffer) >= 20 or event.get('type') in ('run-end', 'run-suspended'):
            self._flush(run_id)

    def _flush(self, run_id):
        buffer = self._buffers.get(run_id)
        if not buffer:
            return
        events, self._buffers[run_id] = buffer, []
        try:
            self.store.append_events(run_id, events)
        except Exception as exc:  # noqa: BLE001 - losing a log line must not fail a run
            self._log(f'automations: could not store run events: {exc}')

    @staticmethod
    def _redact(event):
        """Keep the shape of a value out of the log without keeping its content.

        Run logs are stored on disk and shown in the dashboard, so by default
        they describe what a step produced rather than reproducing it. The `log`
        step is the deliberate exception: choosing it is how someone asks to see
        a value.
        """
        event = dict(event)
        if event.get('type') == 'node-success':
            event['output'] = _shape(event.get('output'))
        if event.get('type') == 'node-log' and 'data' in event:
            event['data'] = _preview(event['data'])
        return event

    async def _on_effect(self, message):
        run_id = message.get('runId')
        context = self._contexts.get(run_id)
        if not context:
            raise AppServiceError(409, 'That run is no longer active.')
        run = self.store.get_run(run_id)
        if run['cancel_requested']:
            raise AppServiceError(409, 'This run was cancelled.')
        return await self.effects.perform(
            workflow_id=context['workflowId'], run_id=run_id,
            revision_document=context['document'], kind=message.get('kind'),
            node_id=message.get('nodeId'), payload=message.get('payload') or {})

    # ------------------------------------------------------------ schedules --

    async def _schedule_loop(self):
        while True:
            try:
                await asyncio.sleep(SCHEDULE_TICK_SECONDS)
                self.store.expire_approvals()
                self._dispatch_due()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._log(f'automations: schedule error: {exc}')

    def _dispatch_due(self):
        moment = datetime.now(timezone.utc)
        for schedule in self.store.due_schedules(moment.isoformat()):
            workflow_id = schedule['workflow_id']
            config = json.loads(schedule['config'])
            due = datetime.fromisoformat(schedule['next_due'])
            occurrence = due.isoformat()
            # Skip anything the server slept through instead of firing a burst.
            skipped = schedules.missed_between(config, schedule['timezone'], due, moment)
            following = schedules.next_occurrence(config, schedule['timezone'], moment)
            if not self.store.claim_occurrence(workflow_id, occurrence, following.isoformat()):
                continue
            if skipped:
                self._log(f'automations: skipped {skipped} missed occurrence(s) for {workflow_id}')
            # A run waiting for an approval can wait indefinitely, so a scheduled
            # occurrence is skipped and recorded rather than queued behind it.
            # A run that is merely busy will finish, so its occurrence queues.
            if self.store.live_run(workflow_id, ('waiting',)):
                self.store.record_occurrence(
                    workflow_id, occurrence, None,
                    'skipped: the previous run was waiting for a decision')
                continue
            try:
                run_id = self.store.queue_run(
                    workflow_id, schedule['active_revision'], 'schedule',
                    {'firedAt': occurrence, 'scheduledFor': occurrence,
                     'skippedWhileOffline': skipped},
                    runtime=describe_runtime(self.worker.info), occurrence_id=occurrence)
            except AppServiceError as exc:
                self.store.record_occurrence(workflow_id, occurrence, None, f'skipped: {exc.detail}')
                continue
            self.store.record_occurrence(workflow_id, occurrence, run_id, 'queued')
            self._notify()

    # ------------------------------------------------------------- webhooks --

    def rotate_webhook(self, workflow_id, node_id=None) -> dict:
        workflow = self.store.get_workflow(workflow_id)
        stored = self.store.get_revision(workflow_id,
                                         workflow['active_revision'] or workflow['draft_revision'])
        node = validate.trigger_node(stored['document'])
        if not node or validate.trigger_kind(stored['document']) != 'webhook':
            raise AppServiceError(409, 'This automation does not start from a web request.')
        token_id = uuid.uuid4().hex
        secret = secrets.token_urlsafe(32)
        self.store.save_webhook(workflow_id, node_id or node['id'], token_id,
                                hashlib.sha256(secret.encode()).hexdigest())
        return {'tokenId': token_id, 'secret': secret,
                'path': f'/api/automations/hooks/{token_id}',
                'detail': 'This address works wherever Vela already listens. Vela does not open '
                          'it to the internet; copy the secret now, it is not shown again.'}

    def _webhook_view(self, workflow_id, document):
        if validate.trigger_kind(document) != 'webhook':
            return None
        record = self.store.webhook(workflow_id)
        if not record:
            return {'configured': False,
                    'detail': 'Turn this automation on to get its address and secret.'}
        return {'configured': True, 'tokenId': record['token_id'],
                'path': f'/api/automations/hooks/{record["token_id"]}',
                'createdAt': record['created_at'], 'lastUsedAt': record['last_used_at']}

    def receive_webhook(self, token_id, secret, body: bytes, query: dict) -> dict:
        record = self.store.webhook_by_token(token_id)
        if not record:
            raise AppServiceError(404, 'Unknown automation address.')
        if not secret or not hmac.compare_digest(
                hashlib.sha256(secret.encode()).hexdigest(), record['secret_hash']):
            raise AppServiceError(401, 'This request is not signed with the automation secret.')
        if record['status'] != 'active' or not record['active_revision']:
            raise AppServiceError(409, 'That automation is not turned on.')
        if len(body) > MAX_WEBHOOK_BODY_BYTES:
            raise AppServiceError(413, 'That request body is larger than Vela accepts.')
        parsed = None
        if body:
            try:
                parsed = json.loads(body.decode('utf-8'))
            except (ValueError, UnicodeDecodeError) as exc:
                raise AppServiceError(422, 'The request body must be JSON.') from exc
        body_hash = hashlib.sha256(body or b'').hexdigest()
        if self.store.live_run(record['workflow_id'], ('waiting',)):
            raise AppServiceError(409, 'That automation is waiting for a decision on its current '
                                       'run, so Vela is not starting another one.')
        if not self.store.note_webhook_delivery(token_id, body_hash):
            raise AppServiceError(409, 'Vela already accepted an identical request. '
                                       'Include something unique, such as a request id.')
        run_id = self.store.queue_run(
            record['workflow_id'], record['active_revision'], 'webhook',
            {'body': parsed, 'query': dict(query), 'receivedAt': now()},
            runtime=describe_runtime(self.worker.info))
        self._notify()
        return {'runId': run_id, 'status': 'queued'}

    # ------------------------------------------------------------ approvals --

    def pending_approvals(self) -> dict:
        self.store.expire_approvals()
        return {'approvals': [self._approval_view(item) for item in self.store.pending_approvals()]}

    def _approval_view(self, item):
        return {
            'runId': item['run_id'], 'gateKey': item['gate_key'], 'nodeId': item['node_id'],
            'message': item['message'], 'status': item['status'], 'createdAt': item['created_at'],
            'expiresAt': item['expires_at'], 'decidedAt': item['decided_at'],
            'decidedBy': item['decided_by'], 'comment': item['comment'],
            'workflowId': item.get('workflow_id'), 'workflowName': item.get('workflow_name'),
        }

    def decide(self, run_id, gate_key, approved, comment='') -> dict:
        run = self.store.get_run(run_id)
        if run['status'] != 'waiting':
            raise AppServiceError(409, 'That run is not waiting for a decision.')
        self.store.decide_approval(run_id, gate_key, approved, 'you', comment)
        self._spawn(self._resume(run_id))
        return self.run_detail(run_id)

    async def _resume(self, run_id):
        """Continue a suspended run against the exact revision it started on."""
        run = self.store.get_run(run_id)
        if run['status'] != 'waiting':
            return
        decisions = {}
        for item in self.store.approvals(run_id):
            if item['status'] in ('approved', 'rejected'):
                decisions[item['gate_key']] = {
                    'approved': item['status'] == 'approved',
                    'by': item['decided_by'] or 'you',
                    'comment': item['comment'] or '',
                }
        # Permissions are rechecked from scratch on resume: an approval decides
        # this run, it never widens what the workflow may do.
        try:
            stored = self.store.get_revision(run['workflow_id'], run['revision'])
        except AppServiceError as exc:
            self.store.finish_run(run_id, 'failed', exc.detail)
            return
        for review in self.effects.review(run['workflow_id'], stored['document']):
            if review.get('available') and not review['granted']:
                self.store.finish_run(
                    run_id, 'failed',
                    f'Permission for {review.get("appName", review["app"])} changed while this run '
                    'was waiting, so it stopped instead of continuing.')
                return
        with self.store.connection() as db:
            db.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))
        checkpoint = json.loads(run['checkpoint']) if run['checkpoint'] else None
        await self._execute({**run, 'status': 'running'}, approvals=decisions, resume=checkpoint)


def _running_loop():
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _shape(value, depth=0):
    """Describe a value's structure without copying personal content."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return {'type': 'text', 'length': len(value)}
    if isinstance(value, list):
        return {'type': 'list', 'length': len(value)}
    if isinstance(value, dict):
        if depth >= 2:
            return {'type': 'object', 'keys': sorted(value)[:20]}
        return {key: _shape(item, depth + 1) for key, item in list(value.items())[:20]}
    return {'type': type(value).__name__}


def _preview(value, limit=400):
    """The `log` step's own payload, truncated but readable."""
    try:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return '(value could not be shown)'
    return text if len(text) <= limit else text[:limit] + '…'
