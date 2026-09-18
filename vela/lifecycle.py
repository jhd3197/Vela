"""Lifecycle orchestration; HTTP routes only translate transport concerns."""
import socket
import threading
from contextlib import suppress
import time
import shutil
import uuid
from .manifest import load_manifest
from .package_files import replace_dir
from .state import pid_alive


class LifecycleError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def _port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.1)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def _log_tail(path):
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:])
    except OSError:
        return ""


class Lifecycle:
    def __init__(self, config, registry, state, runner, platform, auth, storage, desktops=None):
        self.config, self.registry, self.state = config, registry, state
        self.runner, self.platform = runner, platform
        self.auth, self.storage = auth, storage
        # Optional, because a hub assembled without desktops still installs and
        # removes apps. Present, it is what keeps agent authority from outliving
        # the code it was reviewed against.
        self.desktops = desktops
        self.lock = threading.RLock()
        self.adopt_installations()

    def adopt_installations(self):
        """Give every installed app the identity installing one grants today.

        An installation identity is what an app's stored data, its sessions and
        any window on it are bound to. Installing an app has created one for
        some time — but it did not always, and on an upgraded Vela the apps that
        were already there have none until something happens to start a session
        for them. Anything asking "which installation is this" got `None` for an
        app that is plainly installed, and a window, which binds itself to one
        rather than creating one, could not be opened for those apps at all.

        So it is reconciled once at startup rather than left for a session to
        fix by accident. Only apps whose code is really in `installed/` are
        given one: this must not be a way for a removed app to come back, which
        is the reason `installation` refuses to create one in the first place.
        """
        # Suppressed per app rather than for the whole sweep: one app that
        # cannot be read must not stop the others being adopted, and a hub that
        # cannot reconcile at all is a hub with the gap it already had, not one
        # that fails to start. Every caller still copes with `None`.
        with suppress(Exception):
            for app_id in self.registry.installed_ids():
                with suppress(Exception):
                    if self.storage.installation(app_id) is None:
                        self.storage.activate(app_id)

    def revoke_app_authority(self, app_id, *, reason="that app changed"):
        """Drop everything that was authorized against this app as it was.

        One call site for two things that must not drift apart: the app sessions
        the auth layer issued, and the grants an agent desktop holds. An update
        that ended one and left the other would leave authority reviewed against
        code that is no longer installed.
        """
        self.auth.revoke_app(app_id)
        if self.desktops is not None:
            with suppress(Exception):
                self.desktops.app_changed(app_id, reason=reason)

    def manifest(self, app_id):
        manifest = self.registry.get(app_id)
        if manifest is None:
            raise LifecycleError(404, f"unknown app: {app_id}")
        return manifest

    def install_app(self, app_id):
        with self.lock:
            manifest = self.manifest(app_id)
            if not manifest.supports(self.platform):
                raise LifecycleError(400, f"app {app_id} is not supported on platform: {self.platform}")
            if not self.registry.is_installed(app_id):
                if not (self.config.apps_dir / app_id).is_dir():
                    raise LifecycleError(409, 'Review this catalog release in the Library before installing')
                self.registry.install(app_id)
            self.storage.activate(app_id)
            return {"id": app_id, "installed": True}

    def uninstall_app(self, app_id):
        with self.lock:
            self.manifest(app_id)
            self.stop_app(app_id)
            self.state.clear(app_id)
            self.revoke_app_authority(app_id, reason="that app was removed")
            with self.storage.connection() as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='connections'").fetchone():
                    db.execute("DELETE FROM connections WHERE identity IN (SELECT identity FROM installations WHERE app_id=?)", (app_id,))
                # App data survives uninstall so a reinstall can reattach it.
                # A published desk summary must not: the desk would keep
                # showing a line from an app that is no longer there.
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='widget_summaries'").fetchone():
                    db.execute("DELETE FROM widget_summaries WHERE app_id=?", (app_id,))
            self.storage.deactivate(app_id)
            self.registry.uninstall(app_id)
            return {"id": app_id, "installed": False}

    def upgrade_legacy(self, app_id):
        """One-time bundled v1→v2 adoption, with the original package retained."""
        with self.lock:
            installed = self.manifest(app_id)
            source = self.config.apps_dir / app_id
            replacement = load_manifest(source)
            if not self.registry.is_installed(app_id) or installed.schema_version != 1 or replacement.schema_version != 2 or installed.active_runtime(self.platform) != "web" or replacement.active_runtime(self.platform) != "web":
                raise LifecycleError(400, "Only a bundled static v1-to-v2 migration is supported here")
            staging_root = self.config.data_dir / "staging"
            staging_root.mkdir(exist_ok=True)
            staging = staging_root / str(uuid.uuid4())
            archive = self.config.data_dir / "legacy-packages" / str(uuid.uuid4()) / app_id
            archive.parent.mkdir(parents=True)
            target = self.registry.installed_path(app_id)
            try:
                shutil.copytree(source, staging / app_id)
                load_manifest(staging / app_id)
                replace_dir(target, archive)
                try:
                    replace_dir(staging / app_id, target)
                except Exception:
                    replace_dir(archive, target)
                    raise
                self.revoke_app_authority(app_id, reason="that app was upgraded")
                return {"id": app_id, "upgraded": True, "previousPackage": str(archive)}
            finally:
                shutil.rmtree(staging, ignore_errors=True)

    def launch_app(self, app_id):
        with self.lock:
            self.install_app(app_id)
            manifest = self.manifest(app_id)
            runtime = manifest.active_runtime(self.platform)
            if runtime in ("web", "external", "none"):
                return self.app_status(app_id)
            entry = self.state.get(app_id)
            if entry and pid_alive(entry.get("pid", -1), entry.get("pid_ctime")):
                raise LifecycleError(409, f"app {app_id} is already running")
            self.state.clear(app_id)
            spec = manifest.platform_spec(self.platform)
            if spec is None:
                raise LifecycleError(400, f"app {app_id} is not supported on platform: {self.platform}")
            command, port = spec.run, spec.port
            if port is not None:
                if not _port_available(port):
                    port = _find_free_port()
                command = command.replace("{port}", str(port))
            try:
                pid, ctime = self.runner.launch(app_id, command, self.registry.installed_path(app_id), self.config.logs_dir / f"{app_id}.log")
            except NotImplementedError as exc:
                raise LifecycleError(400, str(exc)) from exc
            if (port is not None and not _wait_for_port(port)) or not pid_alive(pid, ctime):
                if pid_alive(pid, ctime):
                    self.runner.stop(pid)
                raise LifecycleError(502, f"app {app_id} did not become ready within 5s")
            self.state.set(app_id, pid=pid, port=port, pid_ctime=ctime)
            return self.app_status(app_id)

    def stop_app(self, app_id):
        with self.lock:
            manifest = self.manifest(app_id)
            if manifest.active_runtime(self.platform) != "process":
                return {"id": app_id, "running": False}
            entry = self.state.get(app_id)
            if entry and pid_alive(entry.get("pid", -1), entry.get("pid_ctime")):
                self.runner.stop(entry["pid"])
            self.state.clear(app_id)
            return {"id": app_id, "running": False}

    def app_status(self, app_id):
        with self.lock:
            manifest = self.manifest(app_id)
            runtime = manifest.active_runtime(self.platform)
            installed = self.registry.is_installed(app_id)
            entry = self.state.get(app_id) if runtime == "process" else None
            running = bool(entry and pid_alive(entry.get("pid", -1), entry.get("pid_ctime"))) if runtime == "process" else installed
            if entry and not running:
                self.state.clear(app_id)
                entry = None
            port = entry.get("port") if entry else None
            view = manifest.view
            url = None
            if running:
                if view["surface"] == "external":
                    url = view["url"]
                elif view["surface"] == "embedded" and (manifest.web or port):
                    url = f"/apps/{app_id}/"
            return {"id": app_id, "installed": installed, "running": running,
                    "pid": entry.get("pid") if entry else None, "port": port, "url": url,
                    "started_at": entry.get("started_at") if entry else None,
                    "logs": _log_tail(self.config.logs_dir / f"{app_id}.log"),
                    "runtime": runtime, "runtimes": manifest.runtimes(self.platform), "view": view}
