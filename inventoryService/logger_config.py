
import logging
import os
import socket
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

# Configuration Logstash
LOGSTASH_HOST = os.getenv("LOGSTASH_HOST", "logstash")
LOGSTASH_PORT = int(os.getenv("LOGSTASH_PORT", "5006"))


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


class LogstashTCPHandler(logging.Handler):

    def emit(self, record):
        try:
            log_message = self.format(record) + "\n"

            with socket.create_connection(
                (LOGSTASH_HOST, LOGSTASH_PORT),
                timeout=0.5
            ) as connection:
                connection.sendall(
                    log_message.encode("utf-8")
                )

        except (OSError, TimeoutError):
            # Une indisponibilité de Logstash
            # ne doit pas arrêter le microservice.
            pass


logger = logging.getLogger(SERVICE_NAME)

logger.setLevel(
    getattr(
        logging,
        os.getenv("LOG_LEVEL", "INFO").upper(),
        logging.INFO
    )
)

logger.propagate = False

if not logger.handlers:

    # Filtrage des événements
    logger.addFilter(StructuredLogFilter())

    # Format JSON commun
    formatter = jsonlogger.JsonFormatter(
        "%(timestamp)s %(service)s %(levelname)s "
        "%(event)s %(message)s %(correlation_id)s"
    )

    # 1. Affichage dans la console Docker
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 2. Transmission vers Logstash
    logstash_handler = LogstashTCPHandler()
    logstash_handler.setFormatter(formatter)
    logger.addHandler(logstash_handler)
