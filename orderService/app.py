import os
import logging
import uuid
import time
import random

from datetime import datetime, timezone
from collections import deque
from threading import Lock

import requests

from flask import (
    Flask, jsonify, request, g, Response, has_request_context
)

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import SQLAlchemyError

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST
)

from logger_config import (
    logger,
    set_correlation_id as set_log_correlation_id
)


app = Flask(__name__)

# ==============================
# PROMETHEUS METRICS
# ==============================

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"]
)

HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["endpoint"]
)

HTTP_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "Requests currently being processed"
)

ORDERS_CREATED = Counter(
    "orders_created_total",
    "Total successfully created orders"
)

ORDERS_FAILED = Counter(
    "orders_failed_total",
    "Total failed order operations"
)
# ==============================
# RESILIENCE METRICS
# ==============================

DOWNSTREAM_RETRIES = Counter(
    "downstream_retries_total",
    "Total retry attempts for downstream services",
    ["service"]
)

DOWNSTREAM_TIMEOUTS = Counter(
    "downstream_timeouts_total",
    "Total downstream request timeouts",
    ["service"]
)

CIRCUIT_BREAKER_STATE = Gauge(
    "circuit_breaker_state",
    "Circuit breaker state: 0=CLOSED, 0.5=HALF-OPEN, 1=OPEN",
    ["service"]
)

CIRCUIT_BREAKER_OPEN = Counter(
    "circuit_breaker_open_total",
    "Total transitions of circuit breakers to OPEN",
    ["service"]
)

CIRCUIT_BREAKER_REJECTED = Counter(
    "circuit_breaker_rejected_total",
    "Total requests rejected by open circuit breakers",
    ["service"]
)

FALLBACK_TOTAL = Counter(
    "fallback_total",
    "Total fallback operations used",
    ["service"]
)

# Initialize resilience metrics with zero values

for service in [
    "customer-service",
    "inventory-service",
    "payment-service",
    "shipping-service",
    "notification-service"
]:
    DOWNSTREAM_RETRIES.labels(service=service).inc(0)
    DOWNSTREAM_TIMEOUTS.labels(service=service).inc(0)

CIRCUIT_BREAKER_OPEN.labels(service="payment-service").inc(0)
CIRCUIT_BREAKER_REJECTED.labels(service="payment-service").inc(0)
FALLBACK_TOTAL.labels(service="customer-service").inc(0)


# ==============================
# CORRELATION ID AND HTTP METRICS
# ==============================

@app.before_request
def observability_before_request():

    g.correlation_id = (
        request.headers.get("X-Correlation-ID")
        or str(uuid.uuid4())
    )

    set_log_correlation_id(g.correlation_id)

    g.request_started_at = time.perf_counter()

    HTTP_IN_PROGRESS.inc()

    logger.info(
        "HTTP request received",
        extra={
            "event": "http_request_received",
            "path": request.path,
            "method": request.method,
            "remote_addr": request.remote_addr
        }
    )


@app.after_request
def observability_after_request(response):

    endpoint = request.endpoint or "unknown"

    duration = time.perf_counter() - g.request_started_at

    HTTP_REQUESTS.labels(
        request.method,
        endpoint,
        str(response.status_code)
    ).inc()

    HTTP_DURATION.labels(endpoint).observe(duration)

    HTTP_IN_PROGRESS.dec()

    response.headers["X-Correlation-ID"] = g.correlation_id

    logger.info(
        "HTTP request completed",
        extra={
            "event": "http_request_completed",
            "path": request.path,
            "method": request.method,
            "status_code": response.status_code,
            "duration_seconds": round(duration, 6)
        }
    )

    return response


# ==============================
# PROMETHEUS ENDPOINT
# ==============================

@app.get("/metrics")
def metrics():

    return Response(
        generate_latest(),
        mimetype=CONTENT_TYPE_LATEST
    )


# ==============================
# CONFIGURATION
# ==============================

TIMEOUT = 5

customer_cache = {}
payment_queue = deque()
notification_queue = deque()


# ==============================
# RETRY WITH EXPONENTIAL BACKOFF
# ==============================

