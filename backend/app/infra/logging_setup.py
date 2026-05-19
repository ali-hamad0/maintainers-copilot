"""structlog configuration.

Call configure_logging() once in lifespan before anything else logs.
JSON renderer in prod; ConsoleRenderer (key=value) in dev.
Every log line is expected to carry trace_id and request_id via contextvars
(injected by middleware in later phases).
"""

import logging
import sys
from pathlib import Path

import structlog

from app.infra.redaction import structlog_processor as _redact_processor


def configure_logging(log_level: str, log_format: str, log_dir: str = "logs") -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)

    Path(log_dir).mkdir(exist_ok=True)
    log_file = Path(log_dir) / "api.log"

    shared_processors: list[structlog.types.Processor] = [
        # Redaction MUST be first — no secret can escape via any later processor.
        _redact_processor,
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if log_format == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    # stdout handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    # file handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    # Avoid duplicate handlers on hot reload
    root_logger.handlers.clear()
    root_logger.addHandler(stdout_handler)
    root_logger.addHandler(file_handler)

    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
