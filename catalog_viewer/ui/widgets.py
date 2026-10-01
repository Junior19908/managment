"""Widgets e utilitários Tk reutilizáveis."""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Optional, Sequence, Tuple


def clear_tree(tree: ttk.Treeview) -> None:
    tree.delete(*tree.get_children())


def make_scrolled_tree(
    parent: tk.Misc,
    columns: Sequence[str],
    headings: Dict[str, str],
    widths: Dict[str, int],
    anchors: Optional[Dict[str, str]] = None,
    height: Optional[int] = None,
    sortable: bool = True,
    horizontal: bool = True,
) -> Tuple[ttk.Frame, ttk.Treeview]:
    """Cria um Treeview com barras de rolagem dentro de um Frame (grid)."""
    anchors = anchors or {}
    frame = ttk.Frame(parent)
    kwargs = {"columns": list(columns), "show": "headings", "selectmode": "browse"}
    if height:
        kwargs["height"] = height
    tree = ttk.Treeview(frame, **kwargs)

    for col in columns:
        tree.heading(col, text=headings.get(col, col), anchor="w")
        tree.column(col, width=widths.get(col, 120), minwidth=40, anchor=anchors.get(col, "w"))

    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    if horizontal:
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(xscrollcommand=hsb.set)
        hsb.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)

    if sortable:
        enable_column_sorting(tree, columns)
    return frame, tree


def enable_column_sorting(tree: ttk.Treeview, columns: Sequence[str]) -> None:
    """Clique no cabeçalho ordena a coluna (alterna asc/desc) e mostra um indicador."""
    state = {"col": None, "reverse": False}

    def _sort_key(value: str):
        try:
            return (0, float(value))
        except (TypeError, ValueError):
            return (1, str(value).lower())

    def sort_by(col: str) -> None:
        reverse = state["col"] == col and not state["reverse"]
        state.update(col=col, reverse=reverse)
        items = [(tree.set(iid, col), iid) for iid in tree.get_children("")]
        items.sort(key=lambda t: _sort_key(t[0]), reverse=reverse)
        for index, (_, iid) in enumerate(items):
            tree.move(iid, "", index)
        for c in columns:
            base = tree.heading(c, "text").rstrip(" ▲▼")
            marker = (" ▼" if reverse else " ▲") if c == col else ""
            tree.heading(c, text=base + marker)

    for col in columns:
        tree.heading(col, command=lambda c=col: sort_by(c))


def make_scrolled_text(
    parent: tk.Misc, wrap: str = "none", read_only: bool = True, **kwargs
) -> Tuple[ttk.Frame, tk.Text]:
    """Text com rolagem vertical/horizontal. Quando ``read_only`` o usuário só seleciona/copia."""
    frame = ttk.Frame(parent)
    text = tk.Text(frame, wrap=wrap, undo=False, **kwargs)
    vsb = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=vsb.set)
    text.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    if wrap == "none":
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(xscrollcommand=hsb.set)
        hsb.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)

    if read_only:
        make_text_read_only(text)
    return frame, text


def make_text_read_only(text: tk.Text) -> None:
    """Bloqueia edição mas mantém seleção, cópia (Ctrl+C) e Ctrl+A."""

    def block(event):
        # Permite atalhos de navegação/cópia; bloqueia inserção de texto.
        if event.state & 0x4 and event.keysym.lower() in ("c", "a", "f"):
            return None
        if event.keysym in (
            "Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next",
            "Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
        ):
            return None
        return "break"

    text.bind("<Key>", block)
    text.bind("<Control-a>", lambda e: (text.tag_add("sel", "1.0", "end-1c"), "break")[1])


def set_text(text: tk.Text, content: str) -> None:
    text.delete("1.0", tk.END)
    text.insert(tk.END, content)
    text.edit_reset()


def copy_to_clipboard(widget: tk.Misc, value: str) -> None:
    widget.clipboard_clear()
    widget.clipboard_append(value)
    widget.update_idletasks()


def center_on_parent(dialog: tk.Toplevel, parent: tk.Misc) -> None:
    parent.update_idletasks()
    dialog.update_idletasks()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    dw, dh = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
    if pw < 50 or ph < 50:  # janela pai ainda não mapeada: centraliza na tela
        px, py = 0, 0
        pw, ph = dialog.winfo_screenwidth(), dialog.winfo_screenheight()
    x = max(px + (pw - dw) // 2, 0)
    y = max(py + (ph - dh) // 2, 0)
    dialog.geometry(f"+{x}+{y}")


def bring_to_front(dialog: tk.Toplevel) -> None:
    dialog.lift()
    dialog.attributes("-topmost", True)
    dialog.after(150, lambda: dialog.attributes("-topmost", False))


class Debouncer:
    """Agrupa chamadas rápidas (ex.: digitação no filtro) em uma única execução."""

    def __init__(self, widget: tk.Misc, delay_ms: int, callback: Callable[[], None]):
        self._widget = widget
        self._delay = delay_ms
        self._callback = callback
        self._job: Optional[str] = None

    def trigger(self, *_args) -> None:
        if self._job is not None:
            self._widget.after_cancel(self._job)
        self._job = self._widget.after(self._delay, self._fire)

    def _fire(self) -> None:
        self._job = None
        self._callback()
