import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import os
from urllib.parse import urlparse
from datetime import datetime, timedelta
import hashlib
import xml.etree.ElementTree as ET
import traceback
import sys

try:
    import requests
except ImportError:
    requests = None

# =========================== LOGGING ========================================

LOG_FILE = "catalog_viewer.log"


def log(msg: str):
    """Registra mensagens em arquivo de log com timestamp."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        print(line)


def excepthook(exc_type, exc_value, exc_tb):
    """Captura exceções não tratadas e joga no log."""
    log(
        "EXCEPTION FATAL:\n"
        + "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    )


sys.excepthook = excepthook

# ============================================================================


class CatalogApp(tk.Tk):
    def __init__(self):
        log("Inicializando CatalogApp...")
        super().__init__()

        self.title("Visualizador de Serviços OData (CATALOGSERVICE)")
        self.geometry("1100x600")
        self.minsize(900, 500)

        # Caminho do arquivo de configuração
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_path = os.path.join(base_dir, "catalog_viewer_config.json")
        log(f"Caminho de config: {self.config_path}")

        # Estado interno
        self.services = []
        self.last_selected_tech_name = None
        self.config_data = {}

        # Estruturas de metadata parseado
        self.entitytype_properties = {}  # full_name -> list of props

        # Variáveis de UI básicas
        self.base_url_var = tk.StringVar(
            value="https://seu-servidor:porta/sap/opu/odata/IWFND/CATALOGSERVICE;v=2"
        )
        self.user_var = tk.StringVar()
        self.pass_var = tk.StringVar()
        self.ignore_ssl_var = tk.BooleanVar(value=True)

        # Fonte de dados: local ou online
        self.data_source_var = tk.StringVar(value="local")  # "local" ou "online"
        self.local_metadata_path_var = tk.StringVar(value="metadata.json")

        # Detalhes do serviço
        self.detail_vars = {
            "TechnicalServiceName": tk.StringVar(),
            "Version": tk.StringVar(),
            "ServiceDescription": tk.StringVar(),
            "ServicePath": tk.StringVar(),
            "ServiceUrl": tk.StringVar(),
        }

        # Widgets que vamos precisar em outros métodos
        self.tree = None
        self.summary_text = None
        self.metadata_text = None
        self.metadata_source_var = tk.StringVar(value="Nenhum $metadata carregado.")
        self.entitysets_tree = None
        self.entitytypes_tree = None
        self.properties_tree = None

        # Carrega configurações salvas (se existirem)
        self.load_config()

        # Monta UI
        log("Construindo UI...")
        self._build_ui()
        log("UI construída.")

        # Restaura geometria se salva
        geom = self.config_data.get("window_geometry")
        if geom:
            log(f"Tentando restaurar geometry salva: {geom}")
            try:
                self.geometry(geom)
            except Exception as e:
                log(f"Falha ao aplicar geometry salva: {e}")

        # Failsafe: garante que a janela não fique 1x1 perdida na tela
        self.after(200, self._ensure_min_geometry)

        # Ao fechar, salvar estado
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Processo de login / sessão (sem withdraw!)
        self.after(100, self.post_init_login_check)

    # ==================== CONFIGURAÇÃO (SALVAR/CARREGAR) ====================

    def load_config(self):
        if not os.path.exists(self.config_path):
            log("Arquivo de configuração não encontrado, usando defaults.")
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.config_data = json.load(f)
            log("Configuração carregada com sucesso.")
        except Exception as e:
            log(f"Falha ao carregar configuração: {e}")
            self.config_data = {}
            return

        # Restaura valores nas variáveis
        self.base_url_var.set(self.config_data.get("base_url", self.base_url_var.get()))
        self.user_var.set(self.config_data.get("username", ""))
        # senha não é restaurada de propósito (segurança)
        self.ignore_ssl_var.set(self.config_data.get("ignore_ssl", True))
        self.data_source_var.set(self.config_data.get("data_source", "local"))
        self.local_metadata_path_var.set(
            self.config_data.get("local_metadata_path", "metadata.json")
        )
        self.last_selected_tech_name = self.config_data.get("last_selected_tech_name")
        log(
            f"Estado restaurado: data_source={self.data_source_var.get()}, "
            f"local_metadata_path={self.local_metadata_path_var.get()}, "
            f"last_selected_tech_name={self.last_selected_tech_name}"
        )

    def save_config(self):
        self.config_data["base_url"] = self.base_url_var.get().strip()
        self.config_data["username"] = self.user_var.get().strip()
        self.config_data["ignore_ssl"] = bool(self.ignore_ssl_var.get())
        self.config_data["data_source"] = self.data_source_var.get()
        self.config_data["local_metadata_path"] = self.local_metadata_path_var.get()
        self.config_data["last_selected_tech_name"] = self.last_selected_tech_name

        # Geometria atual da janela
        try:
            self.config_data["window_geometry"] = self.geometry()
        except Exception as e:
            log(f"Falha ao obter geometry para salvar: {e}")

        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, ensure_ascii=False, indent=2)
            log("Configuração salva.")
        except Exception as e:
            log(f"Falha ao salvar config: {e}")
            print(f"Falha ao salvar config: {e}")

    # ========================= LOGIN / SESSÃO ================================

    def post_init_login_check(self):
        """Verifica sessão de 24h e controla fluxo de login."""
        log("Checando sessão...")
        if self.is_session_valid():
            log("Sessão ainda válida. Pulando tela de login.")
            self.auto_load_on_start()
            return

        log("Sessão inválida ou expirada. Solicitando login.")

        if not self.ensure_app_password():
            log("Login não realizado. Encerrando aplicação.")
            self.destroy()
            return

        self.start_new_session()
        log("Login realizado com sucesso. Nova sessão iniciada.")
        self.auto_load_on_start()

    def is_session_valid(self) -> bool:
        exp_str = self.config_data.get("session_expires_at")
        if not exp_str:
            log("Nenhum 'session_expires_at' encontrado na config.")
            return False
        try:
            exp = datetime.fromisoformat(exp_str)
        except Exception as e:
            log(f"Erro ao converter session_expires_at: {e}")
            return False
        valid = datetime.now() < exp
        log(f"Resultado da verificação de sessão: {valid} (expira em {exp_str})")
        return valid

    def start_new_session(self):
        expires = datetime.now() + timedelta(hours=24)
        self.config_data["session_expires_at"] = expires.isoformat()
        log(f"Nova sessão salva com expiração em {expires.isoformat()}")
        self.save_config()

    def ensure_app_password(self) -> bool:
        """
        Garante que exista uma senha definida e realiza o login.
        Retorna True se o login for bem sucedido, False em caso de cancelamento.
        """
        pwd_hash = self.config_data.get("app_password_hash")

        if not pwd_hash:
            log("Nenhuma senha configurada. Solicitando criação de senha.")
            if not self.show_set_password_dialog():
                log("Usuário cancelou a definição de senha.")
                return False

        return self.show_login_dialog()

    # ---------- helpers para centralizar diálogos ---------------------------

    def _center_dialog(self, dialog: tk.Toplevel):
        """Centraliza o dialog em relação à janela principal."""
        self.update_idletasks()
        dialog.update_idletasks()

        x = self.winfo_rootx()
        y = self.winfo_rooty()
        w = self.winfo_width()
        h = self.winfo_height()

        dw = dialog.winfo_reqwidth()
        dh = dialog.winfo_reqheight()

        pos_x = x + (w - dw) // 2
        pos_y = y + (h - dh) // 2

        dialog.geometry(f"+{max(pos_x, 0)}+{max(pos_y, 0)}")

    def show_set_password_dialog(self) -> bool:
        """Dialogo para definir a senha do aplicativo (primeira vez)."""
        log("Exibindo diálogo para definição de senha do app.")
        dialog = tk.Toplevel(self)
        dialog.title("Definir senha do aplicativo")
        dialog.transient(self)
        dialog.resizable(False, False)

        ttk.Label(dialog, text="Defina uma senha para o visualizador OData:").grid(
            row=0, column=0, columnspan=2, padx=10, pady=(10, 5), sticky="w"
        )

        ttk.Label(dialog, text="Senha:").grid(row=1, column=0, padx=10, pady=5, sticky="e")
        ttk.Label(dialog, text="Confirmar:").grid(
            row=2, column=0, padx=10, pady=5, sticky="e"
        )

        pwd_var = tk.StringVar()
        pwd_conf_var = tk.StringVar()

        entry_pwd = ttk.Entry(dialog, textvariable=pwd_var, show="*")
        entry_conf = ttk.Entry(dialog, textvariable=pwd_conf_var, show="*")
        entry_pwd.grid(row=1, column=1, padx=10, pady=5)
        entry_conf.grid(row=2, column=1, padx=10, pady=5)

        result = {"ok": False}

        def on_ok():
            pwd = pwd_var.get()
            conf = pwd_conf_var.get()
            if not pwd:
                messagebox.showerror(
                    "Erro", "A senha não pode ser vazia.", parent=dialog
                )
                return
            if pwd != conf:
                messagebox.showerror(
                    "Erro", "As senhas não conferem.", parent=dialog
                )
                return

            h = hashlib.sha256(pwd.encode("utf-8")).hexdigest()
            self.config_data["app_password_hash"] = h
            log("Senha do app definida e hash salva na configuração.")
            self.save_config()
            result["ok"] = True
            dialog.destroy()

        def on_cancel():
            log("Usuário cancelou definição de senha.")
            result["ok"] = False
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=3, column=0, columnspan=2, pady=10)
        ttk.Button(btn_frame, text="Cancelar", command=on_cancel).pack(
            side=tk.RIGHT, padx=5
        )
        ttk.Button(btn_frame, text="Salvar", command=on_ok).pack(
            side=tk.RIGHT, padx=5
        )

        dialog.bind("<Return>", lambda e: on_ok())
        dialog.bind("<Escape>", lambda e: on_cancel())
        entry_pwd.focus_set()

        # centraliza e garante que fique na frente
        self._center_dialog(dialog)
        dialog.lift()
        dialog.attributes("-topmost", True)
        dialog.after(100, lambda: dialog.attributes("-topmost", False))

        dialog.grab_set()
        self.wait_window(dialog)
        return result["ok"]

    def show_login_dialog(self) -> bool:
        """Dialogo de login (usa a senha já cadastrada)."""
        log("Exibindo diálogo de login do app.")
        dialog = tk.Toplevel(self)
        dialog.title("Login")
        dialog.transient(self)
        dialog.resizable(False, False)

        ttk.Label(dialog, text="Informe a senha do visualizador OData:").grid(
            row=0, column=0, columnspan=2, padx=10, pady=(10, 5), sticky="w"
        )

        ttk.Label(dialog, text="Senha:").grid(row=1, column=0, padx=10, pady=5, sticky="e")

        pwd_var = tk.StringVar()
        entry_pwd = ttk.Entry(dialog, textvariable=pwd_var, show="*")
        entry_pwd.grid(row=1, column=1, padx=10, pady=5)

        result = {"ok": False}

        def on_ok():
            pwd = pwd_var.get()
            if not pwd:
                messagebox.showerror(
                    "Erro", "Informe a senha.", parent=dialog
                )
                return

            h = hashlib.sha256(pwd.encode("utf-8")).hexdigest()
            if h != self.config_data.get("app_password_hash"):
                log("Tentativa de login com senha incorreta.")
                messagebox.showerror(
                    "Erro", "Senha incorreta.", parent=dialog
                )
                return

            log("Login do app bem-sucedido.")
            result["ok"] = True
            dialog.destroy()

        def on_cancel():
            log("Usuário cancelou o login.")
            result["ok"] = False
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=10)
        ttk.Button(btn_frame, text="Cancelar", command=on_cancel).pack(
            side=tk.RIGHT, padx=5
        )
        ttk.Button(btn_frame, text="Entrar", command=on_ok).pack(
            side=tk.RIGHT, padx=5
        )

        dialog.bind("<Return>", lambda e: on_ok())
        dialog.bind("<Escape>", lambda e: on_cancel())
        entry_pwd.focus_set()

        self._center_dialog(dialog)
        dialog.lift()
        dialog.attributes("-topmost", True)
        dialog.after(100, lambda: dialog.attributes("-topmost", False))

        dialog.grab_set()
        self.wait_window(dialog)
        return result["ok"]

    # ============================== UI ======================================

    def _build_ui(self):
        # Barra superior
        top = ttk.Frame(self, padding=10)
        top.pack(side=tk.TOP, fill=tk.X)

        # Fonte de dados
        source_frame = ttk.LabelFrame(top, text="Fonte de dados", padding=5)
        source_frame.grid(row=0, column=0, columnspan=7, sticky="we", pady=(0, 8))

        ttk.Radiobutton(
            source_frame,
            text="Local (metadata.json)",
            value="local",
            variable=self.data_source_var,
            command=self.on_data_source_change,
        ).grid(row=0, column=0, sticky="w")

        ttk.Radiobutton(
            source_frame,
            text="Online (CATALOGSERVICE)",
            value="online",
            variable=self.data_source_var,
            command=self.on_data_source_change,
        ).grid(row=0, column=1, sticky="w", padx=(15, 0))

        # Caminho arquivo local
        ttk.Label(source_frame, text="Arquivo local:").grid(
            row=1, column=0, sticky="w", pady=(5, 0)
        )
        entry_local = ttk.Entry(
            source_frame, textvariable=self.local_metadata_path_var, width=60
        )
        entry_local.grid(row=1, column=1, sticky="we", pady=(5, 0))
        ttk.Button(
            source_frame, text="Procurar...", command=self.browse_metadata_file
        ).grid(row=1, column=2, padx=(5, 0), pady=(5, 0))

        source_frame.columnconfigure(1, weight=1)

        # Configuração online
        ttk.Label(top, text="URL CATALOGSERVICE:").grid(
            row=1, column=0, sticky="w", pady=(2, 0)
        )
        ttk.Entry(top, textvariable=self.base_url_var, width=80).grid(
            row=1, column=1, columnspan=4, sticky="we", padx=5, pady=(2, 0)
        )

        ttk.Label(top, text="Usuário SAP:").grid(
            row=2, column=0, sticky="w", pady=(5, 0)
        )
        ttk.Entry(top, textvariable=self.user_var, width=20).grid(
            row=2, column=1, sticky="w", padx=5, pady=(5, 0)
        )

        ttk.Label(top, text="Senha SAP:").grid(
            row=2, column=2, sticky="w", pady=(5, 0)
        )
        ttk.Entry(top, textvariable=self.pass_var, width=20, show="*").grid(
            row=2, column=3, sticky="w", padx=5, pady=(5, 0)
        )

        ttk.Checkbutton(
            top,
            text="Ignorar certificado SSL (autoassinado)",
            variable=self.ignore_ssl_var,
            command=self.save_config,
        ).grid(row=2, column=4, sticky="w", padx=5, pady=(5, 0))

        load_btn = ttk.Button(top, text="Carregar serviços", command=self.load_services)
        load_btn.grid(row=1, column=5, rowspan=2, padx=(10, 0))

        export_btn = ttk.Button(
            top, text="Exportar lista (CSV)", command=self.export_services_csv
        )
        export_btn.grid(row=1, column=6, rowspan=2, padx=(5, 0))

        top.columnconfigure(1, weight=1)

        # Área principal com abas
        main = ttk.Frame(self, padding=(10, 0, 10, 10))
        main.pack(fill=tk.BOTH, expand=True)

        notebook = ttk.Notebook(main)
        notebook.pack(fill=tk.BOTH, expand=True)

        # Aba 1: Serviços
        services_tab = ttk.Frame(notebook)
        notebook.add(services_tab, text="Serviços OData")

        # Layout da aba Serviços: esquerda (lista), direita (detalhes)
        left = ttk.Frame(services_tab)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right = ttk.Frame(services_tab)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # Treeview de serviços
        columns = ("TechnicalServiceName", "Version", "ServiceDescription", "ServicePath")
        self.tree = ttk.Treeview(left, columns=columns, show="headings")
        headings = {
            "TechnicalServiceName": "Nome Técnico",
            "Version": "Versão",
            "ServiceDescription": "Descrição",
            "ServicePath": "Caminho do Serviço",
        }
        widths = {
            "TechnicalServiceName": 160,
            "Version": 60,
            "ServiceDescription": 260,
            "ServicePath": 260,
        }
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor="w")

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll_y = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_y.set)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        # Painel de detalhes
        detail_frame = ttk.LabelFrame(right, text="Detalhes do Serviço", padding=10)
        detail_frame.pack(fill=tk.BOTH, expand=True)

        row = 0
        ttk.Label(detail_frame, text="Nome técnico:").grid(row=row, column=0, sticky="nw")
        ttk.Label(
            detail_frame,
            textvariable=self.detail_vars["TechnicalServiceName"],
            wraplength=350,
        ).grid(row=row, column=1, sticky="nw")
        row += 1

        ttk.Label(detail_frame, text="Descrição:").grid(row=row, column=0, sticky="nw")
        ttk.Label(
            detail_frame,
            textvariable=self.detail_vars["ServiceDescription"],
            wraplength=350,
        ).grid(row=row, column=1, sticky="nw")
        row += 1

        ttk.Label(detail_frame, text="Versão:").grid(row=row, column=0, sticky="nw")
        ttk.Label(
            detail_frame,
            textvariable=self.detail_vars["Version"],
        ).grid(row=row, column=1, sticky="nw")
        row += 1

        ttk.Label(detail_frame, text="Caminho OData:").grid(row=row, column=0, sticky="nw")
        ttk.Label(
            detail_frame,
            textvariable=self.detail_vars["ServicePath"],
            wraplength=350,
        ).grid(row=row, column=1, sticky="nw")
        row += 1

        ttk.Label(detail_frame, text="URL completa:").grid(row=row, column=0, sticky="nw")
        url_frame = ttk.Frame(detail_frame)
        url_frame.grid(row=row, column=1, sticky="nw")
        ttk.Label(
            url_frame,
            textvariable=self.detail_vars["ServiceUrl"],
            wraplength=350,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(url_frame, text="Copiar", command=self.copy_url).pack(
            side=tk.RIGHT, padx=(5, 0)
        )
        row += 1

        # Área de "explicação" em linguagem natural
        ttk.Label(detail_frame, text="Resumo do que o serviço faz:").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        row += 1

        self.summary_text = tk.Text(
            detail_frame, height=8, wrap="word", state="disabled"
        )
        self.summary_text.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=(2, 0))

        detail_frame.columnconfigure(1, weight=1)
        detail_frame.rowconfigure(row, weight=1)

        # Aba 2: $metadata
        metadata_tab = ttk.Frame(notebook)
        notebook.add(metadata_tab, text="$metadata do Serviço")

        meta_top = ttk.Frame(metadata_tab, padding=(0, 5, 0, 5))
        meta_top.pack(fill=tk.X)

        ttk.Button(
            meta_top,
            text="Carregar $metadata (online)",
            command=self.load_metadata_for_selected_service,
        ).pack(side=tk.LEFT, padx=(0, 5))

        ttk.Button(
            meta_top,
            text="Carregar $metadata (arquivo local)",
            command=self.load_metadata_from_file,
        ).pack(side=tk.LEFT, padx=(0, 5))

        ttk.Label(meta_top, textvariable=self.metadata_source_var).pack(
            side=tk.LEFT, padx=5
        )

        # Área principal da aba $metadata
        meta_main = ttk.Frame(metadata_tab)
        meta_main.pack(fill=tk.BOTH, expand=True)

        # Painel esquerdo: EntitySets + EntityTypes/Propriedades
        left_meta = ttk.Frame(meta_main)
        left_meta.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # EntitySets
        entity_frame = ttk.LabelFrame(left_meta, text="EntitySets encontrados", padding=5)
        entity_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        es_columns = ("Name", "EntityType", "Doc")
        self.entitysets_tree = ttk.Treeview(
            entity_frame,
            columns=es_columns,
            show="headings",
            height=10,
        )
        self.entitysets_tree.heading("Name", text="Name")
        self.entitysets_tree.heading("EntityType", text="EntityType")
        self.entitysets_tree.heading("Doc", text="Documentação / Summary")

        self.entitysets_tree.column("Name", width=160, anchor="w")
        self.entitysets_tree.column("EntityType", width=260, anchor="w")
        self.entitysets_tree.column("Doc", width=260, anchor="w")

        self.entitysets_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        es_scroll_y = ttk.Scrollbar(
            entity_frame, orient="vertical", command=self.entitysets_tree.yview
        )
        self.entitysets_tree.configure(yscrollcommand=es_scroll_y.set)
        es_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        # EntityTypes e Propriedades
        type_frame = ttk.LabelFrame(left_meta, text="EntityTypes e Propriedades", padding=5)
        type_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True, pady=(5, 0))

        # Tree de EntityTypes (esquerda)
        types_frame_inner = ttk.Frame(type_frame)
        types_frame_inner.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        et_columns = ("Name", "BaseType", "Doc")
        self.entitytypes_tree = ttk.Treeview(
            types_frame_inner,
            columns=et_columns,
            show="headings",
            height=8,
        )
        self.entitytypes_tree.heading("Name", text="EntityType")
        self.entitytypes_tree.heading("BaseType", text="BaseType")
        self.entitytypes_tree.heading("Doc", text="Documentação")

        self.entitytypes_tree.column("Name", width=200, anchor="w")
        self.entitytypes_tree.column("BaseType", width=200, anchor="w")
        self.entitytypes_tree.column("Doc", width=260, anchor="w")

        self.entitytypes_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        et_scroll_y = ttk.Scrollbar(
            types_frame_inner, orient="vertical", command=self.entitytypes_tree.yview
        )
        self.entitytypes_tree.configure(yscrollcommand=et_scroll_y.set)
        et_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        self.entitytypes_tree.bind("<<TreeviewSelect>>", self.on_entitytype_select)

        # Tree de propriedades (direita)
        props_frame = ttk.Frame(type_frame)
        props_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        prop_columns = ("Name", "Type", "Nullable", "MaxLength", "Doc")
        self.properties_tree = ttk.Treeview(
            props_frame,
            columns=prop_columns,
            show="headings",
            height=8,
        )
        self.properties_tree.heading("Name", text="Propriedade")
        self.properties_tree.heading("Type", text="Tipo")
        self.properties_tree.heading("Nullable", text="Nullable")
        self.properties_tree.heading("MaxLength", text="MaxLength")
        self.properties_tree.heading("Doc", text="Documentação")

        self.properties_tree.column("Name", width=160, anchor="w")
        self.properties_tree.column("Type", width=160, anchor="w")
        self.properties_tree.column("Nullable", width=70, anchor="center")
        self.properties_tree.column("MaxLength", width=80, anchor="center")
        self.properties_tree.column("Doc", width=260, anchor="w")

        self.properties_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        prop_scroll_y = ttk.Scrollbar(
            props_frame, orient="vertical", command=self.properties_tree.yview
        )
        self.properties_tree.configure(yscrollcommand=prop_scroll_y.set)
        prop_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        # Painel direito: XML bruto do $metadata
        meta_frame = ttk.Frame(meta_main)
        meta_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.metadata_text = tk.Text(meta_frame, wrap="none")
        self.metadata_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        meta_scroll_y = ttk.Scrollbar(
            meta_frame, orient="vertical", command=self.metadata_text.yview
        )
        meta_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.metadata_text.configure(yscrollcommand=meta_scroll_y.set)

        meta_scroll_x = ttk.Scrollbar(
            metadata_tab, orient="horizontal", command=self.metadata_text.xview
        )
        meta_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.metadata_text.configure(xscrollcommand=meta_scroll_x.set)

        # Rodapé
        footer = ttk.Frame(self, padding=(10, 0, 10, 10))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(
            footer,
            text="Dica: a lista vem do ServiceCollection do CATALOGSERVICE (ou de um JSON equivalente em disco).",
        ).pack(side=tk.LEFT)

        self.update_fields_state()

    # ========================== EVENTOS UI ===================================

    def on_data_source_change(self):
        log(f"Fonte de dados alterada para: {self.data_source_var.get()}")
        self.update_fields_state()
        self.save_config()

    def update_fields_state(self):
        pass

    def browse_metadata_file(self):
        initial = self.local_metadata_path_var.get() or "metadata.json"
        file_path = filedialog.askopenfilename(
            title="Selecione o metadata.json",
            filetypes=[("Arquivos JSON", "*.json"), ("Todos os arquivos", "*.*")],
            initialfile=os.path.basename(initial),
        )
        if file_path:
            log(f"Arquivo de metadata local selecionado: {file_path}")
            self.local_metadata_path_var.set(file_path)
            self.save_config()

    def on_close(self):
        log("Fechando aplicação. Salvando configuração e destruindo janela.")
        self.save_config()
        self.destroy()

    def auto_load_on_start(self):
        log("auto_load_on_start chamado. Carregando serviços...")
        self.load_services()

    # ========================== LÓGICA PRINCIPAL =============================

    def _ensure_min_geometry(self):
        """Garante que a janela não fique invisível (ex: 1x1)."""
        try:
            current = self.geometry()
            log(f"Geometry após inicialização: {current}")
            size_part = current.split("+")[0]
            w_str, h_str = size_part.split("x")
            w = int(w_str)
            h = int(h_str)
            if w < 400 or h < 300:
                self.geometry("1100x600+100+100")
                log(
                    f"Geometry muito pequena detectada ({w}x{h}). "
                    "Redefinida para 1100x600+100+100."
                )
        except Exception as e:
            log(f"Erro ao validar geometry mínima: {e}")

    # --------- normalização dos registros de serviço ------------------------

    def normalize_services(self, results):
        """
        Ajusta os registros vindos tanto do JSON local quanto do CATALOGSERVICE
        para sempre terem:
        - Version
        - ServiceDescription
        - ServicePath
        Mantém também ServiceUrl se existir.
        """
        normalized = []
        for svc in results:
            if not isinstance(svc, dict):
                continue

            svc = dict(svc)  # cópia

            # Versão
            if not svc.get("Version"):
                v = svc.get("TechnicalServiceVersion")
                if isinstance(v, (int, float)):
                    v = str(int(v))
                elif v is None:
                    v = ""
                svc["Version"] = v

            # Descrição
            if not svc.get("ServiceDescription"):
                desc = (
                    svc.get("ServiceDescription")
                    or svc.get("Description")
                    or svc.get("Title")
                    or ""
                )
                svc["ServiceDescription"] = desc

            # Caminho do serviço
            if not svc.get("ServicePath"):
                sp = svc.get("ServicePath")
                if not sp:
                    surl = svc.get("ServiceUrl") or svc.get("MetadataUrl")
                    if surl:
                        try:
                            parsed = urlparse(surl)
                            sp = parsed.path
                        except Exception as e:
                            log(
                                f"Falha ao obter ServicePath de ServiceUrl '{surl}': {e}"
                            )
                            sp = ""
                svc["ServicePath"] = sp or ""

            normalized.append(svc)

        log(
            f"Normalização de serviços concluída. "
            f"{len(normalized)} registros processados."
        )
        return normalized

    def load_services(self):
        source = self.data_source_var.get()
        log(f"load_services chamado. Fonte={source}")
        self.save_config()

        if source == "local":
            self.load_services_local()
        else:
            self.load_services_online()

    def load_services_local(self):
        path = self.local_metadata_path_var.get().strip()
        log(f"Carregando serviços localmente a partir de: {path}")
        if not path:
            messagebox.showerror(
                "Arquivo não informado",
                "Informe o caminho do arquivo metadata.json (fonte local).",
            )
            log("Erro: caminho de arquivo local não informado.")
            return

        if not os.path.isabs(path):
            base_dir = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(base_dir, path)

        if not os.path.exists(path):
            messagebox.showerror(
                "Arquivo não encontrado",
                f"Não foi possível encontrar o arquivo:\n\n{path}",
            )
            log(f"Erro: arquivo local não encontrado: {path}")
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror(
                "Erro ao ler JSON",
                f"Falha ao carregar o arquivo local:\n\n{e}",
            )
            log(f"Erro ao ler JSON local: {e}")
            return

        results = []
        if isinstance(data, dict):
            if isinstance(data.get("d"), dict) and isinstance(
                data["d"].get("results"), list
            ):
                results = data["d"]["results"]
            elif isinstance(data.get("results"), list):
                results = data["results"]
        elif isinstance(data, list):
            results = data

        if not results:
            messagebox.showinfo(
                "Nenhum serviço",
                "Não foi possível encontrar uma lista de serviços em 'results' "
                "ou em 'd.results' no JSON informado.",
            )
            log("Aviso: nenhum serviço encontrado no JSON local.")

        self.services = self.normalize_services(results)
        log(f"Quantidade de serviços carregados localmente: {len(self.services)}")
        self.refresh_tree()

    def load_services_online(self):
        if requests is None:
            messagebox.showerror(
                "Biblioteca ausente",
                "A biblioteca 'requests' não está instalada.\n\n"
                "Instale com:\n\n    pip install requests",
            )
            log("Erro: biblioteca requests não instalada.")
            return

        base = self.base_url_var.get().strip()
        if not base:
            messagebox.showerror("Erro", "Informe a URL base do CATALOGSERVICE.")
            log("Erro: URL base do CATALOGSERVICE não informada.")
            return

        if not base.endswith("/"):
            base = base + "/"

        url = base + "ServiceCollection?$format=json"
        log(f"Carregando serviços online em: {url}")

        auth = None
        if self.user_var.get():
            auth = (self.user_var.get(), self.pass_var.get() or "")

        try:
            resp = requests.get(
                url,
                auth=auth,
                verify=not self.ignore_ssl_var.get(),
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            messagebox.showerror("Erro ao carregar serviços", str(e))
            log(f"Erro ao carregar serviços online: {e}")
            return

        try:
            data = resp.json()
        except json.JSONDecodeError:
            messagebox.showerror(
                "Erro",
                "A resposta do servidor não é JSON.\n"
                "Verifique se o CATALOGSERVICE está acessível e se o parâmetro ?$format=json foi aceito.",
            )
            log("Erro: resposta do servidor não é JSON.")
            return

        results = data.get("d", {}).get("results", [])
        if not results:
            messagebox.showinfo(
                "Nenhum serviço",
                "Nenhum registro retornado em ServiceCollection.\n"
                "Verifique se o usuário tem autorização no CATALOGSERVICE.",
            )
            log("Aviso: nenhum serviço retornado pelo CATALOGSERVICE.")

        self.services = self.normalize_services(results)
        log(f"Quantidade de serviços carregados online: {len(self.services)}")
        self.refresh_tree()

    def refresh_tree(self):
        log("Atualizando treeview de serviços...")
        for item in self.tree.get_children():
            self.tree.delete(item)

        for idx, svc in enumerate(self.services):
            values = (
                svc.get("TechnicalServiceName", ""),
                svc.get("Version", ""),
                svc.get("ServiceDescription", ""),
                svc.get("ServicePath", ""),
            )
            self.tree.insert("", "end", iid=str(idx), values=values)

        target_index = None
        if self.last_selected_tech_name:
            for i, svc in enumerate(self.services):
                if svc.get("TechnicalServiceName") == self.last_selected_tech_name:
                    target_index = i
                    break

        if target_index is None and self.services:
            target_index = 0

        if target_index is not None:
            iid = str(target_index)
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            log(f"Selecionando serviço índice {target_index} automaticamente.")
            self.show_details(target_index)
        else:
            log("Nenhum serviço para selecionar. Limpando detalhes.")
            self.clear_details()

    def clear_details(self):
        for v in self.detail_vars.values():
            v.set("")
        self.set_summary("Nenhum serviço selecionado.")
        self.clear_metadata_view()

    def on_select(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        try:
            idx = int(sel[0])
        except ValueError:
            return
        if 0 <= idx < len(self.services):
            log(f"Usuário clicou no serviço índice {idx}.")
            self.show_details(idx)

    def build_service_url(self, svc):
        """
        Se existir ServiceUrl no registro (caso do metadata.json local),
        usa diretamente. Caso contrário, monta a partir do ServicePath
        e da base do Gateway.
        """
        service_url = svc.get("ServiceUrl")
        if service_url:
            return service_url

        service_path = svc.get("ServicePath") or ""

        # Se já for URL completa
        if service_path.startswith("http://") or service_path.startswith("https://"):
            return service_path

        base = self.base_url_var.get().strip()
        if not base:
            return service_path

        parsed = urlparse(base)
        if not parsed.scheme or not parsed.netloc:
            return service_path

        root = f"{parsed.scheme}://{parsed.netloc}"

        if service_path.startswith("/"):
            return root + service_path
        else:
            return root + "/" + service_path

    def show_details(self, idx: int):
        svc = self.services[idx]
        tech_name = svc.get("TechnicalServiceName", "")

        log(f"Exibindo detalhes para serviço: {tech_name}")

        self.detail_vars["TechnicalServiceName"].set(tech_name)
        self.detail_vars["Version"].set(svc.get("Version", ""))
        self.detail_vars["ServiceDescription"].set(svc.get("ServiceDescription", ""))
        self.detail_vars["ServicePath"].set(svc.get("ServicePath", ""))

        full_url = self.build_service_url(svc)
        self.detail_vars["ServiceUrl"].set(full_url)

        self.last_selected_tech_name = tech_name or None
        self.save_config()

        self.set_summary(self.build_summary_text(svc, full_url))
        self.clear_metadata_view()

    def set_summary(self, text: str):
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", tk.END)
        self.summary_text.insert(tk.END, text)
        self.summary_text.configure(state="disabled")

    def build_summary_text(self, svc, full_url: str) -> str:
        tech = svc.get("TechnicalServiceName") or "(sem nome técnico)"
        desc = svc.get("ServiceDescription") or "(sem descrição)"
        ver = svc.get("Version") or "(sem versão)"
        path = svc.get("ServicePath") or "(sem caminho)"

        summary = [
            f"Serviço OData: {tech}",
            "",
            f"Descrição informada no catálogo: {desc}",
            "",
            f"Versão do serviço: {ver}",
            f"Path registrado no Gateway: {path}",
            "",
            "Na prática, este serviço expõe entidades OData que podem ser",
            "consumidas por aplicações Fiori / UI5, relatórios externos ou",
            "integrações via HTTP (GET/POST/PUT/etc.).",
            "",
            "URL base sugerida para consumo:",
            full_url or "(não foi possível montar a URL)",
            "",
            "Dica: anexe '/$metadata' ao final da URL para inspecionar o modelo",
            "de dados (entity sets, entity types, propriedades, etc.).",
        ]

        return "\n".join(summary)

    def copy_url(self):
        url = self.detail_vars["ServiceUrl"].get()
        if not url:
            messagebox.showinfo("Nada para copiar", "Nenhuma URL de serviço disponível.")
            return
        self.clipboard_clear()
        self.clipboard_append(url)
        self.update_idletasks()
        log(f"URL copiada para a área de transferência: {url}")
        messagebox.showinfo("Copiado", "URL copiada para a área de transferência.")

    # =================== $METADATA (ABA 2) ===================================

    def clear_metadata_view(self):
        self.metadata_text.configure(state="normal")
        self.metadata_text.delete("1.0", tk.END)
        self.metadata_text.configure(state="normal")
        self.metadata_source_var.set("Nenhum $metadata carregado.")

        if self.entitysets_tree is not None:
            for item in self.entitysets_tree.get_children():
                self.entitysets_tree.delete(item)

        self.entitytype_properties = {}
        if self.entitytypes_tree is not None:
            for item in self.entitytypes_tree.get_children():
                self.entitytypes_tree.delete(item)
        if self.properties_tree is not None:
            for item in self.properties_tree.get_children():
                self.properties_tree.delete(item)

    def load_metadata_for_selected_service(self):
        if requests is None:
            messagebox.showerror(
                "Biblioteca ausente",
                "A biblioteca 'requests' não está instalada.\n\n"
                "Instale com:\n\n    pip install requests",
            )
            log("Erro: biblioteca requests não instalada ao tentar carregar $metadata.")
            return

        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(
                "Seleção necessária",
                "Selecione um serviço na aba 'Serviços OData' primeiro.",
            )
            log("Usuário tentou carregar $metadata sem selecionar serviço.")
            return

        try:
            idx = int(sel[0])
        except ValueError:
            return

        if not (0 <= idx < len(self.services)):
            return

        svc = self.services[idx]
        base_url = self.build_service_url(svc)
        if not base_url:
            messagebox.showerror(
                "URL inválida",
                "Não foi possível montar a URL base do serviço.",
            )
            log("Erro: não foi possível montar URL base ao tentar carregar $metadata.")
            return

        if base_url.endswith("/"):
            meta_url = base_url + "$metadata"
        elif base_url.lower().endswith("$metadata"):
            meta_url = base_url
        else:
            meta_url = base_url + "/$metadata"

        auth = None
        if self.user_var.get():
            auth = (self.user_var.get(), self.pass_var.get() or "")

        log(f"Carregando $metadata online de: {meta_url}")
        try:
            resp = requests.get(
                meta_url,
                auth=auth,
                verify=not self.ignore_ssl_var.get(),
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            messagebox.showerror(
                "Erro ao carregar $metadata",
                f"Falha em {meta_url}:\n\n{e}",
            )
            log(f"Erro ao carregar $metadata online: {e}")
            return

        text = resp.text
        log(f"$metadata recebido (tamanho {len(text)} bytes).")
        self.display_metadata(text, f"$metadata carregado de: {meta_url}")

    def load_metadata_from_file(self):
        file_path = filedialog.askopenfilename(
            title="Selecione o arquivo de $metadata (XML/EDMX)",
            filetypes=[
                ("Arquivos XML/EDMX", "*.xml *.edmx"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if not file_path:
            log("Usuário cancelou seleção de arquivo de $metadata local.")
            return

        log(f"Carregando $metadata de arquivo local: {file_path}")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            messagebox.showerror(
                "Erro ao ler arquivo",
                f"Não foi possível ler o arquivo:\n\n{e}",
            )
            log(f"Erro ao ler arquivo local de $metadata: {e}")
            return

        self.display_metadata(text, f"$metadata carregado de arquivo local: {file_path}")

    def display_metadata(self, text: str, source_label: str):
        log(f"Exibindo $metadata na UI. Fonte: {source_label}")
        self.metadata_text.configure(state="normal")
        self.metadata_text.delete("1.0", tk.END)
        self.metadata_text.insert(tk.END, text)
        self.metadata_text.configure(state="normal")

        self.metadata_source_var.set(source_label)
        self.parse_metadata_and_fill_views(text)

    @staticmethod
    def _localname(tag: str) -> str:
        if "}" in tag:
            return tag.split("}", 1)[1]
        return tag

    def parse_metadata_and_fill_views(self, xml_text: str):
        log("Iniciando parse do XML do $metadata...")
        if self.entitysets_tree is not None:
            for item in self.entitysets_tree.get_children():
                self.entitysets_tree.delete(item)
        if self.entitytypes_tree is not None:
            for item in self.entitytypes_tree.get_children():
                self.entitytypes_tree.delete(item)
        if self.properties_tree is not None:
            for item in self.properties_tree.get_children():
                self.properties_tree.delete(item)
        self.entitytype_properties = {}

        try:
            root = ET.fromstring(xml_text)
        except Exception as e:
            messagebox.showerror(
                "Erro ao parsear $metadata",
                f"Não foi possível interpretar o XML do $metadata:\n\n{e}",
            )
            log(f"Erro ao parsear XML do $metadata: {e}")
            return

        es_count = 0
        for es in root.iter():
            if self._localname(es.tag) == "EntitySet":
                name = es.attrib.get("Name", "")
                etype = es.attrib.get("EntityType", "")
                doc = ""

                for child in es:
                    if self._localname(child.tag) == "Documentation":
                        for ch2 in child:
                            if self._localname(ch2.tag) in (
                                "Summary",
                                "LongDescription",
                                "Description",
                            ):
                                if ch2.text and ch2.text.strip():
                                    doc = ch2.text.strip()
                                    break
                        if doc:
                            break

                if self.entitysets_tree is not None:
                    self.entitysets_tree.insert("", "end", values=(name, etype, doc))
                es_count += 1

        if es_count == 0 and self.entitysets_tree is not None:
            self.entitysets_tree.insert(
                "",
                "end",
                values=("[Nenhum EntitySet encontrado]", "", ""),
            )

        et_count = 0
        for schema in root.iter():
            if self._localname(schema.tag) != "Schema":
                continue

            schema_ns = schema.attrib.get("Namespace", "")

            for et in schema:
                if self._localname(et.tag) != "EntityType":
                    continue

                simple_name = et.attrib.get("Name", "")
                full_name = f"{schema_ns}.{simple_name}" if schema_ns else simple_name
                base_type = et.attrib.get("BaseType", "")
                doc = ""

                for child in et:
                    if self._localname(child.tag) == "Documentation":
                        for ch2 in child:
                            if self._localname(ch2.tag) in (
                                "Summary",
                                "LongDescription",
                                "Description",
                            ):
                                if ch2.text and ch2.text.strip():
                                    doc = ch2.text.strip()
                                    break
                        if doc:
                            break

                props = []
                for child in et:
                    if self._localname(child.tag) == "Property":
                        pname = child.attrib.get("Name", "")
                        ptype = child.attrib.get("Type", "")
                        pnull = child.attrib.get("Nullable", "true")
                        pmax = child.attrib.get("MaxLength", "")
                        pdoc = ""

                        for ch2 in child:
                            if self._localname(ch2.tag) == "Documentation":
                                for ch3 in ch2:
                                    if self._localname(ch3.tag) in (
                                        "Summary",
                                        "LongDescription",
                                        "Description",
                                    ):
                                        if ch3.text and ch3.text.strip():
                                            pdoc = ch3.text.strip()
                                            break
                                if pdoc:
                                    break

                        props.append(
                            {
                                "Name": pname,
                                "Type": ptype,
                                "Nullable": pnull,
                                "MaxLength": pmax,
                                "Doc": pdoc,
                            }
                        )

                self.entitytype_properties[full_name] = props

                if self.entitytypes_tree is not None:
                    self.entitytypes_tree.insert(
                        "",
                        "end",
                        values=(full_name, base_type, doc),
                    )
                et_count += 1

        if et_count == 0 and self.entitytypes_tree is not None:
            self.entitytypes_tree.insert(
                "",
                "end",
                values=("[Nenhum EntityType encontrado]", "", ""),
            )

        log(
            f"Parse de $metadata concluído: {es_count} EntitySets, {et_count} EntityTypes."
        )

    def on_entitytype_select(self, event):
        if self.entitytypes_tree is None or self.properties_tree is None:
            return

        sel = self.entitytypes_tree.selection()
        if not sel:
            return

        item_id = sel[0]
        values = self.entitytypes_tree.item(item_id, "values")
        if not values:
            return

        full_name = values[0]
        log(f"EntityType selecionado: {full_name}")
        props = self.entitytype_properties.get(full_name, [])

        for item in self.properties_tree.get_children():
            self.properties_tree.delete(item)

        if not props:
            self.properties_tree.insert(
                "",
                "end",
                values=("[Sem propriedades]", "", "", "", ""),
            )
            return

        for p in props:
            self.properties_tree.insert(
                "",
                "end",
                values=(
                    p.get("Name", ""),
                    p.get("Type", ""),
                    p.get("Nullable", ""),
                    p.get("MaxLength", ""),
                    p.get("Doc", ""),
                ),
            )

    # ========================= EXPORTAÇÃO CSV ================================

    def export_services_csv(self):
        if not self.services:
            messagebox.showinfo(
                "Sem dados",
                "Não há serviços carregados para exportar.",
            )
            log("Usuário tentou exportar CSV sem serviços carregados.")
            return

        file_path = filedialog.asksavefilename(
            title="Salvar lista de serviços como CSV",
            defaultextension=".csv",
            filetypes=[("Arquivos CSV", "*.csv"), ("Todos os arquivos", "*.*")],
        )
        if not file_path:
            log("Usuário cancelou salvamento do CSV.")
            return

        headers = [
            "TechnicalServiceName",
            "Version",
            "ServiceDescription",
            "ServicePath",
            "ServiceUrlCompleta",
        ]

        try:
            with open(file_path, "w", encoding="utf-8-sig") as f:
                f.write(";".join(headers) + "\n")
                for svc in self.services:
                    tech = svc.get("TechnicalServiceName", "").replace(";", ",")
                    ver = svc.get("Version", "").replace(";", ",")
                    desc = svc.get("ServiceDescription", "").replace(";", ",")
                    path = svc.get("ServicePath", "").replace(";", ",")
                    full_url = self.build_service_url(svc).replace(";", ",")

                    row = [tech, ver, desc, path, full_url]
                    f.write(";".join(row) + "\n")
        except Exception as e:
            messagebox.showerror(
                "Erro ao salvar CSV",
                f"Falha ao escrever o arquivo:\n\n{e}",
            )
            log(f"Erro ao salvar CSV: {e}")
            return

        log(f"CSV exportado com sucesso para: {file_path}")
        messagebox.showinfo(
            "Exportação concluída",
            f"Lista de serviços exportada com sucesso para:\n\n{file_path}",
        )


if __name__ == "__main__":
    log("==== Aplicação iniciada (main) ====")
    print("Iniciando CatalogApp...")
    app = CatalogApp()
    print("Geometry inicial:", app.geometry())
    log(f"Janela criada com geometry inicial: {app.geometry()}")
    print("Entrando no mainloop...")
    app.mainloop()
    log("Aplicação finalizada (mainloop encerrado).")
