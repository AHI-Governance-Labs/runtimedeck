"""Dataset-driven answer checks against the app-owned local model server."""

import json
import http.client
import re
import select
import socket
import threading
import time
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import messagebox
from urllib.error import URLError
from urllib.request import urlopen
from urllib.parse import urlsplit

from .benchmarks import BenchmarkSession
from .datasets import freeze_corpus


class _CancellableSocket(socket.socket):
    """Poll before reads: closing a socket need not wake Windows select()."""

    def recv_into(self, buffer, nbytes=0, flags=0):
        deadline = time.monotonic() + 180
        while not self.cancel_event.is_set():
            if select.select([self], [], [], 0.2)[0]:
                return super().recv_into(buffer, nbytes, flags)
            if time.monotonic() >= deadline:
                raise TimeoutError("Local runtime response timed out")
        raise OSError("Evaluation cancelled")


def load_cases(path):
    if Path(path).stat().st_size > 16 * 1024 * 1024:
        raise ValueError("A JSONL evaluation dataset must be at most 16 MiB")
    cases = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
                if not isinstance(case, dict) or not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
                    raise ValueError("Each case requires a nonempty prompt")
                if case.get("check") not in ("exact", "contains", "regex", "json"):
                    raise ValueError("check must be exact, contains, regex or json")
                if case["check"] != "json" and not isinstance(case.get("expected"), str):
                    raise ValueError("expected must be a string")
                if case["check"] == "regex":
                    re.compile(case["expected"])
                cases.append(case)
                if len(cases) > 1000:
                    raise ValueError("At most 1000 cases are supported per evaluation")
            except (ValueError, re.error) as exc:
                raise ValueError(f"Dataset line {line_number}: {exc}") from exc
    if not cases or len(cases) > 1000:
        raise ValueError("The dataset must contain 1–1000 JSONL cases")
    return cases


def check_answer(case, answer):
    answer = answer.strip()
    check = case["check"]
    if check == "exact":
        return answer == case["expected"].strip()
    if check == "contains":
        return case["expected"] in answer
    if check == "regex":
        return re.search(case["expected"], answer) is not None
    try:
        value = json.loads(answer)
        expected = case.get("expected")
        return value == expected if "expected" in case else isinstance(value, (dict, list))
    except ValueError:
        return False


