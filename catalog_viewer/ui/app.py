"""Janela principal do Visualizador de Serviços OData."""

import json
import os
import queue
import threading
import traceback
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import Any, Callable, List, Optional

from .. import __version__
from ..config import AppConfig, CONFIG_FILE_NAME
from ..logging_setup import log, LOG_FILE_NAME
from ..metadata_parser import (
    MetadataModel,
    MetadataParseError,
    parse_metadata,
    pretty_print_xml,
)
from ..odata_client import ODataClientError, fetch_metadata, fetch_service_collection
from ..security import (
    hash_password,
    needs_rehash,
    new_session_expiry,
    session_is_valid,
    verify_password,
)
from ..services import (
    Service,
    build_service_url,
    export_services_csv,
    extract_results,
    filter_services,
    find_service_index,
    metadata_url_for,
    normalize_services,
)
from .dialogs import LoginDialog, SetPasswordDialog
from .widgets import (
    Debouncer,
    clear_tree,
    copy_to_clipboard,
    make_scrolled_text,
    make_scrolled_tree,
    set_text,
)

APP_TITLE = "Visualizador de Serviços OData (CATALOGSERVICE)"
DEFAULT_GEOMETRY = "1280x760"
MIN_SIZE = (960, 560)
MONO_FONT = ("Consolas", 10)


