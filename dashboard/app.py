
import os
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

SERVICES = {
    "order": {
        "name": "Order Service",
        "url": os.getenv("ORDER_URL", "http://localhost:5000"),
        "health": "/health",
        "port": 5000,
    },
    "inventory": {
        "name": "Inventory Service",
        "url": os.getenv("INVENTORY_URL", "http://localhost:5001"),
        "health": "/products",
        "port": 5001,
    },
    "customer": {
        "name": "Customer Service",
        "url": os.getenv("CUSTOMER_URL", "http://localhost:5002"),
        "health": "/customers",
        "port": 5002,
    },
    "payment": {
        "name": "Payment Service",
        "url": os.getenv("PAYMENT_URL", "http://localhost:5003"),
        "health": "/",
        "port": 5003,
    },
    "shipping": {
        "name": "Shipping Service",
        "url": os.getenv("SHIPPING_URL", "http://localhost:5004"),
        "health": "/",
        "port": 5004,
    },
    "notification": {
        "name": "Notification Service",
        "url": os.getenv("NOTIFICATION_URL", "http://localhost:5005"),
        "health": "/",
        "port": 5005,
    },
}

TIMEOUT = 4


def call_service(service_key, path="", method="GET", **kwargs):
    service = SERVICES[service_key]
    url = service["url"].rstrip("/") + path

    try:
        response = requests.request(
            method=method,
            url=url,
            timeout=TIMEOUT,
            **kwargs,
        )
        try:
            data = response.json()
        except ValueError:
            data = {"message": response.text}

        return jsonify(data), response.status_code

    except requests.RequestException as error:
        return jsonify({
            "error": "Service inaccessible",
            "service": service["name"],
            "details": str(error),
        }), 502


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    results = []

    for key, service in SERVICES.items():
        try:
            response = requests.get(
                service["url"].rstrip("/") + service["health"],
                timeout=TIMEOUT,
            )
            results.append({
                "key": key,
                "name": service["name"],
                "port": service["port"],
                "status": "UP" if response.ok else "ERROR",
                "http_status": response.status_code,
            })
        except requests.RequestException:
            results.append({
                "key": key,
                "name": service["name"],
                "port": service["port"],
                "status": "DOWN",
                "http_status": None,
            })

    return jsonify(results)


@app.route("/api/orders")
def orders():
    return call_service("order", "/orders")


@app.route("/api/orders/<int:order_id>")
def order_detail(order_id):
    return call_service("order", f"/orders/{order_id}")


@app.route("/api/customers")
def customers():
    return call_service("customer", "/customers")


@app.route("/api/products")
def products():
    return call_service("inventory", "/products")


@app.route("/api/checkout", methods=["POST"])
def checkout():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"error": "Envoyez un objet JSON valide."}), 400

    return call_service("order", "/checkout", method="POST", json=data)
import uuid
from flask import request, g

@app.before_request
def set_correlation_id():
    correlation_id = request.headers.get("X-Correlation-ID")

    if not correlation_id:
        correlation_id = str(uuid.uuid4())

    g.correlation_id = correlation_id


@app.after_request
def add_correlation_header(response):
    response.headers["X-Correlation-ID"] = g.correlation_id
    return response

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