def get_downstream_service(url):

    services = [
        ("customer-service", CUSTOMER_URL),
        ("inventory-service", INVENTORY_URL),
        ("payment-service", PAYMENT_URL),
        ("shipping-service", SHIPPING_URL),
        ("notification-service", NOTIFICATION_URL)
    ]

    for service_name, base_url in services:
        if url.startswith(base_url):
            return service_name

    return "unknown"



# ==============================
# RETRY WITH EXPONENTIAL BACKOFF
# ==============================

def request_with_retry(method, url, **kwargs):

    service_name = get_downstream_service(url)

    headers = dict(kwargs.pop("headers", {}) or {})

    # Propagate the Correlation ID to other services
    if has_request_context():
        headers.setdefault(
            "X-Correlation-ID",
            g.correlation_id
        )

    for attempt in range(4):

        try:

            response = requests.request(
                method,
                url,
                headers=headers,
                **kwargs
            )

            response.raise_for_status()

            return response

        except requests.HTTPError as exc:

            status = exc.response.status_code if exc.response else 0

            if status < 500 or attempt == 3:
                raise

        except requests.Timeout:

            DOWNSTREAM_TIMEOUTS.labels(
                service=service_name
            ).inc()

            if attempt == 3:
                raise

        except requests.ConnectionError:

            if attempt == 3:
                raise

        # Count retry attempt
        DOWNSTREAM_RETRIES.labels(
            service=service_name
        ).inc()

        # Exponential backoff
        delay = 2 ** attempt

        delay = random.uniform(
            delay * 0.7,
            delay * 1.3
        )

        logger.warning(
            "Downstream request retry",
            extra={
                "event": "downstream_request_retry",
                "service": service_name,
                "attempt": attempt + 1,
                "url": url
            }
        )

        time.sleep(delay)


# ==============================
# CIRCUIT BREAKER
# ==============================

class CircuitBreaker:

    def __init__(
        self,
        failure_threshold=0.50,
        minimum_requests=10,
        reset_timeout=30,
        service_name="payment-service"
    ):

        self.failure_threshold = failure_threshold
        self.minimum_requests = minimum_requests
        self.reset_timeout = reset_timeout
        self.service_name = service_name

        self.state = "CLOSED"
        self.results = deque(maxlen=100)

        self.opened_at = None
        self.half_open_test_running = False

        self.lock = Lock()

        # Initial Prometheus state
        CIRCUIT_BREAKER_STATE.labels(
            service=self.service_name
        ).set(0)

    def update_state(self, new_state):

        previous_state = self.state

        self.state = new_state

        state_values = {
            "CLOSED": 0,
            "HALF-OPEN": 0.5,
            "OPEN": 1
        }

        CIRCUIT_BREAKER_STATE.labels(
            service=self.service_name
        ).set(state_values[new_state])

        # Count transitions to OPEN
        if new_state == "OPEN" and previous_state != "OPEN":

            CIRCUIT_BREAKER_OPEN.labels(
                service=self.service_name
            ).inc()

    def call(self, function, *args, **kwargs):

        with self.lock:

            if self.state == "OPEN":

                if time.monotonic() - self.opened_at < self.reset_timeout:

                    CIRCUIT_BREAKER_REJECTED.labels(
                        service=self.service_name
                    ).inc()

                    raise RuntimeError("Circuit Breaker OPEN")

                self.update_state("HALF-OPEN")
                self.half_open_test_running = False

            if self.state == "HALF-OPEN":

                if self.half_open_test_running:

                    CIRCUIT_BREAKER_REJECTED.labels(
                        service=self.service_name
                    ).inc()

                    raise RuntimeError("Circuit Breaker HALF-OPEN")

                self.half_open_test_running = True

        try:

            result = function(*args, **kwargs)

        except Exception:

            with self.lock:

                if self.state == "HALF-OPEN":

                    self.update_state("OPEN")

                    self.opened_at = time.monotonic()
                    self.half_open_test_running = False

                else:

                    self.results.append(False)
                    self.check_failure_rate()

            raise

        with self.lock:

            if self.state == "HALF-OPEN":

                self.update_state("CLOSED")

                self.results.clear()
                self.half_open_test_running = False

            else:

                self.results.append(True)
                self.check_failure_rate()

        return result

    def check_failure_rate(self):

        if len(self.results) < self.minimum_requests:
            return

        failures = sum(
            not result for result in self.results
        )

        failure_rate = failures / len(self.results)

        if failure_rate >= self.failure_threshold:

            self.update_state("OPEN")
            self.opened_at = time.monotonic()

