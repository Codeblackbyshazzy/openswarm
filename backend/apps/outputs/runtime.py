"""Per-workspace persistent backend runtime.

Each App (workspace) has at most one long-running `backend.py` subprocess
managed by `AppRuntime`. Lifetime is reference-counted via the module-level
`manager` singleton: when the first ViewEditor / DashboardViewCard /
TerminalPanel attaches to a workspace, the process is spawned; when the
last detaches, it's terminated. Multiple subscribers share the same
process and the same in-memory log ring buffer.

This replaces the old one-shot `execute_backend_code` model for the
"backend serves real HTTP endpoints" use case. The one-shot path stays
around (see `executor.py`) for legacy `/api/outputs/execute` callers.
"""

import asyncio
import logging
import os
import socket
import sys
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Recent log lines kept in memory per runtime. Lets a Terminal tab that
# opens mid-session replay the context that was already printed instead
# of seeing a blank pane. 2000 lines ≈ a few hundred KB at worst —
# bounded and predictable.
_LOG_BUFFER_LINES = 2000

# Seconds to wait after SIGTERM before escalating to SIGKILL. Most
# well-behaved Python servers shut down well under a second; this is the
# upper bound before we move on so a wedged process can't block a
# workspace tear-down forever.
_TERMINATE_GRACE_SECONDS = 3


