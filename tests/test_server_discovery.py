"""Portable sibling discovery and the external-client connection contract."""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from runtime_deck_core.catalog import model_label
from runtime_deck_core.commands import build_command
from runtime_deck_core.config import APP_ROOT, DEFAULT_ROOT
from runtime_deck_core.discovery import discover_workspace, normalize_workspace
from runtime_deck_core.server_connection import fetch_model_ids, server_base_url
from runtime_deck_core.settings import DEFAULTS, SettingsStore


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.model_path = self.root / "Modelos" / "family" / "model.gguf"
        self.model_path.parent.mkdir(parents=True)
        self.model_path.write_bytes(b"GGUF")
        self.binary = self.root / "Runtimes" / "engine" / "build" / "bin" / "Debug" / "llama-server.exe"
        self.binary.parent.mkdir(parents=True)
        self.binary.touch()

    def test_parent_and_either_asset_folder_find_the_same_inventory(self):
        inventory = discover_workspace(self.root)
        for choice in (self.root / "Modelos", self.root / "Runtimes"):
            self.assertEqual(normalize_workspace(choice), self.root)
            self.assertEqual(discover_workspace(choice), inventory)
        models, runtimes, warnings = inventory
        self.assertEqual(models[0].path, str(self.model_path))
        self.assertEqual(runtimes[0].server, str(self.binary))
        self.assertEqual(runtimes[0].label, "engine/build/bin/Debug")
        self.assertEqual(model_label(models[0], self.root)["family"], "Family")
        self.assertEqual(warnings, [])

    def test_source_folders_are_visible_without_a_fake_launch_capability(self):
        (self.root / "Runtimes" / "other-engine").mkdir()
        models, runtimes, _ = discover_workspace(self.root)
        self.assertTrue(runtimes[0].server)
        source = runtimes[1]
        self.assertEqual(source.label, "other-engine")
        self.assertEqual(source.capabilities, "sin ejecutable compatible")
        with self.assertRaises(ValueError):
            build_command("server", models[0], source, DEFAULTS)

    def test_dependency_trees_are_skipped_but_build_output_is_scanned(self):
        for parent in ("Modelos", "Runtimes"):
            dependency = self.root / parent / "node_modules"
            dependency.mkdir()
            (dependency / "llama-server.exe").touch()
            (dependency / "junk.gguf").touch()
        models, runtimes, _ = discover_workspace(self.root)
        self.assertEqual(len(models), 1)
        self.assertEqual(len(runtimes), 1)
        self.assertEqual(runtimes[0].server, str(self.binary))

    def test_missing_asset_folders_report_the_paths_being_searched(self):
        empty = self.root / "empty"
        empty.mkdir()
        models, runtimes, warnings = discover_workspace(empty)
        self.assertEqual((models, runtimes), ([], []))
        self.assertEqual(len(warnings), 2)
        self.assertTrue(all(str(empty) in warning for warning in warnings))

    def test_saved_asset_folder_is_migrated_to_its_parent(self):
        store = SettingsStore(self.root / "settings.json")
        store.save(dict(DEFAULTS, root=str(self.root / "Runtimes"), model_path=str(self.model_path),
                        runtime_directory=str(self.binary.parent)))
        self.assertEqual(store.load()["root"], str(self.root))
        self.assertEqual(store.load()["model_path"], str(self.model_path))
        self.assertEqual(store.load()["runtime_directory"], str(self.binary.parent))
        store.save(dict(DEFAULTS, root=str(self.root / "removed-workspace")))
        self.assertEqual(store.load()["root"], str(DEFAULT_ROOT))
        self.assertEqual(DEFAULT_ROOT, APP_ROOT.parent)

    def test_server_command_does_not_inherit_local_generation_parameters(self):
        models, runtimes, _ = discover_workspace(self.root)
        values = dict(DEFAULTS, temp=4, top_p=0.1, max_tokens=9, seed=123, server_parallel=2, mmap=False)
        command = build_command("server", models[0], runtimes[0], values)
        for flag in ("--temp", "--top-p", "--top-k", "--seed", "-n", "-p", "--props"):
            self.assertNotIn(flag, command)
        self.assertEqual(command[command.index("--alias") + 1], models[0].name)
        self.assertEqual(command[command.index("--parallel") + 1], "2")
        self.assertIn("--no-mmap", command)


class ConnectionTests(unittest.TestCase):
    def test_ipv4_ipv6_and_bind_all_addresses_produce_usable_client_urls(self):
        for host, expected in (("0.0.0.0", "127.0.0.1"), ("::", "[::1]"),
                               ("::1", "[::1]"), ("[::]", "[::1]"), ("localhost", "localhost")):
            self.assertEqual(server_base_url(dict(DEFAULTS, server_host=host, server_port=12345)),
                             f"http://{expected}:12345/v1")
        for host in ("http://localhost", "localhost/path", "bad host", "host@localhost", "localhost:1234"):
            with self.assertRaises(ValueError):
                server_base_url(dict(DEFAULTS, server_host=host))

    def test_ids_are_read_from_a_real_http_response_and_invalid_services_are_rejected(self):
        class Handler(BaseHTTPRequestHandler):
            body = {"data": [{"id": "published-model"}, {"id": "published-model"}, {"id": "second"}]}

            def do_GET(self):
                self.server.seen_path = self.path
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(self.body).encode())

            def log_message(self, *_):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}/v1"
                self.assertEqual(fetch_model_ids(base), ["published-model", "second"])
                self.assertEqual(server.seen_path, "/v1/models")
                for body in ({"data": []}, {"status": "ok"}, [], {"data": [{"id": 4}]}):
                    Handler.body = body
                    with self.assertRaises(ValueError):
                        fetch_model_ids(base)
            finally:
                server.shutdown()
                thread.join(3)

    def test_gui_uses_published_id_and_preserves_the_running_address(self):
        from runtime_deck_core.app import RuntimeDeck
        with patch.object(SettingsStore, "load", return_value=DEFAULTS.copy()):
            app = RuntimeDeck()
        app.withdraw()
        try:
            for callback in app.tk.splitlist(app.tk.call("after", "info")):
                app.after_cancel(callback)
            self.assertEqual(app.workspace_notebook.select(), str(app.server_tab))
            connection = app.server_connection
            original = connection.base_url.get()
            connection.event((connection.generation, original, ["id-from-api"], ""))
            connection.copy("config")
            self.assertEqual(json.loads(app.clipboard_get()), {"base_url": original, "model": "id-from-api"})
            app.server_values = DEFAULTS.copy()
            app.active_kind = "server"
            app.server_port_var.set(8765)
            self.assertEqual(connection.base_url.get(), original)
            app.active_kind = None
            generation = connection.generation
            connection.stopped()
            self.assertIn(":8765/", connection.base_url.get())
            connection.event((generation, original, ["stale-id"], ""))
            self.assertEqual(connection.model_ids, [])
        finally:
            app.server_connection.close()
            app.destroy()