payment_breaker = CircuitBreaker()

#c'est une pour la test

# @app.route("/test/circuit-breaker", methods=["POST"])

# def test_circuit_breaker():
#     # Route de test désactivée par défaut
#     if os.getenv("ENABLE_CB_TEST", "false").lower() != "true":
#         return jsonify({"error": "Test route disabled"}), 404

#     # Vérification du jeton secret
#     expected_token = os.getenv("CB_TEST_TOKEN")
#     provided_token = request.headers.get("X-CB-Test-Token")

#     if not expected_token or provided_token != expected_token:
#         return jsonify({"error": "Unauthorized"}), 401

#     data = request.get_json(silent=True) or {}
#     mode = data.get("mode")

#     if mode not in ["success", "fail"]:
#         return jsonify({
#             "error": "mode must be success or fail"
#         }), 400

#     def simulated_payment():
#         if mode == "fail":
#             raise RuntimeError("Simulated payment failure")
#         return {"status": "success"}

#     try:
#         result = payment_breaker.call(simulated_payment)
#         return jsonify({
#             "result": result,
#             "circuit_breaker_state": payment_breaker.state
#         }), 200

#     except Exception as error:
#         return jsonify({
#             "error": str(error),
#             "circuit_breaker_state": payment_breaker.state
#         }), 503


def get_customer_with_fallback(customer_id):

    try:
        response = request_with_retry(
            "GET",
            f"{CUSTOMER_URL}/customers",
            timeout=3
        )

        customers = response.json()
        customer = find_by_id(customers, customer_id)

        if customer is None:
            return None

        # Save customer data in cache
        customer_cache[customer_id] = customer

        return customer

    except (requests.RequestException, ValueError):

        # Use cached customer if Customer Service is unavailable
        customer = customer_cache.get(customer_id)

        if customer is not None:

            # Count fallback usage
            FALLBACK_TOTAL.labels(
                service="customer-service"
            ).inc()

            logger.warning(
                "Using cached customer data",
                extra={
                    "event": "customer_cache_fallback",
                    "customer_id": customer_id
                }
            )

            return customer

        raise


def process_payment(order_id, amount):

    def payment_request():

        return request_with_retry(
            "POST",
            f"{PAYMENT_URL}/payments",
            json={
                "order_id": order_id,
                "amount": amount
            },
            timeout=5
        )

    return payment_breaker.call(payment_request)


def queue_payment(order_id, amount):

    payment_queue.append({
        "order_id": order_id,
        "amount": amount
    })


def queue_notification(customer_id, order_id, amount):

    notification_queue.append({
        "customer_id": customer_id,
        "order_id": order_id,
        "message": (
            f"Votre commande {order_id} est confirmée. "
            f"Montant : {amount:.2f}"
        )
    })


# Configure rate limiting
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["100 per 15 minutes"],
    storage_uri="memory://"
)


@app.errorhandler(429)
def ratelimit_handler(error):
    return jsonify({
        "error": "Too Many Requests",
        "message": "Rate limit exceeded. Try again later."
    }), 429


# Database configuration
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://sidimed:p%40ssword@127.0.0.1:5432/orders_db"
)

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# Microservices URLs
CUSTOMER_URL = os.getenv(
    "CUSTOMER_URL", "http://127.0.0.1:5002"
)

INVENTORY_URL = os.getenv(
    "INVENTORY_URL", "http://127.0.0.1:5001"
)

PAYMENT_URL = os.getenv(
    "PAYMENT_URL", "http://127.0.0.1:5003"
)

