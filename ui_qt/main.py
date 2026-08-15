"""
Entry point for the PhotoFlow desktop application.

Run with:  python -m ui_qt.main   (or)   python ui_qt/main.py
"""

from __future__ import annotations

import sys
import traceback
from types import TracebackType
from typing import Optional, Type

from PyQt6.QtWidgets import QApplication, QMessageBox

from utils.config import ConfigError, load_config
from utils.logger import get_logger, setup_logging
from utils.version import COMPANY_NAME, __version__
from ui_qt.theme import apply_dark_theme
from ui_qt.views.main_window import MainWindow

logger = get_logger("ui_qt.main")


def _install_excepthook() -> None:
    """
    Route otherwise-uncaught exceptions to the log and a dialog.

    Without this, an exception escaping a Qt slot prints a traceback to a
    console the user never sees and the window simply vanishes. We log the full
    traceback (so it lands in logs/photoflow.log for support) and show a concise
    dialog instead of crashing silently.
    """
    previous = sys.excepthook

    def _hook(
        exc_type: Type[BaseException],
        exc: BaseException,
        tb: Optional[TracebackType],
    ) -> None:
        # Let Ctrl-C behave normally.
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc, tb)
            return
        logger.critical(
            "Unhandled exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc, tb)),
        )
        try:
            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None,
                    "PhotoFlow - unexpected error",
                    f"{exc_type.__name__}: {exc}\n\n"
                    "The details were saved to the log file (logs/photoflow.log).",
                )
        except Exception:  # noqa: BLE001 - never let the handler itself crash
            pass

    sys.excepthook = _hook


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    try:
        setup_logging(load_config().logging)
    except ConfigError as exc:
        # Logging setup is best-effort; the app can still run with defaults, but
        # the user should know their config was not applied.
        logger.warning("Could not load configuration; using defaults: %s", exc)

    _install_excepthook()

    app = QApplication(argv)
    app.setApplicationName("PhotoFlow")
    app.setOrganizationName(COMPANY_NAME)
    app.setApplicationVersion(__version__)
    apply_dark_theme(app)

    # Opens on the in-window startup chooser (Generate Album / Passport
    # Photos / Make a Collage) rather than a popup dialog; picking a card
    # rebuilds this same window in place (see MainWindow._enter_mode).
    window = MainWindow(mode="chooser")
    window.show()
    logger.info("PhotoFlow %s desktop UI started.", __version__)

    _start_licensing(window)
    return app.exec()


def _start_licensing(parent) -> None:
    """
    Set up authentication, licensing and (opt-in) usage counts, without ever
    blocking startup.

    Everything here is wrapped: a licensing bug must not stop a studio from
    opening the application. Worst case we log it and carry on unlicensed, which
    errs in the customer's favour -- the opposite trade-off would mean a bug on
    our side stops someone delivering an album.
    """
    try:
        from core.auth import AuthManager
        from core.licensing import HttpBackend, LicenseManager
        from core.telemetry import configure

        # AuthManager owns the session (login/refresh/logout); licensing only
        # ever needs a bearer token, so ensure_access_token is handed straight
        # to HttpBackend as its token provider rather than duplicating any
        # authentication logic here. ensure_access_token() refreshes a stale
        # token automatically, and returns None gracefully if nobody is
        # signed in -- HttpBackend handles that without a network call.
        auth_manager = AuthManager()
        backend = HttpBackend(
            base_url=f"{auth_manager.base_url}/licenses",
            token_provider=auth_manager.ensure_access_token,
        )
        manager = LicenseManager(backend=backend)
        status = manager.status()

        # Usage counts are off unless the customer has explicitly agreed.
        # TELEMETRY_ENDPOINT stays None until a collection endpoint exists; the
        # counters are still kept locally so support can ask for them.
        counters = configure(consent=manager.telemetry_consent(), endpoint=None)
        counters.record("app_launched")

        # Re-check an activated licence at most weekly, in the background.
        manager.revalidate()

        # Only interrupt when there's something the user must act on: trial
        # nearly over, trial ended, or a licence that needs re-checking.
        if status.should_nag or manager.telemetry_consent() is None:
            from ui_qt.views.license_dialog import LicenseDialog

            LicenseDialog(manager, telemetry=counters, parent=parent).exec()

        parent.auth_manager = auth_manager      # so a future login/logout UI can reuse it
        parent.license_manager = manager        # so an About/Licence menu can reuse it
    except Exception as exc:  # noqa: BLE001 - licensing must never break startup
        logger.warning("Licensing setup skipped after an error: %s", exc)


if __name__ == "__main__":
    raise SystemExit(main())
