
import os
import uuid
import time

from flask import Flask, jsonify, request, g, Response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import SQLAlchemyError

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
# DATABASE CONFIGURATION
# ==========================================

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://sidimed:p%40ssword@127.0.0.1:5432/inventory_db"
)

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# ==========================================
# PROMETHEUS METRICS
# ==========================================

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total number of HTTP requests",
    ["method", "endpoint", "status"]
)

HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"]
)

HTTP_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "Number of HTTP requests currently being processed"
)

PRODUCTS_CREATED = Counter(
    "products_created_total",
    "Total number of products created"
)

PRODUCT_OPERATIONS = Counter(
    "products_operations_total",
    "Total product operations",
    ["operation", "status"]
)


# ==========================================
# PRODUCT MODEL
# ==========================================

class Product(db.Model):

    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=0)

    def to_dict(self):

        return {
            "id": self.id,
            "name": self.name,
            "price": self.price,
            "quantity": self.quantity
        }


# ==========================================
# CORRELATION ID AND HTTP MONITORING
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

    HTTP_IN_PROGRESS.inc()

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

    endpoint = request.endpoint or request.path

    HTTP_REQUESTS.labels(
        method=request.method,
        endpoint=endpoint,
        status=str(response.status_code)
    ).inc()

    HTTP_DURATION.labels(
        method=request.method,
        endpoint=endpoint
    ).observe(duration)

    HTTP_IN_PROGRESS.dec()

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
# PROMETHEUS ENDPOINT
# ==========================================

@app.route("/metrics", methods=["GET"])
def metrics():

    return Response(
        generate_latest(),
        mimetype=CONTENT_TYPE_LATEST
    )


# ==========================================
# GET ALL PRODUCTS
# ==========================================

@app.route("/products", methods=["GET"])
def get_products():

    try:

        products = Product.query.all()

        PRODUCT_OPERATIONS.labels(
            operation="read_all",
            status="success"
        ).inc()

        logger.info(
            "Products retrieved",
            extra={
                "event": "products_retrieved",
                "count": len(products),
                "operation": "read_all"
            }
        )

        return jsonify([
            product.to_dict()
            for product in products
        ]), 200

    except SQLAlchemyError:

        db.session.rollback()

        PRODUCT_OPERATIONS.labels(
            operation="read_all",
            status="failed"
        ).inc()

        logger.exception(
            "Could not retrieve products",
            extra={
                "event": "product_database_operation_failed",
                "operation": "read_all"
            }
        )

        return jsonify({
            "error": "Could not retrieve products"
        }), 500


# ==========================================
# GET ONE PRODUCT
# ==========================================

@app.route("/products/<int:product_id>", methods=["GET"])
def get_product(product_id):

    try:

        product = db.session.get(Product, product_id)

        if product is None:

            PRODUCT_OPERATIONS.labels(
                operation="read",
                status="failed"
            ).inc()

            logger.warning(
                "Product not found",
                extra={
                    "event": "product_not_found",
                    "product_id": product_id,
                    "operation": "read"
                }
            )

            return jsonify({
                "error": "Product not found"
            }), 404

        PRODUCT_OPERATIONS.labels(
            operation="read",
            status="success"
        ).inc()

        logger.info(
            "Product retrieved",
            extra={
                "event": "product_retrieved",
                "product_id": product_id,
                "operation": "read"
            }
        )

        return jsonify(product.to_dict()), 200

    except SQLAlchemyError:

        db.session.rollback()

        PRODUCT_OPERATIONS.labels(
            operation="read",
            status="failed"
        ).inc()

        logger.exception(
            "Could not retrieve product",
            extra={
                "event": "product_database_operation_failed",
                "product_id": product_id,
                "operation": "read"
            }
        )

        return jsonify({
            "error": "Could not retrieve product"
        }), 500


# ==========================================
# CREATE PRODUCT
# ==========================================

@app.route("/products", methods=["POST"])
def create_product():

    data = request.get_json(silent=True) or {}

    if (
        not data.get("name")
        or "price" not in data
        or "quantity" not in data
    ):

        PRODUCT_OPERATIONS.labels(
            operation="create",
            status="failed"
        ).inc()

        logger.warning(
            "Product creation rejected: missing required fields",
            extra={
                "event": "product_validation_failed",
                "operation": "create"
            }
        )

        return jsonify({
            "error": "name, price and quantity are required"
        }), 400

    try:

        price = float(data["price"])
        quantity = int(data["quantity"])

        if price < 0 or quantity < 0:
            raise ValueError

    except (ValueError, TypeError):

        PRODUCT_OPERATIONS.labels(
            operation="create",
            status="failed"
        ).inc()

        logger.warning(
            "Product creation rejected: invalid values",
            extra={
                "event": "product_validation_failed",
                "operation": "create"
            }
        )

        return jsonify({
            "error": "price and quantity must be non-negative numbers"
        }), 400

    product = Product(
        name=data["name"],
        price=price,
        quantity=quantity
    )

    try:

        db.session.add(product)
        db.session.commit()

        PRODUCTS_CREATED.inc()

        PRODUCT_OPERATIONS.labels(
            operation="create",
            status="success"
        ).inc()

        logger.info(
            "Product created",
            extra={
                "event": "product_created",
                "product_id": product.id,
                "operation": "create"
            }
        )

        return jsonify(product.to_dict()), 201

    except SQLAlchemyError:

        db.session.rollback()

        PRODUCT_OPERATIONS.labels(
            operation="create",
            status="failed"
        ).inc()

        logger.exception(
            "Could not create product",
            extra={
                "event": "product_database_operation_failed",
                "operation": "create"
            }
        )

        return jsonify({
            "error": "Could not create product"
        }), 500


