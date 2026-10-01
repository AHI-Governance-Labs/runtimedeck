"""Persistent hardware strip shared by every workspace tab."""

import time
import tkinter as tk
from tkinter import ttk


class TelemetryStrip(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=(8, 4, 8, 8))
        self.latest = None
        self.device = tk.StringVar(self)
        self.device_box = ttk.Combobox(self, textvariable=self.device, state="readonly", width=28)
        self.device_box.grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.device_box.bind("<<ComboboxSelected>>", lambda event: self._render())
        self.health = tk.StringVar(self, "Esperando sensores · uso total del dispositivo")
        ttk.Label(self, textvariable=self.health, style="Muted.TLabel").grid(row=1, column=0, sticky="w")
        self.values = {}
        for index, (key, label) in enumerate((("memory", "VRAM"), ("temperature", "TEMPERATURA"),
                                               ("utilization", "CARGA GPU"), ("power", "POTENCIA")), 1):
            card = ttk.Frame(self, padding=(14, 3), style="Card.TFrame")
            card.grid(row=0, column=index, rowspan=2, sticky="nsew", padx=3)
            self.columnconfigure(index, weight=1, uniform="cards")
            ttk.Label(card, text=label, style="Muted.TLabel").pack(anchor="w")
            variable = tk.StringVar(self, "N/D")
            self.values[key] = variable
            ttk.Label(card, textvariable=variable, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self._timer_owner = self.winfo_toplevel()
        self._timer_owner.after(1000, self._freshness)

    def sample(self, sample):
        self.latest = sample
        devices = [f"{index} · {gpu['name']}" for index, gpu in enumerate(sample.get("gpus", []))]
        previous = self.device.get()
        self.device_box.configure(values=devices)
        if previous not in devices:
            self.device.set(devices[0] if devices else "Sensor no disponible")
        self._render()

    def _render(self):
        gpus = (self.latest or {}).get("gpus", [])
        if not gpus:
            for variable in self.values.values():
                variable.set("N/D")
            self.health.set((self.latest or {}).get("error", "Esperando sensores"))
            return
        index = self.device_box.current()
        gpu = gpus[max(0, index)]
        used, total = gpu.get("used_mib"), gpu.get("total_mib")
        self.values["memory"].set(f"{used / 1024:.2f} / {total / 1024:.1f} GiB" if used is not None and total else "N/D")
        for key, source, suffix in (("temperature", "temperature", "°C"), ("utilization", "utilization", "%"), ("power", "power_watts", "W")):
            value = gpu.get(source)
            self.values[key].set(f"{value:.0f} {suffix}" if value is not None else "N/D")
        self._age()

    def _age(self):
        if self.latest and self.latest.get("gpus"):
            age = max(0, time.monotonic() - self.latest["clock"])
            self.health.set(f"{'DESACTUALIZADO' if age > 6 else 'En vivo'} · hace {age:.0f}s · uso total del dispositivo")

    def _freshness(self):
        self._age()
        self._timer_owner.after(1000, self._freshness)
