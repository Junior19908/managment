"""Ponto de entrada do Visualizador de Serviços OData (CATALOGSERVICE).

Uso:
    python app.py
"""

import os
import sys
import traceback


def _app_dir() -> str:
    # PyInstaller (--onefile) expõe o executável em sys.executable.
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    app_dir = _app_dir()

    from catalog_viewer.logging_setup import log, setup_logging

    setup_logging(app_dir)
    log.info("==== Aplicação iniciada ====")

    def excepthook(exc_type, exc_value, exc_tb):
        log.critical("EXCEÇÃO FATAL:\n%s", "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook

    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("Tkinter não está disponível nesta instalação do Python.", file=sys.stderr)
        return 1

    from catalog_viewer.ui.app import CatalogApp

    app = CatalogApp(app_dir)
    app.mainloop()
    log.info("Aplicação finalizada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