# ==========================================
# UPDATE PRODUCT
# ==========================================

@app.route("/products/<int:product_id>", methods=["PUT"])
def update_product(product_id):

    try:

        product = db.session.get(Product, product_id)

        if product is None:

            PRODUCT_OPERATIONS.labels(
                operation="update",
                status="failed"
            ).inc()

            logger.warning(
                "Product update rejected: product not found",
                extra={
                    "event": "product_not_found",
                    "product_id": product_id,
                    "operation": "update"
                }
            )

            return jsonify({
                "error": "Product not found"
            }), 404

        data = request.get_json(silent=True) or {}

        if "name" in data:

            if not data["name"]:

                PRODUCT_OPERATIONS.labels(
                    operation="update",
                    status="failed"
                ).inc()

                logger.warning(
                    "Product update rejected: empty name",
                    extra={
                        "event": "product_validation_failed",
                        "product_id": product_id,
                        "operation": "update"
                    }
                )

                return jsonify({
                    "error": "name cannot be empty"
                }), 400

            product.name = data["name"]

        try:

            if "price" in data:
                product.price = float(data["price"])

            if "quantity" in data:
                product.quantity = int(data["quantity"])

            if product.price < 0 or product.quantity < 0:
                raise ValueError

        except (ValueError, TypeError):

            db.session.rollback()

            PRODUCT_OPERATIONS.labels(
                operation="update",
                status="failed"
            ).inc()

            logger.warning(
                "Product update rejected: invalid values",
                extra={
                    "event": "product_validation_failed",
                    "product_id": product_id,
                    "operation": "update"
                }
            )

            return jsonify({
                "error": "price and quantity must be non-negative numbers"
            }), 400

        db.session.commit()

        PRODUCT_OPERATIONS.labels(
            operation="update",
            status="success"
        ).inc()

        logger.info(
            "Product updated",
            extra={
                "event": "product_updated",
                "product_id": product_id,
                "operation": "update"
            }
        )

        return jsonify(product.to_dict()), 200

    except SQLAlchemyError:

        db.session.rollback()

        PRODUCT_OPERATIONS.labels(
            operation="update",
            status="failed"
        ).inc()

        logger.exception(
            "Could not update product",
            extra={
                "event": "product_database_operation_failed",
                "product_id": product_id,
                "operation": "update"
            }
        )

        return jsonify({
            "error": "Could not update product"
        }), 500


# ==========================================
# DELETE PRODUCT
# ==========================================

@app.route("/products/<int:product_id>", methods=["DELETE"])
def delete_product(product_id):

    try:

        product = db.session.get(Product, product_id)

        if product is None:

            PRODUCT_OPERATIONS.labels(
                operation="delete",
                status="failed"
            ).inc()

            logger.warning(
                "Product deletion rejected: product not found",
                extra={
                    "event": "product_not_found",
                    "product_id": product_id,
                    "operation": "delete"
                }
            )

            return jsonify({
                "error": "Product not found"
            }), 404

        db.session.delete(product)
        db.session.commit()

        PRODUCT_OPERATIONS.labels(
            operation="delete",
            status="success"
        ).inc()

        logger.info(
            "Product deleted",
            extra={
                "event": "product_deleted",
                "product_id": product_id,
                "operation": "delete"
            }
        )

        return jsonify({
            "message": "Product deleted successfully"
        }), 200

    except SQLAlchemyError:

        db.session.rollback()

        PRODUCT_OPERATIONS.labels(
            operation="delete",
            status="failed"
        ).inc()

        logger.exception(
            "Could not delete product",
            extra={
                "event": "product_database_operation_failed",
                "product_id": product_id,
                "operation": "delete"
            }
        )

        return jsonify({
            "error": "Could not delete product"
        }), 500


# ==========================================
# HOME
# ==========================================

@app.route("/", methods=["GET"])
def home():

    logger.info(
        "Inventory Service home route accessed",
        extra={
            "event": "service_home_accessed"
        }
    )

    return jsonify({
        "message": "Inventory Service is running"
    }), 200


# ==========================================
# DATABASE INITIALIZATION
# ==========================================

with app.app_context():
    db.create_all()


# ==========================================
# START APPLICATION
# ==========================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5001,
        debug=False
    )
