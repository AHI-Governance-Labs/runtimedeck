"""Windows ownership checks using only processes created by these tests."""

import ctypes
import os
import queue
import subprocess
import sys
import tempfile
import unittest
from ctypes import wintypes
from pathlib import Path

from runtime_deck_core.processes import ProcessRunner


@unittest.skipUnless(os.name == "nt", "Windows Job Object ownership")
class ProcessGuardTests(unittest.TestCase):
    def assert_process_exited(self, pid):
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x100000, False, pid)
        if not handle:
            self.assertEqual(ctypes.get_last_error(), 87, "Unexpected process inspection failure")
            return
        try:
            self.assertEqual(api.WaitForSingleObject(handle, 5000), 0, "Owned descendant survived")
        finally:
            api.CloseHandle(handle)

    def run_tree(self, finish):
        events = queue.Queue()
        runner = ProcessRunner(events.put, lambda code: None)
        script = ("import subprocess,sys,time; "
                  "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                  "print(child.pid,flush=True); " + finish)
        runner.start([sys.executable, "-u", "-c", script])
        try:
            child = int(events.get(timeout=8).strip())
            if "sleep(60)" in finish:
                runner.stop()
            runner.thread.join(timeout=8)
            self.assertFalse(runner.running)
            self.assert_process_exited(child)
        finally:
            runner.stop()

    def test_stop_releases_descendants(self):
        self.run_tree("time.sleep(60)")

    def test_parent_exit_releases_descendants_and_stdout(self):
        self.run_tree("time.sleep(0.2)")

    def test_app_crash_releases_owned_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready.txt"
            script = ("import os,sys,time; from pathlib import Path; "
                      "from runtime_deck_core.processes import ProcessRunner; "
                      "runner=ProcessRunner(lambda line: Path(sys.argv[1]).write_text(line),lambda code:None); "
                      "runner.start([sys.executable,'-u','-c',"
                      "'import os,time; print(os.getpid(),flush=True); time.sleep(60)']); "
                      "deadline=time.monotonic()+8\n"
                      "while not Path(sys.argv[1]).exists() and time.monotonic()<deadline: time.sleep(.02)\n"
                      "os._exit(0 if Path(sys.argv[1]).exists() else 2)")
            result = subprocess.run([sys.executable, "-c", script, str(ready)], timeout=12)
            self.assertEqual(result.returncode, 0)
            self.assert_process_exited(int(ready.read_text().strip()))