class EvaluationController:
    def __init__(self, app):
        self.app = app
        self.running = False
        self.cancel = threading.Event()
        self.thread = None
        self.last_result = None
        self._connection = None
        self._connection_lock = threading.Lock()
        self.dataset = tk.StringVar(app, str(Path(__file__).resolve().parents[1] / "examples" / "quality.jsonl"))
        self.status = tk.StringVar(app, "Use your own JSONL cases for reproducible answer checks.")

    def start(self):
        if self.running or self.app.lab.running or self.app.chat.running or self.app.active_kind not in (None, "server") or (self.app.plan.running and not self.app.plan.dispatching):
            messagebox.showwarning("RuntimeDeck", "Stop the active job before evaluating answers.")
            return
        try:
            values = self.app.collect_settings()
            cases = load_cases(self.dataset.get())
            if self.app.active_kind is None:
                self.app.run_server()
            if self.app.active_kind != "server":
                return
            server = self.app.server_values.copy()
            model = asdict(self.app.server_model)
            runtime = asdict(self.app.server_runtime)
        except (ValueError, OSError) as exc:
            messagebox.showerror("RuntimeDeck", str(exc))
            return
        self.cancel.clear()
        self.last_result = None
        self.running = True
        self.status.set(f"Loading model · {len(cases)} cases queued")
        self.thread = threading.Thread(target=self._worker,
                                       args=(self.dataset.get(), values, server, model, runtime), daemon=True)
        self.thread.start()

    def _worker(self, dataset, values, server, model, runtime):
        session = None
        outcomes = []
        cases = []
        plan_id = self.app.plan.document["id"] if self.app.plan.running else ""
        try:
            frozen = freeze_corpus(dataset, server["root"], self.cancel.is_set)
            cases = load_cases(frozen["path"])
            host = server["server_host"]
            host = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host
            if ":" in host:
                host = f"[{host}]"
            endpoint = f"http://{host}:{server['server_port']}"
            deadline = time.monotonic() + 180
            while not self.cancel.is_set():
                if not self.app.runner.running:
                    raise RuntimeError("The local server exited; inspect OUTPUT.")
                try:
                    with urlopen(endpoint + "/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except (URLError, OSError):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Local engine readiness timed out")
                self.cancel.wait(0.3)
            session = BenchmarkSession(Path(server["root"]), model, runtime, ["POST", endpoint + "/v1/chat/completions"],
                                       {}, settings=values, experiment="answer_quality", label=Path(dataset).name, dataset=frozen)
            session.plan_id = plan_id
            outcomes = []
            for index, case in enumerate(cases):
                if self.cancel.is_set():
                    break
                self.app.msgq.put(("evaluation_status", f"Evaluating {index + 1}/{len(cases)}"))
                request = {"model": model["name"], "messages": [{"role": "user", "content": case["prompt"]}],
                           "temperature": values["temp"], "seed": values["seed"], "max_tokens": values["max_tokens"],
                           "top_p": values["top_p"], "top_k": values["top_k"], "stream": False,
                           "chat_template_kwargs": {"enable_thinking": False}}
                started = time.monotonic()
                result = self._request_json(endpoint, request)
                answer = result["choices"][0]["message"].get("content") or ""
                if not isinstance(answer, str):
                    raise ValueError("The runtime returned non-text answer content")
                outcome = {"case": case, "answer": answer, "passed": check_answer(case, answer),
                           "elapsed_seconds": time.monotonic() - started, "usage": result.get("usage", {}),
                           "timings": result.get("timings", {})}
                outcomes.append(outcome)
                self.app.msgq.put(("evaluation_log", f"[{index + 1}/{len(cases)}] {'PASS' if outcome['passed'] else 'FAIL'} · {case.get('name', case['prompt'][:50])}\n"))
            session.output.append(json.dumps(outcomes, ensure_ascii=False, indent=2))
            session.extra_metrics = {"cases_total": len(cases), "cases_completed": len(outcomes),
                                     "cases_passed": sum(outcome["passed"] for outcome in outcomes),
                                     "quality_pass_pct": 100 * sum(outcome["passed"] for outcome in outcomes) / len(cases)}
            session.status = "cancelled" if self.cancel.is_set() else "success"
            path = session.save(0 if not self.cancel.is_set() else -1)
            self.app.msgq.put(("evaluation_done", (path, session.extra_metrics, None)))
        except (OSError, ValueError, RuntimeError, KeyError, IndexError, TypeError, AttributeError,
                http.client.HTTPException) as exc:
            path = None
            if session is not None:
                session.output.append(json.dumps(outcomes, ensure_ascii=False, indent=2))
                session.output.append(f"\n[error] {exc}")
                session.status = "cancelled" if self.cancel.is_set() else "failed"
                session.extra_metrics = {"cases_total": len(cases), "cases_completed": len(outcomes),
                                         "cases_passed": sum(outcome["passed"] for outcome in outcomes)}
                try:
                    path = session.save(-1)
                except OSError:
                    pass
            self.app.msgq.put(("evaluation_done", (path, session.extra_metrics if session else {}, str(exc))))

    def _request_json(self, endpoint, payload):
        """Keep the owned socket reachable while waiting for headers or content."""
        target = urlsplit(endpoint)
        connection = http.client.HTTPConnection(target.hostname, target.port, timeout=180)
        with self._connection_lock:
            self._connection = connection
        try:
            if self.cancel.is_set():
                raise RuntimeError("Evaluation cancelled")
            connection.connect()
            original = connection.sock
            connection.sock = _CancellableSocket(original.family, original.type, original.proto,
                                                  fileno=original.detach())
            connection.sock.cancel_event = self.cancel
            connection.sock.settimeout(180)
            connection.request("POST", "/v1/chat/completions", body=json.dumps(payload).encode("utf-8"),
                               headers={"Content-Type": "application/json"})
            if self.cancel.is_set():
                raise RuntimeError("Evaluation cancelled")
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f"Local runtime HTTP error {response.status}: {response.read(4096).decode('utf-8', errors='replace')}")
            return json.load(response)
        finally:
            connection.close()
            with self._connection_lock:
                if self._connection is connection:
                    self._connection = None

    def event(self, kind, payload):
        if kind == "evaluation_status":
            self.status.set(str(payload))
        elif kind == "evaluation_log":
            self.app.log.insert("end", str(payload))
        else:
            self.running = False
            path, metrics, error = payload
            self.last_result = {"artifact": str(path) if path else None, "metrics": metrics,
                                "error": error, "cancelled": self.cancel.is_set()}
            self.status.set(error or f"{'Cancelled' if self.cancel.is_set() else 'Finished'} · {metrics['cases_passed']}/{metrics['cases_total']} passed ({metrics['quality_pass_pct']:.1f}%)")
            if path:
                self.app.log.insert("end", f"[answer evaluation saved] {path}\n")
            self.app.lab.refresh_history()

    def stop(self):
        self.cancel.set()
        with self._connection_lock:
            connection = self._connection
            if connection is not None and connection.sock is not None:
                try:
                    connection.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
