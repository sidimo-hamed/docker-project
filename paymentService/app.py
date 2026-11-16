
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

PAYMENTS_TOTAL = Counter(
    "payments_total",
    "Total number of simulated payments",
    ["status"]
)


# ==========================================
# CORRELATION ID AND REQUEST MONITORING
# ==========================================

@app.before_request
def before_request():

    correlation_id = (
        request.headers.get("X-Correlation-ID")
        or str(uuid.uuid4())
    )

    g.correlation_id = correlation_id

    set_correlation_id(correlation_id)

    g.start_time = time.perf_counter()

    HTTP_REQUESTS_IN_PROGRESS.inc()

    logger.info(
        "HTTP request received",
        extra={
            "event": "http_request_received",
            "method": request.method,
            "path": request.path,
            "remote_addr": request.remote_addr
        }
    )


@app.after_request
def after_request(response):

    duration = time.perf_counter() - g.start_time

    endpoint = request.endpoint or "unknown"
    method = request.method
    status = str(response.status_code)

    HTTP_REQUESTS.labels(
        method=method,
        endpoint=endpoint,
        status=status
    ).inc()

    HTTP_REQUEST_DURATION.labels(
        method=method,
        endpoint=endpoint
    ).observe(duration)

    HTTP_REQUESTS_IN_PROGRESS.dec()

    response.headers["X-Correlation-ID"] = g.correlation_id

    logger.info(
        "HTTP request completed",
        extra={
            "event": "http_request_completed",
            "method": method,
            "path": request.path,
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
        "Payment Service home route accessed",
        extra={
            "event": "service_home_accessed"
        }
    )

    return jsonify({
        "service": "Payment Service",
        "status": "running"
    }), 200


# ==========================================
# CREATE PAYMENT
# ==========================================

@app.route("/payments", methods=["POST"])
def create_payment():

    data = request.get_json(silent=True) or {}

    order_id = data.get("order_id")
    amount = data.get("amount")

    # Check required fields
    if order_id is None or amount is None:

        PAYMENTS_TOTAL.labels(status="failed").inc()

        logger.warning(
            "Payment rejected: missing required fields",
            extra={
                "event": "payment_validation_failed",
                "order_id": order_id
            }
        )

        return jsonify({
            "error": "order_id and amount are required"
        }), 400

    # Validate amount
    try:

        amount = float(amount)

    except (ValueError, TypeError):

        PAYMENTS_TOTAL.labels(status="failed").inc()

        logger.warning(
            "Payment rejected: invalid amount",
            extra={
                "event": "payment_validation_failed",
                "order_id": order_id
            }
        )

        return jsonify({
            "error": "Invalid amount"
        }), 400

    # Check positive amount
    if amount <= 0:

        PAYMENTS_TOTAL.labels(status="failed").inc()

        logger.warning(
            "Payment rejected: amount must be positive",
            extra={
                "event": "payment_validation_failed",
                "order_id": order_id,
                "amount": amount
            }
        )

        return jsonify({
            "error": "Amount must be positive"
        }), 400

    # ======================================
    # PAYMENT SIMULATION
    # No real banking transaction
    # ======================================

    PAYMENTS_TOTAL.labels(status="completed").inc()

    logger.info(
        "Payment simulated successfully",
        extra={
            "event": "payment_completed",
            "order_id": order_id,
            "amount": amount,
            "payment_status": "COMPLETED"
        }
    )

    return jsonify({
        "order_id": order_id,
        "amount": amount,
        "status": "COMPLETED",
        "message": "Payment simulated successfully"
    }), 201


# ==========================================
# HEALTH CHECK
# ==========================================

@app.get("/health")
def health():

    logger.info(
        "Payment Service health check",
        extra={
            "event": "health_check",
            "service_status": "healthy"
        }
    )

    return jsonify({
        "service": "payment-service",
        "status": "healthy"
    }), 200


# ==========================================
# PROMETHEUS METRICS ENDPOINT
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
        port=5003,
        debug=False
    )
