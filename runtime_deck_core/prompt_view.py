"""Multiline prompt authoring with undo, search, templates and UTF-8 files."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .theme import style_text

TEMPLATES = {
    "Pregunta libre": "",
    "Razonamiento": "Resuelve el problema siguiente. Declara tus supuestos y comprueba el resultado.\n\nProblema:\n",
    "JSON": "Devuelve únicamente JSON válido, sin bloques de código.\n\nTarea:\n\nEsquema esperado:\n",
    "Revisión de código": "Revisa este código. Identifica errores concretos, su impacto y propone una corrección.\n\nCódigo:\n",
    "Resumen": "Resume el texto siguiente conservando los hechos principales. Señala las incertidumbres.\n\nTexto:\n",
}


class PromptEditor(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self._syncing = False
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(header, text="PROMPT · Ctrl+Enter para ejecutar", style="Title.TLabel").pack(side="left")
        self.count = tk.StringVar(self)
        ttk.Label(header, textvariable=self.count, style="Muted.TLabel").pack(side="right")
        toolbar = ttk.Frame(self)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self.template = tk.StringVar(self, "Pregunta libre")
        ttk.Combobox(toolbar, textvariable=self.template, values=list(TEMPLATES), state="readonly", width=20).pack(side="left")
        ttk.Button(toolbar, text="Usar plantilla", command=self.use_template).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Abrir", command=self.load).pack(side="left")
        ttk.Button(toolbar, text="Guardar", command=self.save).pack(side="left", padx=4)
        self.text = tk.Text(self, undo=True, autoseparators=True, maxundo=100, wrap="word", height=9)
        style_text(self.text)
        self.text.grid(row=2, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, command=self.text.yview)
        scrollbar.grid(row=2, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.tag_configure("found", background="#294c72")
        self.text.insert("1.0", app.prompt_var.get())
        self.text.edit_modified(False)
        self.text.bind("<<Modified>>", self._changed)
        self.text.bind("<Control-Return>", self._run)
        self.text.bind("<Control-f>", self._focus_find)
        self.text.bind("<Control-s>", lambda event: self._save_key())
        self.text.bind("<Control-z>", lambda event: self._undo())
        search = ttk.Frame(self)
        search.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(search, text="Buscar:").pack(side="left")
        self.find_var = tk.StringVar(self)
        self.find_entry = ttk.Entry(search, textvariable=self.find_var, width=20)
        self.find_entry.pack(side="left", padx=4)
        self.find_entry.bind("<Return>", lambda event: self.find())
        ttk.Button(search, text="Siguiente", command=self.find).pack(side="left")
        ttk.Button(search, text="Deshacer", command=self._undo).pack(side="left", padx=4)
        ttk.Button(search, text="Rehacer", command=self._redo).pack(side="left")
        ttk.Button(search, text="Llevar a Chat", command=self.to_chat).pack(side="right")
        app.prompt_var.trace_add("write", self._from_variable)
        self._count()

    def _count(self):
        text = self.text.get("1.0", "end-1c")
        self.count.set(f"{len(text):,} caracteres · {text.count(chr(10)) + 1} líneas")

    def _changed(self, event=None):
        if self.text.edit_modified():
            if not self._syncing:
                self._syncing = True
                self.app.prompt_var.set(self.text.get("1.0", "end-1c"))
                self._syncing = False
            self._count()
            self.text.edit_modified(False)

    def _from_variable(self, *args):
        if self._syncing:
            return
        self._syncing = True
        self.text.delete("1.0", "end")
        self.text.insert("1.0", self.app.prompt_var.get())
        self.text.edit_modified(False)
        self._count()
        self._syncing = False

    def use_template(self):
        self.app.prompt_var.set(TEMPLATES[self.template.get()])
        self.text.focus_set()

    def find(self):
        term = self.find_var.get()
        self.text.tag_remove("found", "1.0", "end")
        if not term:
            return
        start = self.text.search(term, "insert + 1c", stopindex="end", nocase=True)
        start = start or self.text.search(term, "1.0", stopindex="end", nocase=True)
        if start:
            self.text.tag_add("found", start, f"{start}+{len(term)}c")
            self.text.mark_set("insert", start)
            self.text.see(start)

    def _focus_find(self, event):
        self.find_entry.focus_set()
        return "break"

    def _run(self, event):
        self._changed()
        self.app.run_inference()
        return "break"

    def _undo(self):
        try:
            self.text.edit_undo()
        except tk.TclError:
            pass
        return "break"

    def _redo(self):
        try:
            self.text.edit_redo()
        except tk.TclError:
            pass
        return "break"

    def _save_key(self):
        self.save()
        return "break"

    def load(self):
        path = filedialog.askopenfilename(filetypes=[("Prompt UTF-8", "*.txt *.md"), ("Todos", "*.*")])
        if path:
            try:
                self.app.prompt_var.set(Path(path).read_text(encoding="utf-8"))
            except (OSError, UnicodeError) as exc:
                messagebox.showerror("Prompt", str(exc))

    def save(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Prompt UTF-8", "*.txt")])
        if path:
            try:
                Path(path).write_text(self.text.get("1.0", "end-1c"), encoding="utf-8")
            except OSError as exc:
                messagebox.showerror("Prompt", str(exc))

    def to_chat(self):
        self.app.chat_panel.input.delete("1.0", "end")
        self.app.chat_panel.input.insert("1.0", self.text.get("1.0", "end-1c"))
        self.app.workspace_notebook.select(self.app.chat_panel)