SHIPPING_URL = os.getenv(
    "SHIPPING_URL", "http://127.0.0.1:5004"
)

NOTIFICATION_URL = os.getenv(
    "NOTIFICATION_URL", "http://127.0.0.1:5005"
)

TIMEOUT = 5


# Order database model
class Order(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        nullable=False
    )

    product_id = db.Column(
        db.Integer,
        nullable=False
    )

    quantity = db.Column(
        db.Integer,
        nullable=False
    )

    status = db.Column(
        db.String(30),
        nullable=False,
        default="PENDING"
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "product_id": self.product_id,
            "quantity": self.quantity,
            "status": self.status,
            "created_at": (
                self.created_at.isoformat()
                if self.created_at else None
            )
        }


# Home route
@app.route("/")
def home():
    return "Order Service is running!", 200


# Check service and database status
@app.get("/health")
def health():

    try:
        db.session.execute(db.text("SELECT 1"))

        return jsonify(
            service="order-service",
            database="connected"
        ), 200

    except SQLAlchemyError:

        db.session.rollback()

        return jsonify(
            service="order-service",
            database="disconnected"
        ), 503


# Get all orders
@app.get("/orders")
def get_orders():

    orders = Order.query.order_by(Order.id).all()

    return jsonify([
        order.to_dict()
        for order in orders
    ]), 200


# Get one order by ID
@app.get("/orders/<int:order_id>")
def get_order(order_id):

    order = db.session.get(Order, order_id)

    if order is None:
        return jsonify(error="Order not found"), 404

    return jsonify(order.to_dict()), 200


# Create a new order
@app.post("/orders")
@limiter.limit("10 per minute")
def create_order():

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify(error="A JSON object is required"), 400

    if not all(
        key in data
        for key in ("customer_id", "product_id", "quantity")
    ):
        return jsonify(
            error="customer_id, product_id and quantity are required"
        ), 400

    try:
        customer_id = int(data["customer_id"])
        product_id = int(data["product_id"])
        quantity = int(data["quantity"])

    except (ValueError, TypeError):

        return jsonify(
            error="IDs and quantity must be integers"
        ), 400

    if customer_id <= 0 or product_id <= 0 or quantity <= 0:

        return jsonify(
            error="All IDs and quantity must be positive"
        ), 400

    order = Order(
        customer_id=customer_id,
        product_id=product_id,
        quantity=quantity
    )

    try:
        db.session.add(order)
        db.session.commit()

    except SQLAlchemyError:

        db.session.rollback()
        ORDERS_FAILED.inc()

        logger.exception(
            "Could not save order",
            extra={"event": "order_database_save_failed"}
        )

        return jsonify(error="Could not save order"), 500

    ORDERS_CREATED.inc()

    logger.info(
        "Order created",
        extra={
            "event": "order_created",
            "order_id": order.id,
            "customer_id": order.customer_id,
            "product_id": order.product_id,
            "quantity": order.quantity,
            "status": order.status
        }
    )

    return jsonify(order.to_dict()), 201


