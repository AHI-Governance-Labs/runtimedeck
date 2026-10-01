"""Live GPU use and sampled history; memory includes all processes on the device."""

import tkinter as tk
from collections import deque
from tkinter import ttk

from .theme import PALETTE


class MonitorPanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=12)
        self.samples = deque(maxlen=120)
        self.summary = tk.StringVar(self, "Waiting for NVIDIA telemetry...")
        ttk.Label(self, text="LIVE HARDWARE", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self, textvariable=self.summary, wraplength=850).pack(fill="x", pady=15)
        self.chart = tk.Canvas(self, background=PALETTE["field"], height=230, highlightthickness=0)
        self.chart.pack(fill="both", expand=True)
        self.chart.bind("<Configure>", lambda event: self._draw())
        ttk.Label(self, text="Blue: VRAM utilization % · green: GPU compute % · last 120 samples. Device memory includes other applications. Peak measurements are also saved with benchmark results.", wraplength=850, style="Muted.TLabel").pack(fill="x", pady=12)

    def sample(self, sample):
        if not sample.get("gpus"):
            self.summary.set(sample.get("error", "No NVIDIA GPU detected."))
            return
        self.samples.append(sample)
        lines = []
        for gpu in sample["gpus"]:
            lines.append(f"{gpu['name']} · VRAM {gpu['used_mib'] or 0:.0f}/{gpu['total_mib'] or 0:.0f} MiB"
                         f" · GPU {gpu['utilization'] or 0:.0f}% · {gpu['temperature'] or 0:.0f} °C"
                         f" · {gpu['power_watts'] or 0:.1f} W")
        self.summary.set("\n".join(lines))
        self._draw()

    def _draw(self):
        self.chart.delete("all")
        width, height = max(100, self.chart.winfo_width()), max(100, self.chart.winfo_height())
        left, right, top, bottom = 40, width - 20, 20, height - 30
        for percent in (0, 25, 50, 75, 100):
            y = bottom - (bottom - top) * percent / 100
            self.chart.create_line(left, y, right, y, fill=PALETTE["border"])
            self.chart.create_text(left - 8, y, anchor="e", text=str(percent), fill=PALETTE["muted"])
        if len(self.samples) < 2:
            return
        for memory, color in ((True, PALETTE["accent"]), (False, "#59d8a0")):
            points = []
            for index, sample in enumerate(self.samples):
                gpu = sample["gpus"][0]
                percent = 100 * (gpu["used_mib"] or 0) / (gpu["total_mib"] or 1) if memory else gpu["utilization"] or 0
                points.extend((left + (right - left) * index / (len(self.samples) - 1),
                               bottom - (bottom - top) * min(100, max(0, percent)) / 100))
            self.chart.create_line(*points, fill=color, width=2)
