"""KV compatibility, corruption refusal, quotas and the native slot protocol."""

import copy
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from runtime_deck_core.commands import build_command
from runtime_deck_core.discovery import discover_workspace
from runtime_deck_core.kv_archive import KVArchive, server_identity
from runtime_deck_core.kv_slots import SlotKVService
from runtime_deck_core.settings import DEFAULTS, SettingsStore


class ByteStorage:
    """Protocol fixture: actual OTT correctness is exercised by verify_kv_ott.py."""
    def store(self, path, blob):
        Path(path).write_bytes(blob)
        return {"audit_chain": "OK"}

    def load(self, path, max_bytes):
        blob = Path(path).read_bytes()
        if len(blob) > max_bytes:
            raise ValueError("too large")
        return blob, {"audit_chain": "OK"}


class KVSessionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        model = self.root / "Modelos" / "test.gguf"
        model.parent.mkdir()
        model.write_bytes(b"GGUF test identity")
        binary = self.root / "Runtimes" / "test" / "llama-server.exe"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"server binary identity")
        models, runtimes, _ = discover_workspace(self.root)
        self.model, self.runtime = models[0], runtimes[0]
        self.identity = server_identity(self.model, self.runtime, DEFAULTS)
        self.archive = KVArchive(self.root / "sessions", storage=ByteStorage())
        self.native = self.root / "native.bin"
        self.native.write_bytes(b"native KV tokens and layout" * 128)

    def save(self):
        return self.archive.save(self.native, self.identity, "Mi sesión", 42)["snapshot"]

    def test_archive_survives_reopening_and_keeps_exact_native_bytes(self):
        record = self.save()
        reopened = KVArchive(self.archive.root, storage=ByteStorage())
        self.assertEqual(reopened.list_snapshots(), [record])
        payload, evidence = reopened.restore_bytes(record["id"], self.identity)
        self.assertEqual(payload, self.native.read_bytes())
        self.assertEqual(evidence["snapshot"]["tokens"], 42)

    def test_incompatible_model_runtime_or_layout_is_rejected_before_loading(self):
        record = self.save()
        for key in ("model_sha256", "runtime_sha256", "runtime_libraries", "layout"):
            identity = copy.deepcopy(self.identity)
            identity[key] = "different"
            with patch.object(self.archive.storage, "load") as load:
                with self.assertRaisesRegex(ValueError, "incompatible"):
                    self.archive.restore_bytes(record["id"], identity)
                load.assert_not_called()

    def test_catalog_tampering_and_payload_corruption_are_refused(self):
        record = self.save()
        catalog = self.archive.root / (record["id"] + ".json")
        changed = dict(record, label="Rewritten catalog")
        catalog.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "catálogo"):
            self.archive.restore_bytes(record["id"], self.identity)
        catalog.write_text(json.dumps(record), encoding="utf-8")
        volume = self.archive.root / (record["id"] + ".ott")
        damaged = bytearray(volume.read_bytes())
        damaged[-1] ^= 1
        volume.write_bytes(damaged)
        with self.assertRaisesRegex(ValueError, "integridad"):
            self.archive.restore_bytes(record["id"], self.identity)

    def test_quota_and_size_limits_do_not_publish_partial_archives(self):
        self.archive.budget = 1
        with patch.object(self.archive.storage, "store") as store:
            with self.assertRaisesRegex(ValueError, "presupuesto"):
                self.save()
            store.assert_not_called()
        self.archive.budget = 4096 * 1024 * 1024
        self.archive.max_bytes = 1
        with self.assertRaisesRegex(ValueError, "límite"):
            self.save()
        self.assertEqual(self.archive.list_snapshots(), [])

    def test_malformed_catalog_is_skipped_and_cannot_be_restored(self):
        record = self.save()
        catalog = self.archive.root / (record["id"] + ".json")
        for value in ([], None, {"id": record["id"], "format": "other"}):
            catalog.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(self.archive.list_snapshots(), [])
            with self.assertRaisesRegex(ValueError, "formato"):
                self.archive.restore_bytes(record["id"], self.identity)

    def test_identity_excludes_sampling_but_tracks_model_and_runtime_libraries(self):
        self.assertEqual(server_identity(self.model, self.runtime, dict(DEFAULTS, temp=3, seed=123)), self.identity)
        (Path(self.runtime.server).parent / "llama.dll").write_bytes(b"layout implementation")
        self.assertNotEqual(server_identity(self.model, self.runtime, DEFAULTS), self.identity)
        Path(self.model.path).write_bytes(b"GGUF another model")
        self.assertNotEqual(server_identity(self.model, self.runtime, DEFAULTS)["model_sha256"],
                            self.identity["model_sha256"])

    def test_slot_commands_are_opt_in_and_keep_client_sampling_ownership(self):
        command = build_command("server", self.model, self.runtime, DEFAULTS)
        self.assertNotIn("--slot-save-path", command)
        values = dict(DEFAULTS, kv_cache_enabled=True, kv_cache_directory=str(self.archive.root), temp=4)
        command = build_command("server", self.model, self.runtime, values)
        self.assertIn("--slots", command)
        self.assertEqual(command[command.index("--slot-save-path") + 1], str(self.archive.slots_path))
        self.assertNotIn("--temp", command)
        with self.assertRaises(ValueError):
            build_command("server", self.model, self.runtime, dict(values, kv_cache_directory=""))

    def test_identifiers_cannot_escape_the_archive(self):
        for snapshot_id in ("../other", "", "A" * 32, None):
            with self.assertRaises(ValueError):
                self.archive.restore_bytes(snapshot_id, self.identity)

    def test_real_http_slot_protocol_cleanup_busy_and_restore_refusal(self):
        payload = self.native.read_bytes()
        stage = self.archive.slots_path
        seen = []

        class Handler(BaseHTTPRequestHandler):
            busy = False

            def reply(self, value):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

            def do_GET(self):
                self.reply([{"id": 0, "n_ctx": 1024, "is_processing": self.busy}])

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(self.path)
                native = stage / body["filename"]
                if self.path.endswith("action=save"):
                    native.write_bytes(payload)
                    self.reply({"id_slot": 0, "n_saved": 42, "n_written": len(payload)})
                else:
                    assert native.read_bytes() == payload
                    self.reply({"id_slot": 0, "n_restored": 42, "n_read": len(payload)})

            def log_message(self, *_):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                service = SlotKVService(f"http://127.0.0.1:{server.server_port}/v1", self.archive, self.identity)
                saved = service.save(0, "HTTP session")
                self.assertEqual(list(stage.iterdir()), [])
                service.restore(0, saved["snapshot"]["id"])
                self.assertEqual(list(stage.iterdir()), [])
                self.assertEqual(seen, ["/slots/0?action=save", "/slots/0?action=restore"])
                Handler.busy = True
                with self.assertRaisesRegex(ValueError, "ocupado"):
                    service.save(0, "busy")
                self.assertEqual(len(seen), 2)
                Handler.busy = False
                service.identity = dict(self.identity, model_sha256="different")
                with self.assertRaisesRegex(ValueError, "incompatible"):
                    service.restore(0, saved["snapshot"]["id"])
                self.assertEqual(len(seen), 2)
            finally:
                server.shutdown()
                thread.join(3)

    def test_gui_binds_storage_to_running_server_and_ignores_stale_events(self):
        from runtime_deck_core.app import RuntimeDeck
        values = dict(DEFAULTS, root=str(self.root), kv_cache_enabled=True,
                      kv_cache_directory=str(self.archive.root))
        with patch.object(SettingsStore, "load", return_value=values):
            app = RuntimeDeck()
        app.withdraw()
        try:
            for callback in app.tk.splitlist(app.tk.call("after", "info")):
                app.after_cancel(callback)
            app.kv.save()
            self.assertFalse(app.kv.running)
            self.assertIn("Inicia", app.kv.status.get())
            app.kv.bind(self.model, self.runtime, values)
            app.kv_cache_directory_var.set(str(self.root / "future"))
            self.assertEqual(app.kv._values()["kv_cache_directory"], str(self.archive.root))
            generation = app.kv.generation
            app.kv.stopped()
            app.kv.event((generation, "save", None, "stale error"))
            self.assertNotIn("stale", app.kv.status.get())
        finally:
            app.kv.close()
            app.server_connection.close()
            app.destroy()


if __name__ == "__main__":
    unittest.main()