class CatalogApp(tk.Tk):
    def __init__(self, app_dir: str):
        log.info("Inicializando CatalogApp v%s", __version__)
        super().__init__()
        self.withdraw()  # evita "piscar" a janela 1x1 antes do layout

        self.app_dir = app_dir
        self.config_path = os.path.join(app_dir, CONFIG_FILE_NAME)
        self.cfg = AppConfig.load(self.config_path)

        # Estado
        self.services: List[Service] = []
        self.current_index: Optional[int] = None
        self.metadata_model: Optional[MetadataModel] = None
        self.metadata_xml: str = ""
        self.metadata_service_index: Optional[int] = None
        self.last_online_payload: Any = None
        self._busy = False
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._suppress_select = False

        # Variáveis Tk
        self.base_url_var = tk.StringVar(value=self.cfg.base_url)
        self.user_var = tk.StringVar(value=self.cfg.username)
        self.pass_var = tk.StringVar()  # senha SAP fica apenas em memória
        self.ignore_ssl_var = tk.BooleanVar(value=self.cfg.ignore_ssl)
        self.data_source_var = tk.StringVar(value=self.cfg.data_source)
        self.local_metadata_path_var = tk.StringVar(value=self.cfg.local_metadata_path)
        self.filter_var = tk.StringVar()
        self.count_var = tk.StringVar(value="Nenhum serviço carregado.")
        self.status_var = tk.StringVar(value="Pronto.")
        self.metadata_source_var = tk.StringVar(value="Nenhum $metadata carregado.")
        self.xml_search_var = tk.StringVar()
        self.xml_search_count_var = tk.StringVar()
        self.detail_vars = {
            key: tk.StringVar()
            for key in (
                "TechnicalServiceName",
                "Version",
                "ServiceType",
                "ServiceDescription",
                "ServicePath",
                "ServiceUrl",
                "Author",
                "UpdatedDateFormatted",
            )
        }

        self.title(APP_TITLE)
        self.minsize(*MIN_SIZE)
        self._build_menu()
        self._build_ui()
        self._bind_shortcuts()
        self._restore_geometry()

        self._filter_debouncer = Debouncer(self, 200, self.apply_filter)
        self._save_debouncer = Debouncer(self, 1500, self.save_config)
        self.filter_var.trace_add("write", self._filter_debouncer.trigger)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.deiconify()
        self.after(100, self.post_init_login_check)

    # ------------------------------------------------------------ erros

    def report_callback_exception(self, exc, val, tb):  # noqa: N802 (API Tk)
        """Exceções em callbacks Tk: loga e avisa o usuário em vez de morrer em silêncio."""
        text = "".join(traceback.format_exception(exc, val, tb))
        log.error("Exceção não tratada em callback Tk:\n%s", text)
        try:
            messagebox.showerror(
                "Erro inesperado",
                f"{exc.__name__}: {val}\n\nDetalhes gravados em {LOG_FILE_NAME}.",
                parent=self,
            )
        except tk.TclError:
            pass

    # ------------------------------------------------------------ config

    def _sync_cfg_from_ui(self) -> None:
        self.cfg.base_url = self.base_url_var.get().strip()
        self.cfg.username = self.user_var.get().strip()
        self.cfg.ignore_ssl = bool(self.ignore_ssl_var.get())
        self.cfg.data_source = self.data_source_var.get()
        self.cfg.set_local_metadata_path(self.local_metadata_path_var.get(), self.app_dir)
        try:
            if self.winfo_viewable():
                self.cfg.window_geometry = self.geometry()
        except tk.TclError:
            pass

    def save_config(self) -> None:
        self._sync_cfg_from_ui()
        self.cfg.save(self.config_path)

    def _restore_geometry(self) -> None:
        geom = self.cfg.window_geometry or DEFAULT_GEOMETRY
        try:
            self.geometry(geom)
        except tk.TclError as e:
            log.warning("Geometry salva inválida (%s): %s", geom, e)
            self.geometry(DEFAULT_GEOMETRY)
        self.after(250, self._ensure_visible_geometry)

    def _ensure_visible_geometry(self) -> None:
        """Garante que a janela não fique minúscula nem fora da tela."""
        try:
            self.update_idletasks()
            w, h = self.winfo_width(), self.winfo_height()
            x, y = self.winfo_x(), self.winfo_y()
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            if w < MIN_SIZE[0] or h < MIN_SIZE[1] or x > sw - 100 or y > sh - 100 or x < -w or y < -h:
                log.info("Geometry fora da tela/muito pequena (%dx%d+%d+%d). Redefinindo.", w, h, x, y)
                self.geometry(f"{DEFAULT_GEOMETRY}+60+60")
        except tk.TclError as e:
            log.warning("Erro ao validar geometry: %s", e)

    # ------------------------------------------------------- login/sessão

    def post_init_login_check(self) -> None:
        if session_is_valid(self.cfg.session_expires_at):
            log.info("Sessão válida até %s; pulando login.", self.cfg.session_expires_at)
            self.load_services()
            return

        log.info("Sessão inexistente/expirada; solicitando login.")
        if not self.cfg.app_password_hash:
            if not SetPasswordDialog(self, self._set_app_password).show():
                log.info("Definição de senha cancelada. Encerrando.")
                self.destroy()
                return

        if not LoginDialog(self, self._verify_app_password).show():
            log.info("Login cancelado. Encerrando.")
            self.destroy()
            return

        self.cfg.session_expires_at = new_session_expiry()
        self.save_config()
        log.info("Nova sessão até %s.", self.cfg.session_expires_at)
        self.load_services()

    def _set_app_password(self, password: str) -> None:
        self.cfg.app_password_hash = hash_password(password)
        self.save_config()

    def _verify_app_password(self, password: str) -> bool:
        ok = verify_password(password, self.cfg.app_password_hash)
        if ok and needs_rehash(self.cfg.app_password_hash):
            log.info("Migrando hash de senha para PBKDF2.")
            self._set_app_password(password)
        return ok

    def change_password(self) -> None:
        if self.cfg.app_password_hash and not LoginDialog(self, self._verify_app_password).show():
            return
        if SetPasswordDialog(self, self._set_app_password).show():
            messagebox.showinfo("Senha", "Senha do aplicativo alterada.", parent=self)

    # ----------------------------------------------------------------- UI

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        m_file = tk.Menu(menubar, tearoff=False)
        m_file.add_command(label="Carregar serviços", accelerator="F5", command=self.load_services)
        m_file.add_command(
            label="Exportar lista (CSV)...", accelerator="Ctrl+E", command=self.export_services_csv
        )
        m_file.add_command(
            label="Salvar lista como metadata.json...", command=self.save_services_json
        )
        m_file.add_separator()
        m_file.add_command(label="Alterar senha do aplicativo...", command=self.change_password)
        m_file.add_separator()
        m_file.add_command(label="Sair", accelerator="Alt+F4", command=self.on_close)
        menubar.add_cascade(label="Arquivo", menu=m_file)

        m_meta = tk.Menu(menubar, tearoff=False)
        m_meta.add_command(
            label="Carregar $metadata (online)", accelerator="Ctrl+M",
            command=self.load_metadata_for_selected_service,
        )
        m_meta.add_command(
            label="Carregar $metadata de arquivo...", accelerator="Ctrl+O",
            command=self.load_metadata_from_file,
        )
        m_meta.add_command(label="Salvar $metadata (XML)...", command=self.save_metadata_xml)
        m_meta.add_command(label="Formatar XML", command=self.format_metadata_xml)
        menubar.add_cascade(label="$metadata", menu=m_meta)

        m_help = tk.Menu(menubar, tearoff=False)
        m_help.add_command(label="Abrir pasta do aplicativo", command=self.open_app_folder)
        m_help.add_command(label="Sobre...", command=self.show_about)
        menubar.add_cascade(label="Ajuda", menu=m_help)

        self.config(menu=menubar)

    def _build_ui(self) -> None:
        self._build_top_bar()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))
        self._build_services_tab()
        self._build_metadata_tab()

        self._build_footer()
        self.update_fields_state()

    def _build_top_bar(self) -> None:
        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(side=tk.TOP, fill=tk.X)
        top.columnconfigure(0, weight=1)

        # --- fonte de dados
        source = ttk.LabelFrame(top, text="Fonte de dados", padding=6)
        source.grid(row=0, column=0, sticky="we")
        source.columnconfigure(2, weight=1)

        ttk.Radiobutton(
            source, text="Local (metadata.json)", value="local",
            variable=self.data_source_var, command=self.on_data_source_change,
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            source, text="Online (CATALOGSERVICE)", value="online",
            variable=self.data_source_var, command=self.on_data_source_change,
        ).grid(row=0, column=1, sticky="w", padx=(15, 0))

        ttk.Label(source, text="Arquivo local:").grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.local_path_entry = ttk.Entry(source, textvariable=self.local_metadata_path_var)
        self.local_path_entry.grid(row=1, column=1, columnspan=2, sticky="we", pady=(5, 0))
        self.browse_btn = ttk.Button(source, text="Procurar...", command=self.browse_metadata_file)
        self.browse_btn.grid(row=1, column=3, padx=(5, 0), pady=(5, 0))

        # --- conexão
        conn = ttk.LabelFrame(top, text="Conexão SAP Gateway (usada no modo online e no $metadata)", padding=6)
        conn.grid(row=1, column=0, sticky="we", pady=(6, 0))
        conn.columnconfigure(1, weight=1)

        ttk.Label(conn, text="URL CATALOGSERVICE:").grid(row=0, column=0, sticky="w")
        ttk.Entry(conn, textvariable=self.base_url_var).grid(
            row=0, column=1, columnspan=5, sticky="we", padx=5
        )

        ttk.Label(conn, text="Usuário SAP:").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(conn, textvariable=self.user_var, width=22).grid(
            row=1, column=1, sticky="w", padx=5, pady=(5, 0)
        )
        ttk.Label(conn, text="Senha SAP:").grid(row=1, column=2, sticky="w", pady=(5, 0))
        pw = ttk.Entry(conn, textvariable=self.pass_var, width=22, show="•")
        pw.grid(row=1, column=3, sticky="w", padx=5, pady=(5, 0))
        pw.bind("<Return>", lambda e: self.load_services())
        ttk.Checkbutton(
            conn, text="Ignorar certificado SSL (autoassinado)",
            variable=self.ignore_ssl_var, command=self.save_config,
        ).grid(row=1, column=4, sticky="w", padx=5, pady=(5, 0))

        # --- botões
        btns = ttk.Frame(top)
        btns.grid(row=0, column=1, rowspan=2, sticky="ns", padx=(10, 0))
        self.load_btn = ttk.Button(btns, text="Carregar serviços", command=self.load_services, width=24)
        self.load_btn.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(btns, text="Exportar lista (CSV)", command=self.export_services_csv, width=24).pack(
            fill=tk.X, pady=(0, 4)
        )
        self.save_json_btn = ttk.Button(
            btns, text="Salvar lista como JSON", command=self.save_services_json, width=24
        )
        self.save_json_btn.pack(fill=tk.X)

    def _build_services_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=(0, 6, 0, 0))
        self.notebook.add(tab, text="Serviços OData")

        paned = ttk.PanedWindow(tab, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # --- esquerda: filtro + lista
        left = ttk.Frame(paned)
        paned.add(left, weight=3)

        filter_row = ttk.Frame(left)
        filter_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(filter_row, text="Filtrar:").pack(side=tk.LEFT)
        self.filter_entry = ttk.Entry(filter_row, textvariable=self.filter_var)
        self.filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.filter_entry.bind("<Escape>", lambda e: self.filter_var.set(""))
        ttk.Button(filter_row, text="Limpar", command=lambda: self.filter_var.set("")).pack(side=tk.LEFT)
        ttk.Label(filter_row, textvariable=self.count_var).pack(side=tk.LEFT, padx=(10, 0))

        columns = ("TechnicalServiceName", "Version", "ServiceType", "ServiceDescription", "ServicePath", "UpdatedDateFormatted")
        headings = {
            "TechnicalServiceName": "Nome Técnico",
            "Version": "Versão",
            "ServiceType": "Tipo",
            "ServiceDescription": "Descrição",
            "ServicePath": "Caminho do Serviço",
            "UpdatedDateFormatted": "Atualizado em",
        }
        widths = {
            "TechnicalServiceName": 210,
            "Version": 55,
            "ServiceType": 80,
            "ServiceDescription": 280,
            "ServicePath": 260,
            "UpdatedDateFormatted": 115,
        }
        tree_frame, self.tree = make_scrolled_tree(
            left, columns, headings, widths, anchors={"Version": "center"}
        )
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", lambda e: self.load_metadata_for_selected_service())
        self.tree.bind("<Control-c>", lambda e: self.copy_selected_name())

        # --- direita: detalhes
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        detail = ttk.LabelFrame(right, text="Detalhes do Serviço", padding=10)
        detail.pack(fill=tk.BOTH, expand=True)
        detail.columnconfigure(1, weight=1)

        rows = [
            ("Nome técnico:", "TechnicalServiceName"),
            ("Descrição:", "ServiceDescription"),
            ("Versão:", "Version"),
            ("Tipo:", "ServiceType"),
            ("Autor:", "Author"),
            ("Atualizado em:", "UpdatedDateFormatted"),
            ("Caminho OData:", "ServicePath"),
        ]
        row = 0
        for label, key in rows:
            ttk.Label(detail, text=label).grid(row=row, column=0, sticky="nw", pady=1)
            ttk.Label(detail, textvariable=self.detail_vars[key], wraplength=420).grid(
                row=row, column=1, sticky="nw", pady=1
            )
            row += 1

        ttk.Label(detail, text="URL completa:").grid(row=row, column=0, sticky="nw", pady=1)
        ttk.Label(detail, textvariable=self.detail_vars["ServiceUrl"], wraplength=420).grid(
            row=row, column=1, sticky="nw", pady=1
        )
        row += 1

        actions = ttk.Frame(detail)
        actions.grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(actions, text="Copiar URL", command=self.copy_url).pack(side=tk.LEFT)
        ttk.Button(actions, text="Copiar URL $metadata", command=self.copy_metadata_url).pack(
            side=tk.LEFT, padx=(5, 0)
        )
        ttk.Button(actions, text="Abrir no navegador", command=self.open_in_browser).pack(
            side=tk.LEFT, padx=(5, 0)
        )
        ttk.Button(actions, text="Ver $metadata", command=self.load_metadata_for_selected_service).pack(
            side=tk.LEFT, padx=(5, 0)
        )
        row += 1

        ttk.Label(detail, text="Resumo do serviço:").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(10, 2)
        )
        row += 1
        summary_frame, self.summary_text = make_scrolled_text(detail, wrap="word", height=10)
        summary_frame.grid(row=row, column=0, columnspan=2, sticky="nsew")
        detail.rowconfigure(row, weight=1)

    def _build_metadata_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=(0, 6, 0, 0))
        self.notebook.add(tab, text="$metadata do Serviço")

        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(
            toolbar, text="Carregar $metadata (online)", command=self.load_metadata_for_selected_service
        ).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Carregar de arquivo...", command=self.load_metadata_from_file).pack(
            side=tk.LEFT, padx=(5, 0)
        )
        ttk.Button(toolbar, text="Salvar XML...", command=self.save_metadata_xml).pack(
            side=tk.LEFT, padx=(5, 0)
        )
        ttk.Button(toolbar, text="Formatar XML", command=self.format_metadata_xml).pack(
            side=tk.LEFT, padx=(5, 0)
        )
        ttk.Label(toolbar, textvariable=self.metadata_source_var, foreground="gray").pack(
            side=tk.LEFT, padx=10
        )

        paned = ttk.PanedWindow(tab, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # --- esquerda: modelo
        left = ttk.PanedWindow(paned, orient=tk.VERTICAL)
        paned.add(left, weight=3)

        es_frame = ttk.LabelFrame(left, text="EntitySets", padding=5)
        left.add(es_frame, weight=1)
        es_cols = ("Name", "EntityType", "CRUD", "Doc")
        es_tree_frame, self.entitysets_tree = make_scrolled_tree(
            es_frame, es_cols,
            {"Name": "Name", "EntityType": "EntityType", "CRUD": "C U D", "Doc": "Label / Documentação"},
            {"Name": 180, "EntityType": 240, "CRUD": 60, "Doc": 240},
            anchors={"CRUD": "center"},
        )
        es_tree_frame.pack(fill=tk.BOTH, expand=True)
        self.entitysets_tree.bind("<<TreeviewSelect>>", self.on_entityset_select)

        lower = ttk.Notebook(left)
        left.add(lower, weight=1)

        types_tab = ttk.Frame(lower, padding=5)
        lower.add(types_tab, text="EntityTypes e Propriedades")
        types_paned = ttk.PanedWindow(types_tab, orient=tk.HORIZONTAL)
        types_paned.pack(fill=tk.BOTH, expand=True)

        et_frame, self.entitytypes_tree = make_scrolled_tree(
            types_paned, ("Name", "Keys", "Props", "Doc"),
            {"Name": "EntityType", "Keys": "Chaves", "Props": "Props", "Doc": "Label / Documentação"},
            {"Name": 220, "Keys": 140, "Props": 50, "Doc": 200},
            anchors={"Props": "center"},
        )
        types_paned.add(et_frame, weight=1)
        self.entitytypes_tree.bind("<<TreeviewSelect>>", self.on_entitytype_select)

        pr_frame, self.properties_tree = make_scrolled_tree(
            types_paned, ("Key", "Name", "Type", "Nullable", "MaxLength", "Doc"),
            {"Key": "🔑", "Name": "Propriedade", "Type": "Tipo", "Nullable": "Nullable", "MaxLength": "MaxLength", "Doc": "Label / Documentação"},
            {"Key": 30, "Name": 170, "Type": 130, "Nullable": 65, "MaxLength": 70, "Doc": 220},
            anchors={"Key": "center", "Nullable": "center", "MaxLength": "center"},
        )
        types_paned.add(pr_frame, weight=1)

        fi_tab = ttk.Frame(lower, padding=5)
        lower.add(fi_tab, text="FunctionImports")
        fi_frame, self.functions_tree = make_scrolled_tree(
            fi_tab, ("Name", "Method", "ReturnType", "Params"),
            {"Name": "FunctionImport", "Method": "HTTP", "ReturnType": "Retorno", "Params": "Parâmetros"},
            {"Name": 200, "Method": 60, "ReturnType": 200, "Params": 300},
            anchors={"Method": "center"},
        )
        fi_frame.pack(fill=tk.BOTH, expand=True)

        # --- direita: XML bruto + busca
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        search = ttk.Frame(right)
        search.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(search, text="Buscar no XML:").pack(side=tk.LEFT)
        self.xml_search_entry = ttk.Entry(search, textvariable=self.xml_search_var)
        self.xml_search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.xml_search_entry.bind("<Return>", lambda e: self.xml_search_next())
        self.xml_search_entry.bind("<Shift-Return>", lambda e: self.xml_search_next(backwards=True))
        ttk.Button(search, text="◀", width=3, command=lambda: self.xml_search_next(backwards=True)).pack(side=tk.LEFT)
        ttk.Button(search, text="▶", width=3, command=self.xml_search_next).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Label(search, textvariable=self.xml_search_count_var, width=10).pack(side=tk.LEFT, padx=(6, 0))
        self.xml_search_var.trace_add("write", lambda *_: self.xml_highlight_all())

        xml_frame, self.metadata_text = make_scrolled_text(right, wrap="none", font=MONO_FONT)
        xml_frame.pack(fill=tk.BOTH, expand=True)
        self.metadata_text.tag_configure("match", background="#fff3a0")
        self.metadata_text.tag_configure("current", background="#ffb347")

    def _build_footer(self) -> None:
        footer = ttk.Frame(self, padding=(10, 0, 10, 6))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(footer, textvariable=self.status_var).pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=140)
        ttk.Label(
            footer,
            text="F5 carrega · Ctrl+F filtra · Duplo clique abre o $metadata",
            foreground="gray",
        ).pack(side=tk.RIGHT)

    def _bind_shortcuts(self) -> None:
        self.bind("<F5>", lambda e: self.load_services())
        self.bind("<Control-e>", lambda e: self.export_services_csv())
        self.bind("<Control-m>", lambda e: self.load_metadata_for_selected_service())
        self.bind("<Control-o>", lambda e: self.load_metadata_from_file())
        self.bind("<Control-f>", self._focus_search)

    def _focus_search(self, _event=None):
        if self.notebook.index(self.notebook.select()) == 0:
            self.filter_entry.focus_set()
            self.filter_entry.select_range(0, tk.END)
        else:
            self.xml_search_entry.focus_set()
            self.xml_search_entry.select_range(0, tk.END)
        return "break"

    # ------------------------------------------------------ estado da UI

    def update_fields_state(self) -> None:
        local = self.data_source_var.get() == "local"
        state = "normal" if local else "disabled"
        self.local_path_entry.configure(state=state)
        self.browse_btn.configure(state=state)
        self.load_btn.configure(text="Carregar serviços (local)" if local else "Carregar serviços (online)")
        self.save_json_btn.configure(state="normal" if self.last_online_payload is not None else "disabled")

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self._busy = busy
        self.set_status(status or ("Aguarde..." if busy else "Pronto."))
        self.load_btn.configure(state="disabled" if busy else "normal")
        try:
            self.configure(cursor="watch" if busy else "")
            if busy:
                self.progress.pack(side=tk.LEFT, padx=10)
                self.progress.start(12)
            else:
                self.progress.stop()
                self.progress.pack_forget()
        except tk.TclError:
            pass

    def _run_async(
        self,
        work: Callable[[], Any],
        on_success: Callable[[Any], None],
        status: str,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """Executa ``work`` em thread e entrega o resultado na thread da UI."""
        if self._busy:
            messagebox.showinfo("Aguarde", "Já existe uma operação em andamento.", parent=self)
            return
        self._set_busy(True, status)

        def runner():
            try:
                self._queue.put(("ok", work()))
            except Exception as e:  # noqa: BLE001 - erro é repassado à UI
                self._queue.put(("err", e))

        threading.Thread(target=runner, daemon=True).start()
        self.after(80, self._poll_queue, on_success, on_error)

    def _poll_queue(self, on_success, on_error) -> None:
        try:
            kind, payload = self._queue.get_nowait()
        except queue.Empty:
            self.after(80, self._poll_queue, on_success, on_error)
            return
        self._set_busy(False)
        if kind == "ok":
            on_success(payload)
        elif on_error is not None:
            on_error(payload)
        else:
            self._show_error("Erro", payload)

    def _show_error(self, title: str, err: Exception) -> None:
        if isinstance(err, (ODataClientError, MetadataParseError, OSError, ValueError)):
            msg = str(err)
        else:
            msg = f"{type(err).__name__}: {err}"
            log.error("Erro inesperado: %s", "".join(traceback.format_exception(err)))
        log.error("%s: %s", title, msg)
        self.set_status(f"Erro: {msg.splitlines()[0]}")
        messagebox.showerror(title, msg, parent=self)

    # ------------------------------------------------------------ eventos

    def on_data_source_change(self) -> None:
        log.info("Fonte de dados alterada para: %s", self.data_source_var.get())
        self.update_fields_state()
        self.save_config()

    def browse_metadata_file(self) -> None:
        current = self.cfg.resolve_local_metadata_path(self.app_dir)
        path = filedialog.askopenfilename(
            title="Selecione o metadata.json",
            filetypes=[("Arquivos JSON", "*.json"), ("Todos os arquivos", "*.*")],
            initialdir=os.path.dirname(current) or self.app_dir,
            initialfile=os.path.basename(current),
            parent=self,
        )
        if path:
            log.info("Arquivo de metadata local selecionado: %s", path)
            self.local_metadata_path_var.set(path)
            self.save_config()
            self.local_metadata_path_var.set(self.cfg.local_metadata_path)

    def on_close(self) -> None:
        log.info("Fechando aplicação.")
        self.save_config()
        self.destroy()

    # ----------------------------------------------------- carregamento

    def load_services(self) -> None:
        self.save_config()
        if self.data_source_var.get() == "local":
            self.load_services_local()
        else:
            self.load_services_online()

    def load_services_local(self) -> None:
        path = self.cfg.resolve_local_metadata_path(self.app_dir)
        log.info("Carregando serviços do arquivo local: %s", path)
        if not os.path.exists(path):
            self._show_error(
                "Arquivo não encontrado",
                FileNotFoundError(f"Não foi possível encontrar o arquivo:\n\n{path}"),
            )
            return

        def work():
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            return normalize_services(extract_results(data))

        def done(services):
            if not services:
                messagebox.showinfo(
                    "Nenhum serviço",
                    "Não foi encontrada uma lista de serviços em 'd.results', 'results' ou 'value' no JSON.",
                    parent=self,
                )
            self.last_online_payload = None
            # Persiste o caminho realmente usado (corrige caminhos absolutos obsoletos).
            self.cfg.set_local_metadata_path(path, self.app_dir)
            self.local_metadata_path_var.set(self.cfg.local_metadata_path)
            self._apply_loaded_services(services, f"{len(services)} serviços carregados de {os.path.basename(path)}.")

        self._run_async(work, done, "Lendo arquivo local...")

    def load_services_online(self) -> None:
        base_url = self.base_url_var.get().strip()
        if not base_url:
            messagebox.showerror("Erro", "Informe a URL base do CATALOGSERVICE.", parent=self)
            return
        username, password = self.user_var.get(), self.pass_var.get()
        verify = not self.ignore_ssl_var.get()
        timeout = self.cfg.request_timeout

        def work():
            results, raw = fetch_service_collection(base_url, username, password, verify, timeout)
            return normalize_services(results), raw

        def done(payload):
            services, raw = payload
            if not services:
                messagebox.showinfo(
                    "Nenhum serviço",
                    "Nenhum registro retornado em ServiceCollection.\n"
                    "Verifique se o usuário tem autorização no CATALOGSERVICE.",
                    parent=self,
                )
            self.last_online_payload = raw
            self._apply_loaded_services(services, f"{len(services)} serviços carregados do Gateway.")

        self._run_async(work, done, "Consultando CATALOGSERVICE...")

    def _apply_loaded_services(self, services: List[Service], status: str) -> None:
        self.services = services
        self.current_index = None  # índices antigos não valem para a nova lista
        self.update_fields_state()
        self.set_status(status)
        self.refresh_tree(select_last=True)

    def refresh_tree(self, select_last: bool = False) -> None:
        """Preenche a lista respeitando o filtro. ``iid`` = índice em ``self.services``."""
        query = self.filter_var.get()
        visible = filter_services(self.services, query)
        visible_ids = {id(s) for s in visible}

        self._suppress_select = True
        clear_tree(self.tree)
        for idx, svc in enumerate(self.services):
            if id(svc) not in visible_ids:
                continue
            self.tree.insert(
                "", "end", iid=str(idx),
                values=(
                    svc.get("TechnicalServiceName", ""),
                    svc.get("Version", ""),
                    svc.get("ServiceType", ""),
                    svc.get("ServiceDescription", ""),
                    svc.get("ServicePath", ""),
                    svc.get("UpdatedDateFormatted", ""),
                ),
            )
        self._suppress_select = False

        total = len(self.services)
        if not total:
            self.count_var.set("Nenhum serviço carregado.")
        elif query.strip():
            self.count_var.set(f"{len(visible)} de {total} serviços")
        else:
            self.count_var.set(f"{total} serviços")

        target = None
        if select_last:
            target = find_service_index(self.services, self.cfg.last_selected_id, self.cfg.last_selected_tech_name)
        elif self.current_index is not None:
            target = self.current_index
        if target is not None and not self.tree.exists(str(target)):
            target = None
        if target is None and visible:
            target = next(i for i, s in enumerate(self.services) if s is visible[0])

        if target is None:
            self.clear_details()
            return
        self.select_service(target)

    def apply_filter(self) -> None:
        if self.services:
            self.refresh_tree()

    def select_service(self, idx: int) -> None:
        iid = str(idx)
        if not self.tree.exists(iid):
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        # selection_set dispara <<TreeviewSelect>>, que chama show_details.

    def on_select(self, _event=None) -> None:
        if self._suppress_select:
            return
        sel = self.tree.selection()
        if not sel:
            return
        try:
            idx = int(sel[0])
        except ValueError:
            return
        if 0 <= idx < len(self.services) and idx != self.current_index:
            self.show_details(idx)

    def clear_details(self) -> None:
        self.current_index = None
        for v in self.detail_vars.values():
            v.set("")
        set_text(self.summary_text, "Nenhum serviço selecionado.")
        self.clear_metadata_view()

    def show_details(self, idx: int) -> None:
        svc = self.services[idx]
        self.current_index = idx
        log.info("Exibindo serviço: %s", svc.get("TechnicalServiceName"))

        for key, var in self.detail_vars.items():
            if key != "ServiceUrl":
                var.set(str(svc.get(key) or ""))
        self.detail_vars["ServiceUrl"].set(self.current_service_url())

        self.cfg.last_selected_tech_name = svc.get("TechnicalServiceName") or None
        self.cfg.last_selected_id = svc.get("ID") or None
        self._save_debouncer.trigger()

        # $metadata de outro serviço não vale para este; XML carregado de arquivo é mantido.
        if self.metadata_service_index is not None and self.metadata_service_index != idx:
            self.clear_metadata_view()
        self.update_summary()

    def current_service(self) -> Optional[Service]:
        if self.current_index is None or not (0 <= self.current_index < len(self.services)):
            return None
        return self.services[self.current_index]

    def current_service_url(self) -> str:
        svc = self.current_service()
        return build_service_url(svc, self.base_url_var.get()) if svc else ""

    def update_summary(self) -> None:
        svc = self.current_service()
        if svc is None:
            return
        set_text(self.summary_text, self.build_summary_text(svc, self.current_service_url()))

    def build_summary_text(self, svc: Service, full_url: str) -> str:
        lines = [
            f"Serviço OData: {svc.get('TechnicalServiceName') or '(sem nome técnico)'}",
            f"ID no catálogo: {svc.get('ID') or '-'}",
            f"Descrição: {svc.get('ServiceDescription') or '(sem descrição)'}",
            f"Versão: {svc.get('Version') or '-'}   Tipo: {svc.get('ServiceType') or '-'}   Autor: {svc.get('Author') or '-'}",
            f"Atualizado em: {svc.get('UpdatedDateFormatted') or '-'}",
            "",
            f"URL base para consumo:\n  {full_url or '(não foi possível montar a URL)'}",
            f"$metadata:\n  {metadata_url_for(full_url) or '-'}",
        ]
        model = self.metadata_model if self.metadata_service_index == self.current_index else None
        if model is not None:
            lines += [
                "",
                f"Modelo de dados: {len(model.entity_sets)} EntitySets, "
                f"{len(model.entity_types)} EntityTypes, {len(model.function_imports)} FunctionImports.",
            ]
            if model.entity_sets:
                names = ", ".join(es.name for es in model.entity_sets[:12])
                more = f" (+{len(model.entity_sets) - 12})" if len(model.entity_sets) > 12 else ""
                lines.append(f"EntitySets: {names}{more}")
        else:
            lines += [
                "",
                "Este serviço expõe entidades OData consumidas por apps Fiori/UI5, relatórios "
                "ou integrações HTTP. Use 'Ver $metadata' para inspecionar o modelo de dados.",
            ]
        return "\n".join(lines)

    # ------------------------------------------------------------ ações

    def copy_url(self) -> None:
        url = self.detail_vars["ServiceUrl"].get()
        if not url:
            messagebox.showinfo("Nada para copiar", "Nenhum serviço selecionado.", parent=self)
            return
        copy_to_clipboard(self, url)
        self.set_status("URL copiada para a área de transferência.")

    def copy_metadata_url(self) -> None:
        url = metadata_url_for(self.detail_vars["ServiceUrl"].get())
        if not url:
            messagebox.showinfo("Nada para copiar", "Nenhum serviço selecionado.", parent=self)
            return
        copy_to_clipboard(self, url)
        self.set_status("URL do $metadata copiada.")

    def copy_selected_name(self) -> None:
        svc = self.current_service()
        if svc:
            copy_to_clipboard(self, svc.get("TechnicalServiceName", ""))
            self.set_status("Nome técnico copiado.")

    def open_in_browser(self) -> None:
        url = self.detail_vars["ServiceUrl"].get()
        if not url.startswith(("http://", "https://")):
            messagebox.showinfo("URL inválida", "Não há URL completa para abrir.", parent=self)
            return
        log.info("Abrindo no navegador: %s", url)
        webbrowser.open(url)

    def open_app_folder(self) -> None:
        if hasattr(os, "startfile"):
            os.startfile(self.app_dir)  # Windows
        else:
            webbrowser.open(f"file://{self.app_dir}")

    def show_about(self) -> None:
        messagebox.showinfo(
            "Sobre",
            f"{APP_TITLE}\nVersão {__version__}\n\n"
            f"Configuração: {self.config_path}\n"
            f"Log: {os.path.join(self.app_dir, LOG_FILE_NAME)}",
            parent=self,
        )

    def export_services_csv(self) -> None:
        if not self.services:
            messagebox.showinfo("Sem dados", "Não há serviços carregados para exportar.", parent=self)
            return
        filtered = filter_services(self.services, self.filter_var.get())
        export_all = True
        if len(filtered) != len(self.services):
            export_all = not messagebox.askyesno(
                "Exportar",
                f"Exportar apenas os {len(filtered)} serviços filtrados?\n\n"
                f"('Não' exporta todos os {len(self.services)}.)",
                parent=self,
            )
        rows = self.services if export_all else filtered

        path = filedialog.asksaveasfilename(
            title="Salvar lista de serviços como CSV",
            defaultextension=".csv",
            initialfile="servicos_odata.csv",
            filetypes=[("Arquivos CSV", "*.csv"), ("Todos os arquivos", "*.*")],
            parent=self,
        )
        if not path:
            return
        try:
            count = export_services_csv(rows, path, self.base_url_var.get())
        except OSError as e:
            self._show_error("Erro ao salvar CSV", e)
            return
        self.set_status(f"CSV exportado ({count} linhas): {path}")
        messagebox.showinfo("Exportação concluída", f"{count} serviços exportados para:\n\n{path}", parent=self)

    def save_services_json(self) -> None:
        """Salva o JSON bruto da última consulta online para uso no modo local."""
        if self.last_online_payload is None:
            messagebox.showinfo(
                "Sem dados online",
                "Carregue a lista no modo Online primeiro para poder salvá-la como JSON local.",
                parent=self,
            )
            return
        path = filedialog.asksaveasfilename(
            title="Salvar ServiceCollection como JSON",
            defaultextension=".json",
            initialdir=self.app_dir,
            initialfile="metadata.json",
            filetypes=[("Arquivos JSON", "*.json")],
            parent=self,
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.last_online_payload, f, ensure_ascii=False, indent=2)
        except OSError as e:
            self._show_error("Erro ao salvar JSON", e)
            return
        log.info("ServiceCollection salvo em %s", path)
        if messagebox.askyesno(
            "JSON salvo",
            f"Lista salva em:\n\n{path}\n\nUsar este arquivo como fonte local a partir de agora?",
            parent=self,
        ):
            self.local_metadata_path_var.set(path)
            self.save_config()
            self.local_metadata_path_var.set(self.cfg.local_metadata_path)

    # ---------------------------------------------------------- $metadata

    def clear_metadata_view(self) -> None:
        self.metadata_model = None
        self.metadata_xml = ""
        self.metadata_service_index = None
        set_text(self.metadata_text, "")
        self.metadata_source_var.set("Nenhum $metadata carregado.")
        self.xml_search_count_var.set("")
        for tree in (self.entitysets_tree, self.entitytypes_tree, self.properties_tree, self.functions_tree):
            clear_tree(tree)

    def load_metadata_for_selected_service(self) -> None:
        svc = self.current_service()
        if svc is None:
            messagebox.showinfo(
                "Seleção necessária", "Selecione um serviço na aba 'Serviços OData' primeiro.", parent=self
            )
            return
        meta_url = metadata_url_for(build_service_url(svc, self.base_url_var.get()))
        if not meta_url.startswith(("http://", "https://")):
            messagebox.showerror(
                "URL inválida",
                "Não foi possível montar a URL do $metadata.\n"
                "Informe a URL base do CATALOGSERVICE para compor o endereço.",
                parent=self,
            )
            return

        idx = self.current_index
        username, password = self.user_var.get(), self.pass_var.get()
        verify = not self.ignore_ssl_var.get()
        timeout = self.cfg.request_timeout

        def work():
            xml_text = fetch_metadata(meta_url, username, password, verify, timeout)
            return xml_text, parse_metadata(xml_text)

        def done(payload):
            xml_text, model = payload
            self.display_metadata(xml_text, model, f"Fonte: {meta_url}", service_index=idx)

        self.notebook.select(1)
        self._run_async(work, done, f"Baixando $metadata de {svc.get('TechnicalServiceName')}...")

    def load_metadata_from_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Selecione o arquivo de $metadata (XML/EDMX)",
            filetypes=[("Arquivos XML/EDMX", "*.xml *.edmx"), ("Todos os arquivos", "*.*")],
            parent=self,
        )
        if not path:
            return

        def work():
            with open(path, "r", encoding="utf-8-sig") as f:
                xml_text = f.read()
            return xml_text, parse_metadata(xml_text)

        def done(payload):
            xml_text, model = payload
            self.display_metadata(xml_text, model, f"Fonte: arquivo {path}", service_index=None)

        self.notebook.select(1)
        self._run_async(work, done, "Lendo arquivo de $metadata...")

    def display_metadata(
        self, xml_text: str, model: MetadataModel, source_label: str, service_index: Optional[int]
    ) -> None:
        self.metadata_xml = xml_text
        self.metadata_model = model
        self.metadata_service_index = service_index
        self.metadata_source_var.set(source_label)
        set_text(self.metadata_text, xml_text)
        self.xml_highlight_all()
        self._fill_model_views(model)
        self.update_summary()
        self.set_status(
            f"$metadata carregado: {len(model.entity_sets)} EntitySets, "
            f"{len(model.entity_types)} EntityTypes, {len(model.function_imports)} FunctionImports."
        )

    def _fill_model_views(self, model: MetadataModel) -> None:
        for tree in (self.entitysets_tree, self.entitytypes_tree, self.properties_tree, self.functions_tree):
            clear_tree(tree)

        for es in model.entity_sets:
            self.entitysets_tree.insert("", "end", values=(es.name, es.entity_type, es.crud_flags, es.display_doc))
        if not model.entity_sets:
            self.entitysets_tree.insert("", "end", values=("[Nenhum EntitySet encontrado]", "", "", ""))

        for et in model.entity_types:
            kwargs = {} if self.entitytypes_tree.exists(et.full_name) else {"iid": et.full_name}
            self.entitytypes_tree.insert(
                "", "end", **kwargs,
                values=(et.full_name, ", ".join(et.keys), len(et.properties), et.display_doc),
            )
        if not model.entity_types:
            self.entitytypes_tree.insert("", "end", values=("[Nenhum EntityType encontrado]", "", "", ""))

        for fi in model.function_imports:
            self.functions_tree.insert(
                "", "end", values=(fi.name, fi.http_method, fi.return_type, ", ".join(fi.parameters))
            )

    def on_entityset_select(self, _event=None) -> None:
        sel = self.entitysets_tree.selection()
        if not sel or self.metadata_model is None:
            return
        entity_type = self.entitysets_tree.item(sel[0], "values")[1]
        et = self.metadata_model.entity_type_by_name(entity_type)
        if et is not None and self.entitytypes_tree.exists(et.full_name):
            self.entitytypes_tree.selection_set(et.full_name)
            self.entitytypes_tree.see(et.full_name)

    def on_entitytype_select(self, _event=None) -> None:
        sel = self.entitytypes_tree.selection()
        if not sel or self.metadata_model is None:
            return
        et = self.metadata_model.entity_types_by_name.get(sel[0])
        clear_tree(self.properties_tree)
        if et is None or not et.properties:
            self.properties_tree.insert("", "end", values=("", "[Sem propriedades]", "", "", "", ""))
            return
        for p in et.properties:
            self.properties_tree.insert(
                "", "end",
                values=("🔑" if p.is_key else "", p.name, p.type, p.nullable, p.max_length, p.display_doc),
            )

    def save_metadata_xml(self) -> None:
        if not self.metadata_xml:
            messagebox.showinfo("Sem $metadata", "Carregue um $metadata antes de salvar.", parent=self)
            return
        svc = self.services[self.metadata_service_index] if self.metadata_service_index is not None else None
        name = (svc or {}).get("TechnicalServiceName") or "metadata"
        path = filedialog.asksaveasfilename(
            title="Salvar $metadata como XML",
            defaultextension=".xml",
            initialfile=f"{name}_metadata.xml",
            filetypes=[("Arquivos XML", "*.xml"), ("Arquivos EDMX", "*.edmx"), ("Todos", "*.*")],
            parent=self,
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.metadata_xml)
        except OSError as e:
            self._show_error("Erro ao salvar XML", e)
            return
        log.info("$metadata salvo em %s", path)
        self.set_status(f"$metadata salvo em {path}")

    def format_metadata_xml(self) -> None:
        if not self.metadata_xml:
            return
        self.metadata_xml = pretty_print_xml(self.metadata_xml)
        set_text(self.metadata_text, self.metadata_xml)
        self.xml_highlight_all()
        self.set_status("XML formatado.")

    # ------------------------------------------------------- busca no XML

    def xml_highlight_all(self) -> None:
        text = self.metadata_text
        text.tag_remove("match", "1.0", tk.END)
        text.tag_remove("current", "1.0", tk.END)
        needle = self.xml_search_var.get()
        if not needle or not self.metadata_xml:
            self.xml_search_count_var.set("")
            return
        count = 0
        pos = "1.0"
        while True:
            pos = text.search(needle, pos, stopindex=tk.END, nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(needle)}c"
            text.tag_add("match", pos, end)
            count += 1
            pos = end
        self.xml_search_count_var.set(f"{count} ocorr." if count else "nenhuma")

    def xml_search_next(self, backwards: bool = False) -> None:
        text = self.metadata_text
        needle = self.xml_search_var.get()
        if not needle or not self.metadata_xml:
            return
        start = text.index("insert")
        if backwards:
            pos = text.search(needle, f"{start}-1c", stopindex="1.0", backwards=True, nocase=True)
            if not pos:
                pos = text.search(needle, tk.END, stopindex="1.0", backwards=True, nocase=True)
        else:
            current = text.tag_ranges("current")
            if current and text.compare(current[0], "==", start):
                start = f"{start}+1c"
            pos = text.search(needle, start, stopindex=tk.END, nocase=True)
            if not pos:
                pos = text.search(needle, "1.0", stopindex=tk.END, nocase=True)
        if not pos:
            return
        end = f"{pos}+{len(needle)}c"
        text.tag_remove("current", "1.0", tk.END)
        text.tag_add("current", pos, end)
        text.mark_set("insert", pos)
        text.see(pos)
