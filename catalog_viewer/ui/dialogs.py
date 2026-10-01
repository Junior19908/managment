"""Diálogos modais de definição de senha e login."""

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Optional

from ..logging_setup import log
from ..security import MIN_PASSWORD_LENGTH
from .widgets import bring_to_front, center_on_parent

MAX_LOGIN_ATTEMPTS = 5


class _ModalDialog(tk.Toplevel):
    """Base: janela transient, centralizada, com Enter/Esc e grab."""

    def __init__(self, parent: tk.Misc, title: str):
        super().__init__(parent)
        self.parent = parent
        self.result = False
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.bind("<Return>", lambda e: self.on_ok())
        self.bind("<Escape>", lambda e: self.on_cancel())

        self.body = ttk.Frame(self, padding=12)
        self.body.grid(row=0, column=0, sticky="nsew")
        self.build_body(self.body)

        btns = ttk.Frame(self, padding=(12, 0, 12, 12))
        btns.grid(row=1, column=0, sticky="e")
        ttk.Button(btns, text=self.ok_label, command=self.on_ok, default="active").pack(
            side=tk.RIGHT
        )
        ttk.Button(btns, text="Cancelar", command=self.on_cancel).pack(side=tk.RIGHT, padx=(0, 6))

    ok_label = "OK"

    def build_body(self, body: ttk.Frame) -> None:  # pragma: no cover - sobrescrito
        raise NotImplementedError

    def on_ok(self) -> None:  # pragma: no cover - sobrescrito
        raise NotImplementedError

    def on_cancel(self) -> None:
        self.result = False
        self.destroy()

    def show(self) -> bool:
        center_on_parent(self, self.parent)
        bring_to_front(self)
        self.grab_set()
        self.wait_window(self)
        return self.result

    def error(self, message: str) -> None:
        messagebox.showerror("Erro", message, parent=self)


class SetPasswordDialog(_ModalDialog):
    """Primeira execução: define a senha do aplicativo."""

    ok_label = "Salvar"

    def __init__(self, parent: tk.Misc, on_save: Callable[[str], None]):
        self._on_save = on_save
        self.pwd_var = tk.StringVar()
        self.confirm_var = tk.StringVar()
        super().__init__(parent, "Definir senha do aplicativo")

    def build_body(self, body: ttk.Frame) -> None:
        ttk.Label(body, text="Defina uma senha para proteger o visualizador OData:").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        ttk.Label(body, text="Senha:").grid(row=1, column=0, sticky="e", pady=3)
        entry = ttk.Entry(body, textvariable=self.pwd_var, show="•", width=28)
        entry.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=3)
        ttk.Label(body, text="Confirmar:").grid(row=2, column=0, sticky="e", pady=3)
        ttk.Entry(body, textvariable=self.confirm_var, show="•", width=28).grid(
            row=2, column=1, sticky="w", padx=(6, 0), pady=3
        )
        ttk.Label(
            body,
            text=f"Mínimo de {MIN_PASSWORD_LENGTH} caracteres.",
            foreground="gray",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        entry.focus_set()

    def on_ok(self) -> None:
        pwd = self.pwd_var.get()
        if len(pwd) < MIN_PASSWORD_LENGTH:
            self.error(f"A senha deve ter pelo menos {MIN_PASSWORD_LENGTH} caracteres.")
            return
        if pwd != self.confirm_var.get():
            self.error("As senhas não conferem.")
            return
        self._on_save(pwd)
        log.info("Senha do aplicativo definida.")
        self.result = True
        self.destroy()


class LoginDialog(_ModalDialog):
    """Solicita a senha já cadastrada; ``verify`` retorna True se estiver correta."""

    ok_label = "Entrar"

    def __init__(self, parent: tk.Misc, verify: Callable[[str], bool]):
        self._verify = verify
        self._attempts = 0
        self.pwd_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self._entry: Optional[ttk.Entry] = None
        super().__init__(parent, "Login")

    def build_body(self, body: ttk.Frame) -> None:
        ttk.Label(body, text="Informe a senha do visualizador OData:").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        ttk.Label(body, text="Senha:").grid(row=1, column=0, sticky="e")
        self._entry = ttk.Entry(body, textvariable=self.pwd_var, show="•", width=28)
        self._entry.grid(row=1, column=1, sticky="w", padx=(6, 0))
        ttk.Label(body, textvariable=self.status_var, foreground="#b00020").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        self._entry.focus_set()

    def on_ok(self) -> None:
        pwd = self.pwd_var.get()
        if not pwd:
            self.status_var.set("Informe a senha.")
            return
        if self._verify(pwd):
            log.info("Login do aplicativo bem-sucedido.")
            self.result = True
            self.destroy()
            return

        self._attempts += 1
        log.warning("Tentativa de login com senha incorreta (%d).", self._attempts)
        remaining = MAX_LOGIN_ATTEMPTS - self._attempts
        if remaining <= 0:
            self.error("Número máximo de tentativas excedido.")
            self.on_cancel()
            return
        self.status_var.set(f"Senha incorreta. {remaining} tentativa(s) restante(s).")
        self.pwd_var.set("")
        if self._entry is not None:
            self._entry.focus_set()
