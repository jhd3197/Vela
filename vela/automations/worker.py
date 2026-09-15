"""Supervision of the private Node worker that executes workflow revisions.

Vela owns the worker's whole lifetime. It starts hidden, speaks a versioned line
protocol over its own pipes, receives no Vela credential and no network address,
and is stopped when Vela stops. If it dies, the runs it was carrying are reported
as interrupted rather than quietly retried: only operations with a proven
idempotency key are ever repeated.
"""
import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..config import REPO_ROOT

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1024 * 1024
START_TIMEOUT_SECONDS = 30
STOP_TIMEOUT_SECONDS = 5

#: The worker exits on its own if Vela goes quiet for longer than its own limit,
#: so a force-killed server cannot leave it running. Ping comfortably inside it.
HEARTBEAT_SECONDS = 25

#: Where a packaged server keeps its bundled Node runtime and worker sources.
BUNDLED_NODE_DIR = 'node-runtime'
WORKER_DIR = Path('scripts/automation-worker')


class WorkerUnavailable(RuntimeError):
    """The automation runtime is missing or refused to start."""


@dataclass(frozen=True)
class WorkerInfo:
    node: str
    tramo: str
    spec: str
    protocol: int
    nodes: tuple[str, ...]

    def as_dict(self):
        return {'node': self.node, 'tramo': self.tramo, 'spec': self.spec,
                'protocol': self.protocol, 'nodes': list(self.nodes)}


def node_executable() -> str | None:
    """The Node runtime to use: an explicit override, the bundled copy, then PATH."""
    override = os.environ.get('VELA_AUTOMATION_NODE')
    if override:
        return override if Path(override).is_file() else None
    name = 'node.exe' if platform.system() == 'Windows' else 'node'
    bundled = REPO_ROOT / BUNDLED_NODE_DIR / name
    if bundled.is_file():
        return str(bundled)
    if platform.system() != 'Windows':
        bundled_bin = REPO_ROOT / BUNDLED_NODE_DIR / 'bin' / name
        if bundled_bin.is_file():
            return str(bundled_bin)
    return shutil.which('node')


def worker_script() -> Path:
    return REPO_ROOT / WORKER_DIR / 'src/index.mjs'


def provenance() -> dict:
    path = REPO_ROOT / WORKER_DIR / 'provenance.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def availability() -> dict:
    """A plain description of whether automations can run on this computer."""
    script = worker_script()
    node = node_executable()
    installed = (REPO_ROOT / WORKER_DIR / 'node_modules/@tramo/runtime').is_dir()
    if not script.is_file():
        detail = 'The automation runtime was not installed with this copy of Vela.'
    elif node is None:
        detail = ('Vela could not find its bundled automation runtime. Reinstall the Vela '
                  'download, or install Node.js 20 or later.')
    elif not installed:
        detail = ('The automation runtime has no workflow engine installed. Run '
                  'python scripts/setup-automation-worker.py from a Vela checkout.')
    else:
        detail = None
    return {'available': detail is None, 'detail': detail,
            'node': node, 'provenance': provenance()}


