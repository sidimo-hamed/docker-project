
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

SHIPMENTS_TOTAL = Counter(
    "shipments_total",
    "Total number of shipments",
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
        "HTTP request completed",
        extra={
            "event": "http_request_completed",
            "method": request.method,
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
        "Shipping Service home route accessed",
        extra={
            "event": "service_home_accessed"
        }
    )

    return jsonify({
        "service": "Shipping Service",
        "status": "running"
    }), 200


# ==========================================
# CREATE SHIPMENT
# ==========================================

@app.route("/shipments", methods=["POST"])
def create_shipment():

    data = request.get_json(silent=True) or {}

    order_id = data.get("order_id")
    customer_id = data.get("customer_id")

    # Validate required fields
    if order_id is None or customer_id is None:

        SHIPMENTS_TOTAL.labels(status="failed").inc()

        logger.warning(
            "Shipment creation rejected: missing required fields",
            extra={
                "event": "shipment_validation_failed",
                "order_id": order_id,
                "customer_id": customer_id
            }
        )

        return jsonify({
            "error": "order_id and customer_id are required"
        }), 400

    # Simulate shipment creation
    SHIPMENTS_TOTAL.labels(status="created").inc()

    logger.info(
        "Shipment created successfully",
        extra={
            "event": "shipment_created",
            "order_id": order_id,
            "customer_id": customer_id,
            "shipment_status": "PREPARING"
        }
    )

    return jsonify({
        "order_id": order_id,
        "customer_id": customer_id,
        "status": "PREPARING",
        "message": "Shipment created successfully"
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
        port=5004,
        debug=False
    )
