
import logging
import os
import json
import socket
from pathlib import Path
from datetime import datetime, timezone
from contextvars import ContextVar
from logging.handlers import SocketHandler

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


class LogstashTCPHandler(SocketHandler):

    def __init__(self, host, port):
        super().__init__(host, port)

    def makePickle(self, record):
        log_data = {
            "timestamp": record.timestamp,
            "service": record.service,
            "levelname": record.levelname,
            "event": record.event,
            "message": record.getMessage(),
            "correlation_id": record.correlation_id
        }

        return (
            json.dumps(log_data) + "\n"
        ).encode("utf-8")


logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:

    formatter = jsonlogger.JsonFormatter(
        "%(timestamp)s %(service)s %(levelname)s "
        "%(event)s %(message)s %(correlation_id)s"
    )

    # Handler 1 : logs dans la console Docker
    console_handler = logging.StreamHandler()
    console_handler.addFilter(StructuredLogFilter())
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Handler 2 : logs vers Logstash
    logstash_host = os.getenv("LOGSTASH_HOST", "logstash")
    logstash_port = int(os.getenv("LOGSTASH_PORT", "5006"))

    logstash_handler = LogstashTCPHandler(
        logstash_host,
        logstash_port
    )
    logstash_handler.addFilter(StructuredLogFilter())
    logger.addHandler(logstash_handler)
