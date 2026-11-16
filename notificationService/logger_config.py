import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from contextvars import ContextVar

from pythonjsonlogger import jsonlogger

# Identifiant de la requête en cours
_correlation_id = ContextVar("correlation_id", default="-")

# Nom du microservice
SERVICE_NAME = os.getenv(
    "SERVICE_NAME",
    Path(__file__).resolve().parent.name
)


def set_correlation_id(value):
    _correlation_id.set(value or "-")


class StructuredLogFilter(logging.Filter):

    def filter(self, record):
        record.timestamp = datetime.now(
            timezone.utc
        ).isoformat()

        record.service = SERVICE_NAME
        record.correlation_id = _correlation_id.get()

        if not hasattr(record, "event"):
            record.event = "application_event"

        return True


logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:

    handler = logging.StreamHandler()
    handler.addFilter(StructuredLogFilter())

    formatter = jsonlogger.JsonFormatter(
        "%(timestamp)s %(service)s %(levelname)s "
        "%(event)s %(message)s %(correlation_id)s"
    )

    handler.setFormatter(formatter)
    logger.addHandler(handler)
