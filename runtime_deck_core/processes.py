"""Thread-safe process lifecycle with nonblocking cancellation."""

import subprocess
import threading
from typing import Callable

from .config import CREATE_NO_WINDOW
from .process_guard import ProcessGuard


class ProcessRunner:
    def __init__(self, emit: Callable[[str], None], done: Callable[[int], None]):
        self.emit = emit
        self.done = done
        self.proc = None
        self.thread = None
        self._lock = threading.Lock()
        self._active = False
        self._cancel = threading.Event()

    @property
    def running(self):
        with self._lock:
            return self._active

    def start(self, argv, cwd=None, env=None):
        with self._lock:
            if self._active:
                raise RuntimeError("A process is already running.")
            self._active = True
            self._cancel.clear()
        self.thread = threading.Thread(target=self._worker, args=(list(argv), cwd, env), daemon=True)
        try:
            self.thread.start()
        except Exception:
            with self._lock:
                self._active = False
            raise

    def _worker(self, argv, cwd, env):
        code = -1
        guard = None
        try:
            guard = ProcessGuard()
            with subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  text=True, encoding="utf-8", errors="replace", bufsize=1,
                                  creationflags=CREATE_NO_WINDOW) as process:
                guard.assign(process)
                # A child may inherit stdout. Release the tree when its owner exits
                # so that the output reader also receives EOF.
                def release_tree():
                    process.wait()
                    guard.close()
                threading.Thread(target=release_tree, daemon=True).start()
                with self._lock:
                    self.proc = process
                if self._cancel.is_set():
                    self._schedule_termination(process)
                for line in process.stdout:
                    self.emit(line)
                code = process.wait()
        except Exception as exc:
            self.emit(f"\n[RuntimeDeck error] {exc}\n")
        finally:
            if guard is not None:
                guard.close()
            # Queue completion before admitting another run: output cannot cross sessions.
            with self._lock:
                self.proc = None
                try:
                    self.done(code)
                finally:
                    self._active = False

    def stop(self):
        self._cancel.set()
        with self._lock:
            process = self.proc
        if process is not None:
            self._schedule_termination(process)

    @staticmethod
    def _schedule_termination(process):
        def terminate():
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            except OSError:
                pass
        threading.Thread(target=terminate, daemon=True).start()
