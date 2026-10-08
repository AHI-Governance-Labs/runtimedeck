"""Real server/UI verification with independent HTTP clients and temporary preferences."""

import argparse
import json
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime_deck_core.app import RuntimeDeck
from runtime_deck_core.chat import sse_events
from runtime_deck_core.config import DEFAULT_ROOT
from runtime_deck_core.settings import DEFAULTS, SettingsStore
from verify_local import capture_window


def http_json(url, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=90) as response:
        return json.load(response)


def stream_chat(url, body):
    request = Request(url, data=json.dumps(dict(body, stream=True)).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=90) as response:
        events = list(sse_events(response))
    if not events or any("error" in event for event in events):
        raise AssertionError(f"Streaming did not succeed: {events}")
    return events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=str(DEFAULT_ROOT))
    parser.add_argument("--model", help="Exact GGUF path; otherwise choose the smallest runnable model")
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / "verification" / "server"
    output.mkdir(parents=True, exist_ok=True)
    with socket.socket() as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        port = port_probe.getsockname()[1]
    values = dict(DEFAULTS, root=args.workspace, ctx=1024, threads=4, batch=256, ubatch=128,
                  server_port=port, server_parallel=1)
    errors = []
    report = {"success": False, "temporary_port": port}

    def failed(title, message, **_):
        raise RuntimeError(f"{title}: {message}")

    with patch.object(SettingsStore, "load", return_value=values), patch("tkinter.messagebox.showerror", side_effect=failed):
        app = RuntimeDeck()
        app.settings_store = SettingsStore(output / "temporary-preferences.json")
        app.report_callback_exception = lambda exc_type, exc, traceback: errors.append(str(exc))

        def pump(predicate, timeout=180):
            deadline = time.monotonic() + timeout
            while not predicate():
                app.update()
                if errors:
                    raise AssertionError(f"Tk errors: {errors}")
                if time.monotonic() >= deadline:
                    raise TimeoutError(app.server_connection.status.get())
                time.sleep(0.02)
            app.update()

        try:
            pump(lambda: bool(app.models) and bool(app.runtimes), timeout=30)
            models = [model for model in app.models if model.runnable]
            if args.model:
                models = [model for model in models if Path(model.path).resolve() == Path(args.model).resolve()]
            if not models:
                raise AssertionError("No runnable GGUF matched the requested model")
            model = min(models, key=lambda item: item.size)
            runtime = next(item for item in app.runtimes if item.server)
            app.select_inventory(model, runtime)
            report["inventory"] = {"workspace": app.root_var.get(), "models": [asdict(item) for item in app.models],
                                   "runtimes": [asdict(item) for item in app.runtimes]}
            if app.workspace_notebook.select() != str(app.server_tab) or not app.server_panel.winfo_viewable():
                raise AssertionError("Server panel is not the visible default tab")
            if not app.model_tree.winfo_viewable() or not app.runtime_tree.winfo_viewable():
                raise AssertionError("Real inventory is not visible")
            if not app.log.winfo_viewable() or app.log.winfo_rooty() + app.log.winfo_height() > app.winfo_rooty() + app.winfo_height():
                raise AssertionError("Output console is not visibly contained in the window")
            app.run_server()
            if app.active_kind != "server":
                raise AssertionError("Server did not start")
            report["command"] = app.build_server()
            report["version"] = subprocess.check_output([runtime.server, "--version"], text=True,
                                                         stderr=subprocess.STDOUT, timeout=10).strip()
            print(f"Loading {model.name} on temporary port {port}...", flush=True)
            pump(lambda: bool(app.server_connection.model_ids) or app.active_kind is None)
            if app.active_kind != "server":
                raise AssertionError("Server exited while loading; see server.log")
            base = app.server_connection.base_url.get()
            published_id = app.server_connection.model_id.get()
            report["connection"] = {"base_url": base, "model": published_id}
            if published_id != model.name:
                raise AssertionError("The server did not publish the configured model alias")
            app.update()
            if sys.platform == "win32":
                capture_window(app, output / "server-ready.png")
            print("API ready; checking client parameters, streaming and resource limits...", flush=True)

            with ThreadPoolExecutor(max_workers=1) as pool:
                def request(url, body=None, streaming=False):
                    future = pool.submit(stream_chat if streaming else http_json, url, body)
                    pump(future.done, timeout=100)
                    return future.result()

                origin = base.removesuffix("/v1")
                before = request(origin + "/props")["default_generation_settings"]
                report["runtime_defaults"] = before
                if before["n_ctx"] != values["ctx"]:
                    raise AssertionError("Runtime reports a different per-request context capacity")
                report["chat_requests"] = []
                for options in ({"temperature": 0.0, "top_p": 0.6, "top_k": 10, "seed": 42, "max_tokens": 8},
                                {"temperature": 0.9, "top_p": 0.95, "top_k": 40, "seed": 7, "max_tokens": 16}):
                    body = dict(options, model=published_id, stream=False,
                                messages=[{"role": "user", "content": "Cuenta desde uno hasta cincuenta, separados por comas."}],
                                chat_template_kwargs={"enable_thinking": False})
                    result = request(base + "/chat/completions", body)
                    if not result.get("choices") or not 0 < result["usage"]["completion_tokens"] <= options["max_tokens"]:
                        raise AssertionError(f"Chat client output limit was not honored: {result}")
                    report["chat_requests"].append({"request": body, "response": result})
                events = request(base + "/chat/completions", body, streaming=True)
                if not any(choice.get("delta", {}).get("content") or choice.get("delta", {}).get("reasoning_content")
                           for event in events for choice in event.get("choices", [])):
                    raise AssertionError("Streaming returned no generated text")
                report["streaming"] = {"events": len(events), "success": True}
                report["effective_client_parameters"] = []
                for options in ({"temperature": 0.0, "top_p": 0.6, "top_k": 10, "seed": 42, "n_predict": 4},
                                {"temperature": 0.9, "top_p": 0.95, "top_k": 40, "seed": 7, "n_predict": 8}):
                    result = request(origin + "/completion", dict(options, prompt="One, two, three,", stream=False))
                    settings = result["generation_settings"]
                    effective = settings.get("params", settings)
                    for key, value in options.items():
                        if abs(effective[key] - value) > 0.00001:
                            raise AssertionError(f"Runtime did not honor {key}: {effective[key]} != {value}")
                    report["effective_client_parameters"].append({"request": options, "runtime_reported": effective})
                after = request(origin + "/props")["default_generation_settings"]
                if before != after:
                    raise AssertionError("Client requests changed the server-wide generation defaults")
                report["global_defaults_unchanged"] = True

            # Editing a future launch or selecting another file must not change this connection.
            app.server_port_var.set(port + 1 if port < 65535 else port - 1)
            if len(app.models) > 1:
                app.select_inventory(next(item for item in app.models if item != model), runtime)
            if (app.server_connection.base_url.get(), app.server_connection.model_id.get()) != (base, published_id):
                raise AssertionError("Selection changes rewrote the active server connection")
            app.server_port_var.set(port)
            app.select_inventory(model, runtime)
            report["captured_connection_preserved"] = True
            app.stop_process()
            pump(lambda: not app.runner.running and app.active_kind is None, timeout=15)
            if app.server_connection.model_ids:
                raise AssertionError("Stopped server still advertises a ready connection")
            report["shutdown"] = "owned server stopped and connection cleared"
            report["success"] = True
        except Exception as exc:
            report["error"] = str(exc)
            raise
        finally:
            app.server_connection.close()
            app.runner.stop()
            app.lab.stop()
            app.chat.stop()
            app.monitor.stop()
            if app.runner.thread:
                app.runner.thread.join(10)
            app.update()
            (output / "server.log").write_text(app.log.get("1.0", "end"), encoding="utf-8")
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            for callback in app.tk.splitlist(app.tk.call("after", "info")):
                app.after_cancel(callback)
            app.destroy()
    print(f"Verified: {output / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