class Worker:
    """One supervised worker process, restarted on demand."""

    def __init__(self, *, on_event, on_effect, log=None):
        self._on_event = on_event
        self._on_effect = on_effect
        self._log = log or (lambda message: None)
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future] = {}
        self._ready: asyncio.Future | None = None
        self._counter = 0
        self._stopping = False
        self._heartbeat: asyncio.Task | None = None
        self.info: WorkerInfo | None = None

    # ---------------------------------------------------------- lifecycle --

    async def start(self):
        async with self._lock:
            if self._process and self._process.returncode is None:
                return self.info
            await self._spawn()
            return self.info

    async def _spawn(self):
        state = availability()
        if not state['available']:
            raise WorkerUnavailable(state['detail'])
        creation = 0
        if platform.system() == 'Windows':
            # Keep the console window hidden; Vela may be running from the tray.
            creation = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        script = worker_script()
        self._ready = asyncio.get_running_loop().create_future()
        try:
            self._process = await asyncio.create_subprocess_exec(
                state['node'], str(script),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(script.parent.parent),
                creationflags=creation,
                # A minimal environment: no Vela token, no data directory, no proxy.
                env={'PATH': os.environ.get('PATH', ''), 'NODE_ENV': 'production',
                     'SystemRoot': os.environ.get('SystemRoot', ''),
                     'NODE_OPTIONS': '--max-old-space-size=256',
                     **({'VELA_WORKER_SILENCE_MS': os.environ['VELA_WORKER_SILENCE_MS']}
                        if 'VELA_WORKER_SILENCE_MS' in os.environ else {})},
            )
        except OSError as exc:
            raise WorkerUnavailable(f'Vela could not start the automation runtime: {exc}') from exc
        self._stopping = False
        self._reader_task = asyncio.create_task(self._read_loop())
        asyncio.create_task(self._drain_stderr())
        try:
            self.info = await asyncio.wait_for(self._ready, START_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            await self._terminate()
            raise WorkerUnavailable('The automation runtime did not start in time.') from exc
        if self.info.protocol != PROTOCOL_VERSION:
            await self._terminate()
            raise WorkerUnavailable(
                f'The automation runtime speaks protocol {self.info.protocol}; this Vela '
                f'expects {PROTOCOL_VERSION}. Reinstall the Vela download.')
        self._heartbeat = asyncio.create_task(self._heartbeat_loop())
        self._log(f'automation runtime ready (node {self.info.node}, tramo {self.info.tramo})')

    async def _heartbeat_loop(self):
        """Tell the worker Vela is still here, so a killed server leaves no orphan."""
        try:
            while self.running:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                if not self.running:
                    return
                await self.ping(timeout=10)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a missed beat must not kill the loop
            return

    async def _drain_stderr(self):
        process = self._process
        if not process or not process.stderr:
            return
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    return
                self._log('worker: ' + line.decode('utf-8', 'replace').rstrip())
        except (asyncio.CancelledError, ValueError):
            return

    async def _read_loop(self):
        process = self._process
        assert process and process.stdout
        stream = process.stdout
        stream._limit = MAX_MESSAGE_BYTES * 2  # Refuse to buffer an unbounded line.
        try:
            while True:
                try:
                    line = await stream.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    self._log('worker sent an oversized message; restarting it')
                    break
                if not line:
                    break
                text = line.decode('utf-8', 'replace').strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    self._log('worker sent a line that was not JSON')
                    continue
                if not isinstance(message, dict) or message.get('v') != PROTOCOL_VERSION:
                    self._log('worker sent a message with an unsupported protocol version')
                    continue
                self._dispatch(message)
        except asyncio.CancelledError:
            raise
        finally:
            await self._fail_pending('The automation runtime stopped before this run finished.')

    def _dispatch(self, message):
        kind = message.get('t')
        if kind == 'ready':
            if self._ready and not self._ready.done():
                self._ready.set_result(WorkerInfo(
                    node=str(message.get('node', 'unknown')),
                    tramo=str(message.get('tramo', 'unknown')),
                    spec=str(message.get('spec', 'unknown')),
                    protocol=int(message.get('protocol', 0)),
                    nodes=tuple(message.get('nodes', []))))
            return
        if kind == 'event':
            self._on_event(message['runId'], message['event'])
            return
        if kind == 'effect':
            asyncio.create_task(self._handle_effect(message))
            return
        if kind in ('result', 'pong', 'cancelled'):
            future = self._pending.pop(message.get('id', ''), None)
            if future and not future.done():
                future.set_result(message)
            return
        if kind == 'fatal':
            self._log(f'worker reported a fatal error: {message.get("error")}')
            return
        if kind == 'protocol-error':
            self._log(f'worker rejected a message: {message.get("detail")}')

    async def _handle_effect(self, message):
        try:
            output = await self._on_effect(message)
            reply = {'v': PROTOCOL_VERSION, 't': 'effect-result', 'id': message['id'],
                     'ok': True, 'output': output}
        except Exception as exc:  # noqa: BLE001 - every failure becomes a node error
            detail = getattr(exc, 'detail', None) or str(exc) or 'Vela refused this step.'
            reply = {'v': PROTOCOL_VERSION, 't': 'effect-result', 'id': message['id'],
                     'ok': False, 'error': str(detail)[:500]}
        await self._write(reply)

    async def _write(self, message):
        process = self._process
        if not process or process.returncode is not None or not process.stdin:
            raise WorkerUnavailable('The automation runtime is not running.')
        line = json.dumps(message, separators=(',', ':')).encode('utf-8') + b'\n'
        if len(line) > MAX_MESSAGE_BYTES:
            raise WorkerUnavailable('This workflow is too large to send to the automation runtime.')
        process.stdin.write(line)
        await process.stdin.drain()

    async def _fail_pending(self, detail):
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result({'t': 'result', 'ok': False, 'status': 'interrupted',
                                   'error': detail, 'pending': [], 'checkpoint': None})
        self._pending.clear()
        if self._ready and not self._ready.done():
            self._ready.set_exception(WorkerUnavailable(detail))

    async def _terminate(self):
        process = self._process
        self._process = None
        if self._heartbeat:
            self._heartbeat.cancel()
            self._heartbeat = None
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        if not process or process.returncode is not None:
            return
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            await asyncio.wait_for(process.wait(), STOP_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass

    async def stop(self):
        """Ask the worker to finish, then make sure it is gone."""
        self._stopping = True
        process = self._process
        if not process or process.returncode is not None:
            await self._terminate()
            return
        try:
            await self._write({'v': PROTOCOL_VERSION, 't': 'shutdown'})
            await asyncio.wait_for(process.wait(), STOP_TIMEOUT_SECONDS)
            self._process = None
            if self._heartbeat:
                self._heartbeat.cancel()
                self._heartbeat = None
            if self._reader_task:
                self._reader_task.cancel()
                self._reader_task = None
        except (WorkerUnavailable, asyncio.TimeoutError, OSError):
            await self._terminate()

    @property
    def running(self) -> bool:
        return bool(self._process and self._process.returncode is None)

    # ------------------------------------------------------------ requests --

    def _next_id(self) -> str:
        self._counter += 1
        return f'h{self._counter}'

    async def execute(self, run_id, document, *, trigger=None, limits=None, approvals=None,
                      resume=None):
        """Run one revision and wait for its final result message."""
        await self.start()
        request_id = self._next_id()
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({
                'v': PROTOCOL_VERSION, 't': 'run', 'id': request_id, 'runId': run_id,
                'doc': document, 'trigger': trigger, 'limits': limits or {},
                'approvals': approvals or None, 'resume': resume or None,
            })
        except WorkerUnavailable:
            self._pending.pop(request_id, None)
            raise
        return await future

    async def cancel(self, run_id):
        if not self.running:
            return False
        request_id = self._next_id()
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({'v': PROTOCOL_VERSION, 't': 'cancel', 'id': request_id,
                               'runId': run_id})
            message = await asyncio.wait_for(future, 5)
        except (WorkerUnavailable, asyncio.TimeoutError, OSError):
            self._pending.pop(request_id, None)
            return False
        return bool(message.get('found'))

    async def ping(self, timeout=5):
        if not self.running:
            return False
        request_id = self._next_id()
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({'v': PROTOCOL_VERSION, 't': 'ping', 'id': request_id})
            await asyncio.wait_for(future, timeout)
        except (WorkerUnavailable, asyncio.TimeoutError, OSError):
            self._pending.pop(request_id, None)
            return False
        return True


def describe_runtime(info: WorkerInfo | None) -> dict:
    """What a run records about the engine that executed it."""
    return {
        'worker': info.as_dict() if info else None,
        'python': sys.version.split()[0],
        'platform': platform.system().lower(),
    }
