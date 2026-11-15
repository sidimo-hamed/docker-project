
import os
import logging
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import SQLAlchemyError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import time
import random
from collections import deque
from threading import Lock


app = Flask(__name__)

TIMEOUT = 5

customer_cache = {}
payment_queue = deque()
notification_queue = deque()


def request_with_retry(method, url, **kwargs):
    for attempt in range(4):
        try:
            response = requests.request(
                method,
                url,
                **kwargs
            )

            response.raise_for_status()
            return response

        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response else 0

            if status < 500 or attempt == 3:
                raise

        except (requests.ConnectionError, requests.Timeout):
            if attempt == 3:
                raise

        delay = 2 ** attempt
        delay = random.uniform(delay * 0.7, delay * 1.3)
        time.sleep(delay)


class CircuitBreaker:

    def __init__(
        self,
        failure_threshold=0.50,
        minimum_requests=10,
        reset_timeout=30
    ):
        self.failure_threshold = failure_threshold
        self.minimum_requests = minimum_requests
        self.reset_timeout = reset_timeout
        self.state = "CLOSED"
        self.results = deque(maxlen=100)
        self.opened_at = None
        self.half_open_test_running = False
        self.lock = Lock()

    def call(self, function, *args, **kwargs):

        with self.lock:

            if self.state == "OPEN":

                if time.monotonic() - self.opened_at < self.reset_timeout:
                    raise RuntimeError("Circuit Breaker OPEN")

                self.state = "HALF-OPEN"
                self.half_open_test_running = False

            if self.state == "HALF-OPEN":

                if self.half_open_test_running:
                    raise RuntimeError("Circuit Breaker HALF-OPEN")

                self.half_open_test_running = True

        try:
            result = function(*args, **kwargs)

        except Exception:

            with self.lock:

                if self.state == "HALF-OPEN":
                    self.state = "OPEN"
                    self.opened_at = time.monotonic()
                    self.half_open_test_running = False

                else:
                    self.results.append(False)
                    self.check_failure_rate()

            raise

        with self.lock:

            if self.state == "HALF-OPEN":
                self.state = "CLOSED"
                self.results.clear()
                self.half_open_test_running = False

            else:
                self.results.append(True)
                self.check_failure_rate()

        return result

    def check_failure_rate(self):

        if len(self.results) < self.minimum_requests:
            return

        failures = sum(not result for result in self.results)
        failure_rate = failures / len(self.results)

        if failure_rate >= self.failure_threshold:
            self.state = "OPEN"
            self.opened_at = time.monotonic()


payment_breaker = CircuitBreaker()


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

        customer_cache[customer_id] = customer

        return customer

    except (requests.RequestException, ValueError):

        customer = customer_cache.get(customer_id)

        if customer is not None:
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


app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://sidimed:p%40ssword@127.0.0.1:5432/orders_db"
)

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
logging.basicConfig(level=logging.INFO)

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


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, nullable=False)
    product_id = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(
        db.String(30), nullable=False, default="PENDING"
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


@app.route("/")
def home():
    return "Order Service is running!", 200


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


@app.get("/orders")
def get_orders():
    orders = Order.query.order_by(Order.id).all()
    return jsonify([order.to_dict() for order in orders]), 200


@app.get("/orders/<int:order_id>")
def get_order(order_id):
    order = db.session.get(Order, order_id)

    if order is None:
        return jsonify(error="Order not found"), 404

    return jsonify(order.to_dict()), 200


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
        app.logger.exception("Could not save order")
        return jsonify(error="Could not save order"), 500

    return jsonify(order.to_dict()), 201


def get_service_json(url):
    response = requests.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


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
        app.logger.exception("Stock restoration failed")
        return False



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

    try:
        customer = get_customer_with_fallback(customer_id)

    except (requests.RequestException, ValueError):
        app.logger.exception("Customer Service unavailable")
        return jsonify(
            error="Customer Service unavailable and no cached customer"
        ), 502

    if customer is None:
        return jsonify(error="Customer not found"), 404

    try:
        response = request_with_retry(
            "GET",
            f"{INVENTORY_URL}/products",
            timeout=3
        )

        product = find_by_id(response.json(), product_id)

    except (requests.RequestException, ValueError):
        app.logger.exception("Inventory Service unavailable")
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

    try:
        request_with_retry(
            "PUT",
            f"{INVENTORY_URL}/products/{product_id}",
            json={"quantity": remaining_stock},
            timeout=3
        )

    except requests.RequestException:
        app.logger.exception("Could not reserve stock")
        return jsonify(
            error="Could not reserve product stock"
        ), 502

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
        restored = restore_stock(product_id, original_quantity)

        return jsonify(
            error="Could not save order",
            stock_restored=restored
        ), 500

    try:
        response = process_payment(order.id, amount)
        payment = response.json()

    except (requests.RequestException, RuntimeError):

        app.logger.exception("Payment Service unavailable")

        order.status = "PENDING_PAYMENT"

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception("Could not update pending order")

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

    order.status = "CONFIRMED"

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

        app.logger.exception("Shipping Service unavailable")

        return jsonify(
            message="Order confirmed but shipping requires retry",
            order=order.to_dict(),
            payment=payment,
            requires_reconciliation=True
        ), 202

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

        app.logger.exception("Notification Service unavailable")

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
            "PENDING", "CONFIRMED", "CANCELLED",
            "SHIPPED", "DELIVERED"
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


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
