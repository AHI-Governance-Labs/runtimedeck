"""One dark palette shared by ttk widgets and native Tk text widgets."""

from tkinter import ttk

PALETTE = {
    "background": "#11151d", "surface": "#1a2130", "field": "#101824",
    "text": "#e5edf7", "muted": "#a2b0c3", "accent": "#5cb8ff",
    "selected": "#294c72", "border": "#344156", "hover": "#29364b",
}


def apply_dark_theme(root):
    p = PALETTE
    root.configure(background=p["background"])
    root.option_add("*Font", "{Segoe UI} 10")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=p["background"], foreground=p["text"],
                    fieldbackground=p["field"], bordercolor=p["border"],
                    lightcolor=p["border"], darkcolor=p["border"], focuscolor=p["accent"])
    style.configure("TButton", background=p["surface"], padding=(10, 6))
    style.map("TButton", background=[("active", p["hover"]), ("pressed", p["selected"])],
              foreground=[("disabled", p["muted"])])
    style.configure("Accent.TButton", background=p["selected"], foreground=p["text"])
    style.map("Accent.TButton", background=[("active", p["hover"])])
    style.configure("TEntry", insertcolor=p["text"], padding=5)
    style.configure("TSpinbox", insertcolor=p["text"], padding=4, arrowsize=14)
    style.configure("TCombobox", padding=5, arrowcolor=p["muted"], foreground=p["text"])
    style.map("TCombobox", fieldbackground=[("readonly", p["field"]), ("disabled", p["surface"])],
              foreground=[("disabled", p["muted"]), ("readonly", p["text"])],
              selectbackground=[("readonly", p["selected"])], selectforeground=[("readonly", p["text"])])
    root.option_add("*TCombobox*Listbox.background", p["field"])
    root.option_add("*TCombobox*Listbox.foreground", p["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", p["selected"])
    for widget in ("TEntry", "TSpinbox"):
        style.map(widget, fieldbackground=[("readonly", p["surface"]), ("disabled", p["surface"])],
                  foreground=[("disabled", p["muted"])])
    style.configure("TCheckbutton", padding=4)
    style.map("TCheckbutton", background=[("active", p["background"])],
              indicatorbackground=[("selected", p["accent"]), ("!selected", p["field"])])
    style.configure("Treeview", background=p["field"], fieldbackground=p["field"],
                    foreground=p["text"], rowheight=29, borderwidth=0)
    style.configure("Plan.Treeview", rowheight=24)
    style.map("Treeview", background=[("selected", p["selected"])], foreground=[("selected", p["text"])])
    style.configure("Treeview.Heading", background=p["surface"], foreground=p["muted"], padding=7)
    style.map("Treeview.Heading", background=[("active", p["hover"])])
    style.configure("TNotebook", borderwidth=0)
    style.configure("TNotebook.Tab", background=p["surface"], foreground=p["muted"], padding=(12, 8))
    style.map("TNotebook.Tab", background=[("selected", p["selected"]), ("active", p["hover"])],
              foreground=[("selected", p["text"])])
    style.configure("TScrollbar", background=p["surface"], troughcolor=p["field"], arrowcolor=p["muted"])
    style.configure("Muted.TLabel", foreground=p["muted"])
    style.configure("Title.TLabel", foreground=p["accent"], font=("Segoe UI", 11, "bold"))
    return style


def style_text(widget):
    p = PALETTE
    widget.configure(background=p["field"], foreground=p["text"], insertbackground=p["accent"],
                     selectbackground=p["selected"], selectforeground=p["text"],
                     relief="flat", borderwidth=0, highlightthickness=1,
                     highlightbackground=p["border"], highlightcolor=p["accent"],
                     padx=10, pady=8, font=("Consolas", 10))
