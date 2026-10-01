"""Responsive common controls with genuine parent-child geometry."""

import tkinter as tk
from tkinter import ttk

from .theme import PALETTE


class ScrolledForm(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, background=PALETTE["background"], highlightthickness=0,
                                width=600, height=400)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.content = ttk.Frame(self.canvas, padding=10)
        window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(window, width=event.width))
        self.canvas.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-int(event.delta / 120), "units"))


class BoundText(tk.Text):
    """A multiline editor synchronized with a StringVar in both directions."""

    def __init__(self, parent, variable, **options):
        super().__init__(parent, **options)
        self.variable = variable
        self._syncing = False
        self.insert("1.0", variable.get())
        self.edit_modified(False)
        self.bind("<<Modified>>", lambda event: self.flush())
        variable.trace_add("write", self._from_variable)

    def flush(self):
        if self.edit_modified():
            if not self._syncing:
                self._syncing = True
                self.variable.set(self.get("1.0", "end-1c"))
                self._syncing = False
            self.edit_modified(False)

    def _from_variable(self, *args):
        if not self._syncing:
            self._syncing = True
            self.delete("1.0", "end")
            self.insert("1.0", self.variable.get())
            self.edit_modified(False)
            self._syncing = False