def _find_free_port() -> int:
    """Ask the kernel for an unused localhost port. There's a tiny race
    between this socket closing and the backend re-binding, but we hand
    each port to exactly one runtime so no caller competes for it, and
    the kernel won't immediately recycle a freshly-closed port anyway."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class LogLine:
    stream: str  # "stdout" | "stderr" | "runtime" (internal status lines)
    text: str


LogSubscriber = Callable[[LogLine], None]


class AppRuntime:
    """Manages one workspace's backend.py subprocess.

    - `port` is None until start() runs; it's set even if backend.py
      doesn't exist (no-op start returns False but the runtime still
      exists so the Terminal pane has a host for [FRONTEND] capture).
    - `running` is True only while the process is alive. Goes False on
      exit, and we surface a "[runtime] backend exited" line so the
      Terminal pane shows it.
    - `log_buffer` is the replay source for new subscribers.
    """

    def __init__(self, workspace_id: str, workspace_path: str):
        self.workspace_id = workspace_id
        self.workspace_path = workspace_path
        self.port: Optional[int] = None
        self.process: Optional[asyncio.subprocess.Process] = None
        self.log_buffer: deque[LogLine] = deque(maxlen=_LOG_BUFFER_LINES)
        self._subscribers: set[LogSubscriber] = set()
        self._stdout_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._wait_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    @property
    def has_backend_file(self) -> bool:
        return os.path.exists(os.path.join(self.workspace_path, "backend.py"))

    async def start(self) -> bool:
        """Spawn backend.py if it exists. Returns True if a process is
        running after this call. False means the workspace has no
        backend.py (legitimate — pure-frontend apps) or the spawn failed
        (an error line is emitted to the log buffer in that case)."""
        async with self._lock:
            if self.running:
                return True
            if not self.has_backend_file:
                self.port = None
                return False
            self.port = _find_free_port()
            # Strip the install token before handing env to user code.
            # Backend.py can hit our REST API back via its own creds if
            # it really needs to, but it shouldn't inherit the host
            # process's token by default.
            env = {k: v for k, v in os.environ.items() if k != "OPENSWARM_AUTH_TOKEN"}
            env["PORT"] = str(self.port)
            env["BACKEND_PORT"] = str(self.port)  # alias — both common names work
            try:
                # -u forces unbuffered stdout/stderr so the Terminal pane
                # sees lines in real time, not whenever Python decides to
                # flush its block buffer.
                self.process = await asyncio.create_subprocess_exec(
                    sys.executable, "-u", "backend.py",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.workspace_path,
                    env=env,
                )
            except Exception as e:
                logger.exception("failed to start backend for %s", self.workspace_id)
                self._broadcast(LogLine("runtime", f"[runtime] failed to start: {e}"))
                self.port = None
                self.process = None
                return False
            self._broadcast(LogLine("runtime", f"[runtime] backend started on port {self.port} (pid {self.process.pid})"))
            self._stdout_task = asyncio.create_task(self._pipe_stream(self.process.stdout, "stdout"))
            self._stderr_task = asyncio.create_task(self._pipe_stream(self.process.stderr, "stderr"))
            self._wait_task = asyncio.create_task(self._await_exit())
            return True

    async def stop(self) -> None:
        async with self._lock:
            if not self.process or self.process.returncode is not None:
                return
            try:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=_TERMINATE_GRACE_SECONDS)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            except ProcessLookupError:
                pass

    async def restart(self) -> bool:
        await self.stop()
        return await self.start()

    def subscribe(self, cb: LogSubscriber) -> Callable[[], None]:
        """Register a log subscriber. Immediately replays the ring buffer
        so a Terminal pane that opens mid-session shows context. Returns
        an unsubscribe function."""
        self._subscribers.add(cb)
        for line in list(self.log_buffer):
            try:
                cb(line)
            except Exception:
                pass

        def _unsub() -> None:
            self._subscribers.discard(cb)

        return _unsub

    def _broadcast(self, line: LogLine) -> None:
        self.log_buffer.append(line)
        # Snapshot subscribers — they can self-remove during dispatch.
        for cb in list(self._subscribers):
            try:
                cb(line)
            except Exception:
                pass

    async def _pipe_stream(self, stream: Optional[asyncio.StreamReader], name: str) -> None:
        if stream is None:
            return
        try:
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                text = raw.decode(errors="replace").rstrip("\r\n")
                if text:
                    self._broadcast(LogLine(name, text))
        except Exception:
            logger.exception("log pipe error (%s) for %s", name, self.workspace_id)

    async def _await_exit(self) -> None:
        if not self.process:
            return
        rc = await self.process.wait()
        self._broadcast(LogLine("runtime", f"[runtime] backend exited with code {rc}"))


class AppRuntimeManager:
    """Per-process singleton tracking all live AppRuntime instances.

    Reference-counts attachments so we don't kill a backend when one
    Terminal closes while another is still subscribed. The first attach
    spawns, the last detach stops."""

    def __init__(self) -> None:
        self.runtimes: dict[str, AppRuntime] = {}
        self._attached: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def attach(self, workspace_id: str, workspace_path: str) -> AppRuntime:
        async with self._lock:
            rt = self.runtimes.get(workspace_id)
            if rt is None:
                rt = AppRuntime(workspace_id, workspace_path)
                self.runtimes[workspace_id] = rt
            else:
                # Workspace paths shouldn't change for a given id, but if
                # somehow they did (e.g. the user moved the workspace
                # folder), trust the latest caller — they have the
                # current truth.
                rt.workspace_path = workspace_path
            self._attached[workspace_id] = self._attached.get(workspace_id, 0) + 1
        if not rt.running:
            await rt.start()
        return rt

    async def detach(self, workspace_id: str) -> None:
        async with self._lock:
            count = self._attached.get(workspace_id, 0) - 1
            if count > 0:
                self._attached[workspace_id] = count
                return
            self._attached.pop(workspace_id, None)
            rt = self.runtimes.pop(workspace_id, None)
        if rt:
            await rt.stop()

    def get(self, workspace_id: str) -> Optional[AppRuntime]:
        return self.runtimes.get(workspace_id)

    async def restart(self, workspace_id: str, workspace_path: Optional[str] = None) -> Optional[AppRuntime]:
        rt = self.runtimes.get(workspace_id)
        if rt is None:
            return None
        if workspace_path:
            rt.workspace_path = workspace_path
        await rt.restart()
        return rt


manager = AppRuntimeManager()
