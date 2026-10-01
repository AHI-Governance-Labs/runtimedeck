"""Streaming local conversation with a multiline prompt and readable transcript."""

import tkinter as tk
from tkinter import ttk

from .theme import style_text


class ChatPanel(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.controller = app.chat
        ttk.Label(self, text="LOCAL CHAT", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self, text="System instruction").pack(anchor="w", pady=(8, 2))
        ttk.Entry(self, textvariable=self.controller.system).pack(fill="x")
        ttk.Checkbutton(self, text="Enable thinking (if the model supports it)", variable=self.controller.thinking).pack(anchor="w")
        self.transcript = app._scrolled(self, tk.Text, wrap="word", height=13)
        style_text(self.transcript)
        self.transcript.configure(state="disabled")
        ttk.Label(self, text="Message · Ctrl+Enter to send").pack(anchor="w", pady=(6, 2))
        self.input = app._scrolled(self, tk.Text, expand=False, height=3, wrap="word")
        style_text(self.input)
        self.input.bind("<Control-Return>", self._send)
        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="SEND", style="Accent.TButton", command=self.controller.send).pack(side="left")
        ttk.Button(actions, text="Stop reply", command=self.controller.stop).pack(side="left", padx=6)
        ttk.Button(actions, text="New conversation", command=self.controller.clear).pack(side="left")
        ttk.Button(actions, text="Save conversation", command=self.controller.save).pack(side="left", padx=6)
        ttk.Label(self, textvariable=self.controller.status, style="Muted.TLabel", wraplength=850).pack(fill="x")

    def _send(self, event):
        self.controller.send()
        return "break"

    def append(self, text):
        self.transcript.configure(state="normal")
        self.transcript.insert("end", text)
        self.transcript.see("end")
        self.transcript.configure(state="disabled")

    def clear(self):
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")
