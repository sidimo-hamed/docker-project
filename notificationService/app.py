
import time
import uuid

from flask import Flask, request, jsonify, g, Response
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST
)

from logger_config import logger, set_correlation_id

app = Flask(__name__)


# ==========================================
# PROMETHEUS METRICS
# ==========================================

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total number of HTTP requests",
    ["method", "endpoint", "status"]
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"]
)

HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "Number of HTTP requests currently being processed"
)

NOTIFICATIONS_TOTAL = Counter(
    "notifications_total",
    "Total number of notifications",
    ["status"]
)


# ==========================================
# CORRELATION ID AND REQUEST MONITORING
# ==========================================

@app.before_request
def before_request():
    correlation_id = request.headers.get("X-Correlation-ID")

    if not correlation_id:
        correlation_id = str(uuid.uuid4())

    g.correlation_id = correlation_id
    set_correlation_id(correlation_id)

    g.start_time = time.perf_counter()

    HTTP_REQUESTS_IN_PROGRESS.inc()

    logger.info(
        "Notification Service received an HTTP request",
        extra={
            "event": "http_request_received",
            "method": request.method,
            "endpoint": request.endpoint or "unknown"
        }
    )


@app.after_request
def after_request(response):
    duration = time.perf_counter() - g.start_time

    endpoint = request.endpoint or "unknown"

    HTTP_REQUESTS.labels(
        method=request.method,
        endpoint=endpoint,
        status=str(response.status_code)
    ).inc()

    HTTP_REQUEST_DURATION.labels(
        method=request.method,
        endpoint=endpoint
    ).observe(duration)

    HTTP_REQUESTS_IN_PROGRESS.dec()

    response.headers["X-Correlation-ID"] = g.correlation_id

    logger.info(
        "Notification Service completed an HTTP request",
        extra={
            "event": "http_request_completed",
            "method": request.method,
            "endpoint": endpoint,
            "status_code": response.status_code,
            "duration_seconds": round(duration, 6)
        }
    )

    return response


# ==========================================
# HOME
# ==========================================

@app.route("/", methods=["GET"])
def home():
    logger.info(
        "Notification Service home endpoint accessed",
        extra={"event": "service_home_accessed"}
    )

    return jsonify({
        "service": "Notification Service",
        "status": "running"
    }), 200


# ==========================================
# SEND NOTIFICATION
# ==========================================

@app.route("/notifications", methods=["POST"])
def send_notification():
    data = request.get_json(silent=True) or {}

    customer_id = data.get("customer_id")
    message = data.get("message")

    if customer_id is None or not message:
        NOTIFICATIONS_TOTAL.labels(status="failed").inc()

        logger.warning(
            "Notification request validation failed",
            extra={
                "event": "notification_validation_failed",
                "customer_id": customer_id,
                "reason": "customer_id and message are required"
            }
        )

        return jsonify({
            "error": "customer_id and message are required"
        }), 400

    # Simulation : affichage dans la console du service.
    print(f"Notification for customer {customer_id}: {message}")

    NOTIFICATIONS_TOTAL.labels(status="sent").inc()

    logger.info(
        "Notification sent successfully",
        extra={
            "event": "notification_sent",
            "customer_id": customer_id,
            "notification_status": "SENT"
        }
    )

    return jsonify({
        "customer_id": customer_id,
        "message": message,
        "status": "SENT"
    }), 201


# ==========================================
# PROMETHEUS METRICS
# ==========================================

@app.route("/metrics", methods=["GET"])
def metrics():
    return Response(
        generate_latest(),
        mimetype=CONTENT_TYPE_LATEST
    )


# ==========================================
# RUN APPLICATION
# ==========================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5005,
        debug=False
    )
