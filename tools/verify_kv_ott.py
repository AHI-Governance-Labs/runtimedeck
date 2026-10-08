"""Verify real llama.cpp KV save, OTT recovery, restart, restore and prefix reuse."""

import argparse
import copy
import json
import shutil
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime_deck_core.app import RuntimeDeck
from runtime_deck_core.config import DEFAULT_ROOT
from runtime_deck_core.kv_archive import KVArchive
from runtime_deck_core.kv_slots import slot_json
from runtime_deck_core.ott_storage import load_ott
from runtime_deck_core.settings import DEFAULTS, SettingsStore
from verify_local import capture_window


def damage_copy(volume, module, copies):
    with volume.open("r+b") as stream:
        manifest = module.read_json_block(stream, module.MANIFEST_OFFSET, module.MANIFEST_SIZE, {})
        entry = manifest["regions"]["0"]
        offsets = [entry["offset"]] + [item["offset"] for item in entry["replicas"]]
        for offset in offsets[:copies]:
            stream.seek(offset)
            value = stream.read(1)[0]
            stream.seek(offset)
            stream.write(bytes([value ^ 1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=str(DEFAULT_ROOT))
    parser.add_argument("--model", required=True, help="Exact compatible GGUF path")
    parser.add_argument("--ott-core", default="", help="Checkout root, or installed ott package if omitted")
    parser.add_argument("--runtime", help="Exact llama-server executable, otherwise first discovered server")
    args = parser.parse_args()
    module = load_ott(args.ott_core)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = Path(__file__).resolve().parents[1] / "verification" / "kv-ott" / run_id
    output.mkdir(parents=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    values = dict(DEFAULTS, root=args.workspace, ctx=1024, threads=4, batch=256, ubatch=128,
                  server_port=port, server_parallel=1, kv_cache_enabled=True, kv_ott_core=args.ott_core,
                  kv_cache_directory=str(output / "sessions"), kv_cache_budget_mib=2048, kv_cache_max_mib=512)
    report = {"success": False, "run_id": run_id, "limits": {"context": 1024, "max_snapshot_mib": 512},
              "scope": "native KV session persistence; no active-context offloading claim"}
    errors = []

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
                    raise TimeoutError(app.kv.status.get() + " / " + app.server_connection.status.get())
                time.sleep(0.02)
            app.update()

        def operation(action, snapshot_id=None):
            if snapshot_id:
                app.kv_panel.snapshots.selection_set(snapshot_id)
            getattr(app.kv, action)()
            pump(lambda: app.kv.last_result is not None or app.kv.status.get().startswith("KV:"))
            if app.kv.last_result is None:
                raise AssertionError(app.kv.status.get())
            pump(lambda: not app.kv.running)
            return app.kv.last_result

        def start():
            app.run_server()
            if app.active_kind != "server":
                raise AssertionError("Server did not start")
            pump(lambda: bool(app.server_connection.model_ids) or app.active_kind is None)
            if app.active_kind != "server":
                raise AssertionError("Server exited while loading; see server.log")

        try:
            pump(lambda: bool(app.models) and bool(app.runtimes), timeout=30)
            model = next(item for item in app.models if Path(item.path).resolve() == Path(args.model).resolve())
            runtime = next(item for item in app.runtimes if item.server and
                           (not args.runtime or Path(item.server).resolve() == Path(args.runtime).resolve()))
            app.select_inventory(model, runtime)
            report["model"] = model.path
            report["runtime_version"] = subprocess.check_output([runtime.server, "--version"], text=True,
                                                                 stderr=subprocess.STDOUT, timeout=10).strip()
            report["command"] = app.build_server()
            print(f"Loading {model.name}; native KV slots enabled on temporary port {port}...", flush=True)
            start()
            origin = app.server_connection.base_url.get().removesuffix("/v1")
            report["connection"] = {"base_url": origin + "/v1", "model": app.server_connection.model_id.get()}

            with ThreadPoolExecutor(max_workers=1) as pool:
                def request(route, body=None):
                    future = pool.submit(slot_json, origin, route, body)
                    pump(future.done, timeout=60)
                    return future.result()

                defaults = request("/props")["default_generation_settings"]
                prompt = "Registro de una sesión local.\n" + "\n".join(
                    f"Entrada {index}: guardamos el contexto y comprobamos sus valores." for index in range(16))
                body = {"prompt": prompt, "id_slot": 0, "cache_prompt": True, "n_predict": 1,
                        "temperature": 0.0, "seed": 42, "return_tokens": True, "stream": False}
                report["warmup"] = request("/completion", body)
                print("Prompt processed; saving the native slot through the KV panel...", flush=True)
                app.kv.label.set("Verificación KV real")
                saved = operation("save")
                report["save"] = saved
                snapshot_id = saved["snapshot"]["id"]
                identity = saved["snapshot"]["identity"]
                archive = KVArchive(values["kv_cache_directory"], args.ott_core, 2048, 512)
                if list(archive.slots_path.iterdir()):
                    raise AssertionError("Native staging files were not removed after archiving")
                followup = dict(body, prompt=prompt + "\nDescribe la utilidad de este registro: ", n_predict=8, seed=123)
                expected = request("/completion", followup)
                report["expected_continuation"] = expected
                if not expected.get("tokens"):
                    raise AssertionError("The native runtime returned no generated tokens")
                request("/slots/0?action=erase", {})

                # A damaged primary must recover using OTT's second replica.
                volume = archive.root / (snapshot_id + ".ott")
                damage_copy(volume, module, 1)
                print("Primary replica damaged; restoring through OTT verification...", flush=True)
                restored = operation("restore", snapshot_id)
                if restored["storage"]["metrics"]["recoveries"] != 1:
                    raise AssertionError("OTT did not recover the damaged primary")
                report["replica_restore"] = restored
                actual = request("/completion", followup)
                if actual["tokens"] != expected["tokens"]:
                    raise AssertionError("Restored KV changed the deterministic continuation")
                if actual.get("timings", {}).get("cache_n", actual.get("tokens_cached", 0)) <= 0:
                    raise AssertionError("Runtime did not report reusing the restored prefix")
                report["reused_continuation"] = actual

                # Incompatible model/layout identities must be refused before native restoration.
                mismatched = copy.deepcopy(identity)
                mismatched["model_sha256"] = "0" * 64
                try:
                    archive.restore_bytes(snapshot_id, mismatched)
                    raise AssertionError("An incompatible snapshot was accepted")
                except ValueError as exc:
                    report["incompatibility_refused"] = str(exc)

                # Keep the valid session while exercising refusal on a separate damaged volume.
                refusal = output / "refusal-probe"
                refusal.mkdir()
                shutil.copy2(volume, refusal / volume.name)
                shutil.copy2(archive.root / (snapshot_id + ".json"), refusal / (snapshot_id + ".json"))
                damage_copy(refusal / volume.name, module, 2)
                try:
                    KVArchive(refusal, args.ott_core, 2048, 512).restore_bytes(snapshot_id, identity)
                    raise AssertionError("Corruption in both replicas was accepted")
                except module.OttTensorError as exc:
                    report["total_corruption_refused"] = str(exc)

                print("Replica recovery and prefix reuse passed; restarting the owned server...", flush=True)
                app.stop_process()
                pump(lambda: app.active_kind is None and not app.runner.running, timeout=15)
                start()
                restarted = operation("restore", snapshot_id)
                report["restore_after_server_restart"] = restarted
                continued = request("/completion", followup)
                if continued["tokens"] != expected["tokens"]:
                    raise AssertionError("Persisted KV changed continuation after server restart")
                if continued.get("timings", {}).get("cache_n", continued.get("tokens_cached", 0)) <= 0:
                    raise AssertionError("Persisted prefix was not reused after restart")
                report["continuation_after_restart"] = continued

                # External apps continue to own generation settings after restoration.
                client = {"model": report["connection"]["model"], "temperature": 0.8, "top_p": 0.7,
                          "seed": 7, "max_tokens": 4, "messages": [{"role": "user", "content": "Di hola."}],
                          "chat_template_kwargs": {"enable_thinking": False}}
                response = request("/v1/chat/completions", client)
                if not 0 < response["usage"]["completion_tokens"] <= 4:
                    raise AssertionError("Client output limit was not respected")
                report["client_parameters"] = {"request": client, "response": response}
                if request("/props")["default_generation_settings"] != defaults:
                    raise AssertionError("KV operations or client requests changed global generation defaults")
                report["generation_defaults_unchanged"] = True

            app.workspace_notebook.select(app.kv_panel)
            app.update()
            if not app.kv_panel.winfo_viewable() or not app.kv_panel.snapshots.exists(snapshot_id):
                raise AssertionError("The saved KV session is not present in the rendered panel")
            if sys.platform == "win32":
                capture_window(app, output / "kv-sessions.png")
            app.stop_process()
            pump(lambda: app.active_kind is None and not app.runner.running, timeout=15)
            report["staging_empty"] = not list(archive.slots_path.iterdir())
            report["shutdown"] = "owned server stopped"
            report["success"] = True
        except Exception as exc:
            report["error"] = str(exc)
            raise
        finally:
            app.kv.close()
            app.server_connection.close()
            app.runner.stop()
            app.lab.stop()
            app.chat.stop()
            app.monitor.stop()
            if app.runner.thread:
                app.runner.thread.join(10)
            if app.kv.thread:
                app.kv.thread.join(50)
            app.update()
            (output / "server.log").write_text(app.log.get("1.0", "end"), encoding="utf-8")
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            for callback in app.tk.splitlist(app.tk.call("after", "info")):
                app.after_cancel(callback)
            app.destroy()
    print(f"Verified: {output / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
