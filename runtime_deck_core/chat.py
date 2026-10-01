"""Local streaming chat against the llama-server process owned by RuntimeDeck."""

import json
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def sse_events(response):
    """Read standard SSE data frames, not arbitrary chunks of UTF-8 bytes."""
    data = []
    for raw in response:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                payload = "\n".join(data)
                data.clear()
                if payload == "[DONE]":
                    return
                yield json.loads(payload)
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data and "\n".join(data) != "[DONE]":
        yield json.loads("\n".join(data))


class ChatController:
    def __init__(self, app):
        self.app = app
        self.running = False
        self.messages = []
        self.system = tk.StringVar(app, "Eres un asistente útil. Responde en español salvo que se solicite otro idioma.")
        self.thinking = tk.BooleanVar(app, False)
        self.status = tk.StringVar(app, "Send a message to load the selected model into the local engine.")
        self.cancel = threading.Event()
        self.response = None
        self.thread = None
        self.root = None
        self.model = None

    def send(self):
        if self.app.plan.running:
            messagebox.showwarning("Plan", "Detén el plan antes de conversar")
            return
        if self.running:
            return
        prompt = self.app.chat_panel.input.get("1.0", "end").strip()
        if not prompt:
            return
        if self.app.lab.running or self.app.evaluation.running or self.app.active_kind not in (None, "server"):
            messagebox.showwarning("RuntimeDeck", "Stop the active experiment/inference before using Chat.")
            return
        try:
            values = self.app.collect_settings()
            if self.app.active_kind is None:
                self.app.run_server()
            if self.app.active_kind != "server":
                return
            server = self.app.server_values.copy()
            self.model = self.app.server_model
            self.root = Path(server["root"])
        except (ValueError, OSError, AttributeError) as exc:
            messagebox.showerror("RuntimeDeck", str(exc))
            return
        host = server["server_host"]
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1" if host == "0.0.0.0" else "::1"
        if ":" in host:
            host = f"[{host}]"
        endpoint = f"http://{host}:{server['server_port']}"
        pending = self.messages + [{"role": "user", "content": prompt}]
        messages = ([{"role": "system", "content": self.system.get()}] if self.system.get().strip() else []) + pending
        request = {"model": self.model.name, "messages": messages, "stream": True,
                   "max_tokens": values["max_tokens"], "temperature": values["temp"],
                   "top_p": values["top_p"], "top_k": values["top_k"], "seed": values["seed"],
                   "chat_template_kwargs": {"enable_thinking": self.thinking.get()}}
        self.running = True
        self.cancel.clear()
        self.app.chat_panel.append(f"\nYOU\n{prompt}\n\nASSISTANT\n")
        self.app.chat_panel.input.delete("1.0", "end")
        self.status.set(f"Loading/waiting for {self.model.name}...")
        self.thread = threading.Thread(target=self._worker, args=(endpoint, request, pending), daemon=True)
        self.thread.start()

    def _worker(self, endpoint, request, pending):
        started = time.monotonic()
        first_token = None
        content = []
        usage = {}
        error = ""
        try:
            deadline = started + 180
            while not self.cancel.is_set():
                if not self.app.runner.running:
                    raise RuntimeError("The local server exited. Inspect OUTPUT for the loading error.")
                try:
                    with urlopen(endpoint + "/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except (OSError, URLError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("The local server did not become ready within 180 seconds.")
                self.cancel.wait(0.3)
            if self.cancel.is_set():
                return
            ready = time.monotonic()
            self.app.msgq.put(("chat_status", f"Generating with {self.model.name}..."))
            http_request = Request(endpoint + "/v1/chat/completions", data=json.dumps(request).encode("utf-8"),
                                   headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(http_request, timeout=60) as response:
                self.response = response
                for event in sse_events(response):
                    if self.cancel.is_set():
                        break
                    if "error" in event:
                        raise RuntimeError(str(event["error"]))
                    usage.update(event.get("usage") or {})
                    choices = event.get("choices") or []
                    delta = choices[0].get("delta", {}) if choices else {}
                    text = delta.get("content") or ""
                    reasoning = delta.get("reasoning_content") or ""
                    if text or reasoning:
                        if first_token is None:
                            first_token = time.monotonic() - ready
                        if text:
                            content.append(text)
                        self.app.msgq.put(("chat_text", reasoning or text))
        except HTTPError as exc:
            error = f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:1000]}"
        except (OSError, ValueError, RuntimeError) as exc:
            error = str(exc)
        finally:
            self.response = None
            self.app.msgq.put(("chat_done", {"pending": pending, "content": "".join(content),
                               "elapsed": time.monotonic() - started, "ttft": first_token,
                               "usage": usage, "error": error, "cancelled": self.cancel.is_set()}))

    def event(self, kind, payload):
        if kind == "chat_text":
            self.app.chat_panel.append(str(payload))
        elif kind == "chat_status":
            self.status.set(str(payload))
        elif kind == "chat_done":
            self.running = False
            if payload["error"]:
                self.app.chat_panel.append(f"\n[error] {payload['error']}\n")
            if payload["content"] and not payload["error"]:
                self.messages = payload["pending"] + [{"role": "assistant", "content": payload["content"]}]
            ttft = f" · first token {payload['ttft']:.2f}s" if payload["ttft"] is not None else ""
            self.status.set(f"{'Cancelled' if payload['cancelled'] else 'Error' if payload['error'] else 'Finished'} · {payload['elapsed']:.2f}s{ttft}")
            self.app.chat_panel.append("\n")

    def stop(self):
        self.cancel.set()
        response = self.response
        if response is not None:
            threading.Thread(target=response.close, daemon=True).start()

    def clear(self):
        if self.running:
            return
        self.messages.clear()
        self.app.chat_panel.clear()

    def save(self):
        if self.root is None or not self.messages:
            return
        try:
            directory = self.root / "conversations"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{datetime.now():%Y%m%d-%H%M%S-%f}.json"
            path.write_text(json.dumps({"model": self.model.path, "system": self.system.get(),
                                       "messages": self.messages}, indent=2, ensure_ascii=False), encoding="utf-8")
            self.status.set(f"Conversation saved: {path.name}")
        except OSError as exc:
            messagebox.showerror("RuntimeDeck", str(exc))
