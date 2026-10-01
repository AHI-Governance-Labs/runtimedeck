"""Opt-in integration checks against installed model/runtime; does not alter user preferences."""

import argparse
import ctypes
import json
import struct
import sys
import time
import zlib
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime_deck_core.app import RuntimeDeck
from runtime_deck_core.settings import DEFAULTS, SettingsStore


def capture_window(app, path):
    """Capture this application window only, using Win32 and standard-library PNG encoding."""
    user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    handle = wintypes.HANDLE
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetWindowDC.argtypes = [wintypes.HWND]
    user.GetWindowDC.restype = handle
    user.PrintWindow.argtypes = [wintypes.HWND, handle, wintypes.UINT]
    user.ReleaseDC.argtypes = [wintypes.HWND, handle]
    gdi.CreateCompatibleDC.argtypes = [handle]
    gdi.CreateCompatibleDC.restype = handle
    gdi.CreateCompatibleBitmap.argtypes = [handle, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = handle
    gdi.SelectObject.argtypes = [handle, handle]
    gdi.SelectObject.restype = handle
    gdi.DeleteObject.argtypes = [handle]
    gdi.DeleteDC.argtypes = [handle]
    gdi.GetDIBits.argtypes = [handle, handle, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    hwnd = user.GetAncestor(app.winfo_id(), 2)
    rectangle = wintypes.RECT()
    user.GetWindowRect(hwnd, ctypes.byref(rectangle))
    width, height = rectangle.right - rectangle.left, rectangle.bottom - rectangle.top
    source = user.GetWindowDC(hwnd)
    destination = gdi.CreateCompatibleDC(source)
    bitmap = gdi.CreateCompatibleBitmap(source, width, height)
    old = gdi.SelectObject(destination, bitmap)
    try:
        if not user.PrintWindow(hwnd, destination, 2):
            raise OSError("PrintWindow failed")
        gdi.SelectObject(destination, old)
        header = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        info = ctypes.create_string_buffer(header + bytes(16))
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi.GetDIBits(source, bitmap, 0, height, pixels, info, 0):
            raise OSError("GetDIBits failed")
        raw = pixels.raw
        rows = bytearray()
        for y in range(height):
            rows.append(0)
            start = y * width * 4
            for x in range(width):
                offset = start + x * 4
                rows.extend((raw[offset + 2], raw[offset + 1], raw[offset]))
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                         + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    finally:
        gdi.SelectObject(destination, old)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(destination)
        user.ReleaseDC(hwnd, source)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tune", action="store_true")
    parser.add_argument("--chat", action="store_true")
    parser.add_argument("--quality", action="store_true")
    parser.add_argument("--answers", action="store_true")
    parser.add_argument("--inference", action="store_true")
    parser.add_argument("--suites", action="store_true")
    parser.add_argument("--model", help="Select an exact model path instead of the first runnable model")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / "verification"
    output.mkdir(exist_ok=True)
    values = dict(DEFAULTS, ctx=1024, bench_prompt=64, bench_gen=16, bench_reps=2,
                  max_tokens=256, temp=0.0, seed=42, server_port=18088)
    errors = []
    def fail_message(title, message, **kwargs):
        raise RuntimeError(f"{title}: {message}")
    with patch.object(SettingsStore, "load", return_value=values), patch("tkinter.messagebox.showerror", side_effect=fail_message):
        app = RuntimeDeck()
        app.report_callback_exception = lambda exc_type, exc, traceback: errors.append(str(exc))
        app.settings_store = SettingsStore(output / "test-preferences.json")
        def pump(predicate, timeout=180):
            deadline = time.monotonic() + timeout
            while not predicate():
                app.update()
                if errors:
                    raise RuntimeError(str(errors))
                if time.monotonic() >= deadline:
                    raise TimeoutError("Integration check timed out")
                time.sleep(0.01)
            app.update()
        report = {}
        try:
            pump(lambda: bool(app.models) and bool(app.runtimes))
            if args.model:
                requested = Path(args.model).resolve()
                matching = [model for model in app.models if Path(model.path).resolve() == requested]
                if len(matching) != 1:
                    raise ValueError("The requested model is not in the discovered inventory")
                index = app.models.index(matching[0])
                app.model_tree.selection_set(f"m{index}")
                app.model_tree.focus(f"m{index}")
                app.on_model_select()
            if not app.selected_model.runnable:
                raise AssertionError("Default selection is not a runnable model")
            if not app.model_tree.winfo_viewable() or app.model_tree.winfo_width() < 50:
                raise AssertionError("Inventory is not visibly rendered")
            if app.log.winfo_rooty() + app.log.winfo_height() > app.winfo_rooty() + app.winfo_height():
                raise AssertionError("Output console extends beyond the visible window")
            report["inventory"] = {"models": len(app.models), "runtimes": len(app.runtimes), "selected": app.selected_model.path}
            capture_window(app, output / "workbench.png")
            if args.inference:
                app.prompt_var.set("Responde brevemente: ¿cuánto es 12 + 7?")
                app.run_inference()
                pump(lambda: app.active_kind is None and not app.runner.running, timeout=240)
                report["inference"] = {"status": app.status_var.get(), "output_tail": app.log.get("1.0", "end")[-1500:]}
                if report["inference"]["status"] != "Process exited with code 0":
                    raise AssertionError("Direct inference did not complete successfully")
            if args.tune:
                app.lab.layers.set("99")
                app.lab.threads.set("4,8")
                app.lab.batches.set("256,512")
                app.lab.start("tuning")
                pump(lambda: not app.lab.running, timeout=600)
                if len(app.lab.records) != 4 or any(record["status"] != "success" for record in app.lab.records):
                    raise AssertionError("A tuning trial failed")
                app.lab.apply_best()
                report["tuning"] = [{"settings": record["settings"], "metrics": record["metrics"]} for record in app.lab.records]
                app.workspace_notebook.select(app.lab_panel)
                app.update()
                capture_window(app, output / "experiments.png")
            if args.suites:
                for mode in ("scaling", "stability"):
                    app.lab.prompts.set("32,128")
                    app.lab.stability_runs.set(3)
                    app.lab.start(mode)
                    pump(lambda: not app.lab.running, timeout=600)
                    expected = 2 if mode == "scaling" else 3
                    if len(app.lab.records) != expected or any(record["status"] != "success" for record in app.lab.records):
                        raise AssertionError(f"{mode} suite failed")
                    report[mode] = [{"settings": record["settings"], "metrics": record["metrics"]} for record in app.lab.records]
            if args.quality:
                corpus = output / "smoke-corpus.txt"
                paragraph = "Language models estimate probabilities over sequences of words. A reliable evaluation uses a fixed corpus and measures the same workload under each configuration. Engineers record the runtime version, settings and raw output so that another person can reproduce each result. Performance and accuracy describe different properties. Faster generation alone does not prove better answers.\n"
                corpus.write_text(paragraph * 80, encoding="utf-8")
                app.ctx_var.set(256)
                app.lab.chunks.set(1)
                app.lab.dataset.set(str(corpus))
                app.lab.start("quality")
                pump(lambda: not app.lab.running, timeout=240)
                report["quality"] = app.lab.records
                if not report["quality"] or report["quality"][0]["status"] != "success":
                    raise AssertionError("Quality evaluation failed")
            if args.chat:
                app.ctx_var.set(1024)
                app.chat_panel.input.insert("end", "Responde con una sola palabra: hola. No expliques ni razones.")
                app.chat.send()
                pump(lambda: not app.chat.running, timeout=240)
                report["chat"] = {"messages": app.chat.messages, "status": app.chat.status.get()}
                if not app.chat.messages:
                    raise AssertionError("Local chat produced no assistant response")
                app.workspace_notebook.select(app.chat_panel)
                app.update()
                capture_window(app, output / "chat.png")
            if args.answers:
                app.evaluation.start()
                pump(lambda: not app.evaluation.running, timeout=300)
                report["answers"] = app.evaluation.last_result
                if (not report["answers"] or report["answers"]["error"] or report["answers"]["cancelled"]
                        or report["answers"]["metrics"].get("cases_completed") != 3):
                    raise AssertionError(f"Answer evaluation integration failed: {report['answers']}")
            if args.plan:
                app.stop_process()
                pump(lambda: app.active_kind is None and not app.runner.running, timeout=15)
                app.ctx_var.set(1024)
                app.lab.layers.set("99")
                app.lab.threads.set("4,8")
                app.lab.batches.set("256,512")
                app.lab.prompts.set("32,128")
                app.plan.criteria_vars["stability_runs"].set("3")
                app.plan.title.set("Verificación del flujo orientado a objetivos")
                app.plan.start()
                if not app.plan.running:
                    raise AssertionError("The objective plan did not start")
                pump(lambda: not app.plan.running, timeout=900)
                pump(lambda: app.active_kind is None and not app.runner.running, timeout=15)
                report["plan"] = {"path": str(app.plan.path), "status": app.plan.document["status"],
                                  "assessment": app.plan.document["assessment"], "stages": app.plan.document["stages"]}
                if report["plan"]["status"] != "finished" or any(stage["status"] != "finished" for stage in report["plan"]["stages"]):
                    raise AssertionError("An objective stage failed")
                app.workspace_notebook.select(app.plan_panel)
                app.plan_panel.render(app.plan.document)
                app.update()
                capture_window(app, output / "04-plan-result.png")
                # A second, deliberately cancelled plan must retain partial evidence
                # without obtaining an acceptance verdict.
                app.plan.start()
                pump(lambda: app.lab.runner.running, timeout=10)
                app.plan.stop()
                pump(lambda: not app.plan.running and not app.lab.runner.running, timeout=20)
                report["plan_cancel"] = {"path": str(app.plan.path), "status": app.plan.document["status"],
                                         "assessment": app.plan.document["assessment"]}
                if report["plan_cancel"]["status"] != "cancelled" or report["plan_cancel"]["assessment"]["state"] != "incompleto":
                    raise AssertionError("Cancelled plan was not marked incomplete")
            app.monitor.stop()
            app.workspace_notebook.select(app.lab_panel)
            app.lab_panel.notebook.select(app.lab_panel.results_view)
            app.update()
            capture_window(app, output / "results.png")
            app.workspace_notebook.select(app.monitor_panel)
            app.update()
            capture_window(app, output / "monitor.png")
            if errors:
                raise RuntimeError(str(errors))
            (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            print(json.dumps({"verified": list(report), "report": str(output / "report.json")}, ensure_ascii=False), flush=True)
        except Exception as exc:
            report["verification_error"] = str(exc)
            (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            raise
        finally:
            app.chat.stop()
            app.evaluation.stop()
            app.monitor.stop()
            app.lab.stop()
            app.plan.stop()
            app.runner.stop()
            for runner in (app.runner, app.lab.runner):
                if runner.thread:
                    runner.thread.join(timeout=8)
            for callback in app.tk.splitlist(app.tk.call("after", "info")):
                app.after_cancel(callback)
            app.destroy()


if __name__ == "__main__":
    main()
