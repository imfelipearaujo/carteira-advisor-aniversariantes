"""Carteira Advisor birthday report — single operational entry point.

Usage:
    python main.py

Exit codes are defined in :class:`src.errors.ExitCode`.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from src.config import PROJECT_ROOT, load_settings
from src.errors import CarteiraAdvisorError, ExitCode
from src.pipeline import run
from src.summary import render_summary

logger = logging.getLogger("carteira_advisor")


def setup_logging(log_dir: Path, moment: datetime) -> Path | None:
    """Log INFO+ to a timestamped file and WARNING+ to stderr.

    Returns:
        The log file path, or ``None`` if the directory is not writable (the
        run continues with console logging only).
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(formatter)
    root.addHandler(console)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"carteira_advisor_{moment:%Y-%m-%d_%H%M%S}.log"
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
    except OSError:
        logger.warning("Não foi possível criar o arquivo de log em %s.", log_dir)
        return None
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    # urllib3 debug lines may contain full URLs; keep third-party noise out.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return log_path


def main() -> int:
    """Run the report and return the process exit code."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    moment = datetime.now()
    try:
        settings = load_settings()
    except CarteiraAdvisorError as exc:
        print(f"ERRO DE CONFIGURAÇÃO: {exc}", file=sys.stderr)
        return int(exc.exit_code)

    log_path = setup_logging(settings.log_dir, moment)
    logger.info("Execução iniciada.")
    try:
        result = run(settings, now=moment)
    except CarteiraAdvisorError as exc:
        logger.error("%s: %s", type(exc).__name__, exc)
        print(f"\nFALHA ({type(exc).__name__}): {exc}", file=sys.stderr)
        print("Nenhum relatório definitivo foi publicado.", file=sys.stderr)
        if log_path:
            print(f"Log: {log_path.relative_to(PROJECT_ROOT).as_posix()}", file=sys.stderr)
        return int(exc.exit_code)
    except Exception:
        logger.exception("Erro inesperado.")
        print("\nERRO INESPERADO. Nenhum relatório definitivo foi publicado.", file=sys.stderr)
        if log_path:
            print(f"Detalhes em: {log_path.relative_to(PROJECT_ROOT).as_posix()}", file=sys.stderr)
        return int(ExitCode.UNEXPECTED_ERROR)

    print(render_summary(result, settings.money_unit, PROJECT_ROOT))
    if log_path:
        print(f"\nLog: {log_path.relative_to(PROJECT_ROOT).as_posix()}")
    logger.info("Execução finalizada com código %d.", int(result.exit_code))
    return int(result.exit_code)


if __name__ == "__main__":
    sys.exit(main())