# Get JSON data from another service
def get_service_json(url):

    response = requests.get(
        url,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    return response.json()


# Find an item by ID in a JSON list
def find_by_id(items, item_id):

    if not isinstance(items, list):
        raise ValueError("Expected a JSON list")

    try:
        wanted_id = int(item_id)

    except (TypeError, ValueError):
        return None

    for item in items:

        if not isinstance(item, dict):
            continue

        try:
            if int(item.get("id")) == wanted_id:
                return item

        except (TypeError, ValueError):
            continue

    return None


# Restore product stock if an order cannot be saved
def restore_stock(product_id, original_quantity):

    try:
        response = requests.put(
            f"{INVENTORY_URL}/products/{product_id}",
            json={"quantity": original_quantity},
            timeout=TIMEOUT
        )

        response.raise_for_status()

        return True

    except requests.RequestException:

        logger.exception(
            "Stock restoration failed",
            extra={"event": "stock_restoration_failed", "product_id": product_id}
        )

        return False
# Complete checkout process
@app.post("/checkout")
@limiter.limit("10 per minute")
def checkout():

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify(error="A JSON object is required"), 400

    required = ("customer_id", "product_id", "quantity")

    if not all(key in data for key in required):
        return jsonify(
            error="customer_id, product_id and quantity are required"
        ), 400

    try:
        customer_id = int(data["customer_id"])
        product_id = int(data["product_id"])
        quantity = int(data["quantity"])
    except (ValueError, TypeError):
        return jsonify(
            error="IDs and quantity must be integers"
        ), 400

    if customer_id <= 0 or product_id <= 0 or quantity <= 0:
        return jsonify(
            error="IDs and quantity must be positive"
        ), 400

    # Verify customer
    try:
        customer = get_customer_with_fallback(customer_id)

    except (requests.RequestException, ValueError):
        logger.exception(
            "Customer Service unavailable",
            extra={"event": "customer_service_unavailable", "customer_id": customer_id}
        )
        return jsonify(
            error="Customer Service unavailable and no cached customer"
        ), 502

    if customer is None:
        return jsonify(error="Customer not found"), 404

    # Verify product and stock
    try:
        response = request_with_retry(
            "GET",
            f"{INVENTORY_URL}/products",
            timeout=3
        )

        product = find_by_id(response.json(), product_id)

    except (requests.RequestException, ValueError):
        logger.exception(
            "Inventory Service unavailable",
            extra={"event": "inventory_service_unavailable", "product_id": product_id}
        )
        return jsonify(
            error="Inventory Service unavailable"
        ), 502

    if product is None:
        return jsonify(error="Product not found"), 404

    try:
        price = float(product["price"])
        original_quantity = int(product["quantity"])
    except (KeyError, TypeError, ValueError):
        return jsonify(
            error="Invalid product price or quantity"
        ), 502

    if price <= 0:
        return jsonify(error="Product price must be positive"), 400

    if original_quantity < quantity:
        return jsonify(error="Insufficient stock"), 409

    amount = round(price * quantity, 2)
    remaining_stock = original_quantity - quantity

    # Reserve product stock
    try:
        request_with_retry(
            "PUT",
            f"{INVENTORY_URL}/products/{product_id}",
            json={"quantity": remaining_stock},
            timeout=3
        )

    except requests.RequestException:
        logger.exception(
            "Could not reserve stock",
            extra={"event": "stock_reservation_failed", "product_id": product_id}
        )
        return jsonify(
            error="Could not reserve product stock"
        ), 502

    # Create the order
    order = Order(
        customer_id=customer_id,
        product_id=product_id,
        quantity=quantity,
        status="PENDING"
    )

    try:
        db.session.add(order)
        db.session.commit()

    except SQLAlchemyError:
        db.session.rollback()
        ORDERS_FAILED.inc()
        logger.exception(
            "Could not save checkout order",
            extra={"event": "checkout_order_database_save_failed"}
        )

        restored = restore_stock(product_id, original_quantity)

        return jsonify(
            error="Could not save order",
            stock_restored=restored
        ), 500

    ORDERS_CREATED.inc()

    logger.info(
        "Checkout order created",
        extra={
            "event": "checkout_order_created",
            "order_id": order.id,
            "customer_id": customer_id,
            "product_id": product_id,
            "quantity": quantity,
            "amount": amount,
            "status": order.status
        }
    )

    # Process payment with Circuit Breaker
    try:
        response = process_payment(order.id, amount)
        payment = response.json()

    except (requests.RequestException, RuntimeError):

        logger.exception(
            "Payment Service unavailable",
            extra={"event": "payment_service_unavailable", "order_id": order.id}
        )

        order.status = "PENDING_PAYMENT"

        logger.warning(
            "Payment unavailable; payment queued",
            extra={
                "event": "payment_queued",
                "order_id": order.id,
                "amount": amount
            }
        )

        try:
            db.session.commit()

        except SQLAlchemyError:
            db.session.rollback()
            logger.exception(
                "Could not update pending order",
                extra={"event": "pending_order_update_failed", "order_id": order.id}
            )

            return jsonify(
                error="Could not save pending payment status",
                order_id=order.id
            ), 500

        queue_payment(order.id, amount)

        return jsonify(
            message="Order created. Payment queued for later.",
            order=order.to_dict(),
            payment_status="PENDING_PAYMENT"
        ), 202

    except ValueError:

        order.status = "CANCELLED"

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()

        restored = restore_stock(product_id, original_quantity)

        return jsonify(
            error="Invalid payment response",
            order_id=order.id,
            stock_restored=restored
        ), 502

    # Check payment result
    if payment.get("status") != "COMPLETED":

        order.status = "CANCELLED"

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()

        restored = restore_stock(product_id, original_quantity)

        return jsonify(
            error="Payment was not completed",
            order_id=order.id,
            stock_restored=restored
        ), 402

    # Confirm the order
    order.status = "CONFIRMED"

    logger.info(
        "Order confirmed",
        extra={
            "event": "order_confirmed",
            "order_id": order.id,
            "payment_status": payment.get("status")
        }
    )

    try:
        db.session.commit()

    except SQLAlchemyError:
        db.session.rollback()

        return jsonify(
            error="Payment completed but order confirmation failed",
            order_id=order.id,
            payment=payment,
            requires_reconciliation=True
        ), 500

    # Create shipment
    try:
        response = request_with_retry(
            "POST",
            f"{SHIPPING_URL}/shipments",
            json={
                "order_id": order.id,
                "customer_id": customer_id
            },
            timeout=5
        )

        shipping = response.json()

    except (requests.RequestException, ValueError):

        logger.exception(
            "Shipping Service unavailable",
            extra={"event": "shipping_service_unavailable", "order_id": order.id}
        )

        return jsonify(
            message="Order confirmed but shipping requires retry",
            order=order.to_dict(),
            payment=payment,
            requires_reconciliation=True
        ), 202

    # Send notification
    try:
        response = request_with_retry(
            "POST",
            f"{NOTIFICATION_URL}/notifications",
            json={
                "customer_id": customer_id,
                "message": (
                    f"Votre commande {order.id} est confirmée. "
                    f"Montant : {amount:.2f}"
                )
            },
            timeout=10
        )

        notification = response.json()

    except (requests.RequestException, ValueError):

        logger.exception(
            "Notification Service unavailable",
            extra={"event": "notification_service_unavailable", "order_id": order.id}
        )

        queue_notification(
            customer_id,
            order.id,
            amount
        )

        notification = {
            "status": "QUEUED",
            "message": "Notification queued for later"
        }

    return jsonify({
        "message": "Checkout completed successfully",
        "order": order.to_dict(),
        "amount": amount,
        "payment": payment,
        "shipping": shipping,
        "notification": notification
    }), 201


# Update an order
@app.put("/orders/<int:order_id>")
def update_order(order_id):

    order = db.session.get(Order, order_id)

    if order is None:
        return jsonify(error="Order not found"), 404

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify(error="A JSON object is required"), 400

    for field in ("customer_id", "product_id", "quantity"):

        if field in data:

            try:
                value = int(data[field])

            except (ValueError, TypeError):
                return jsonify(
                    error=f"{field} must be an integer"
                ), 400

            if value <= 0:
                return jsonify(
                    error=f"{field} must be positive"
                ), 400

            setattr(order, field, value)

    if "status" in data:

        allowed = {
            "PENDING",
            "CONFIRMED",
            "CANCELLED",
            "SHIPPED",
            "DELIVERED"
        }

        if data["status"] not in allowed:
            return jsonify(error="Invalid status"), 400

        order.status = data["status"]

    try:
        db.session.commit()

    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(error="Could not update order"), 500

    return jsonify(order.to_dict()), 200


# Delete an order
@app.delete("/orders/<int:order_id>")
def delete_order(order_id):

    order = db.session.get(Order, order_id)

    if order is None:
        return jsonify(error="Order not found"), 404

    try:
        db.session.delete(order)
        db.session.commit()

    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(error="Could not delete order"), 500

    return jsonify(
        message="Order deleted",
        id=order_id
    ), 200


# Create database tables
with app.app_context():
    db.create_all()


# Start Order Service
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
