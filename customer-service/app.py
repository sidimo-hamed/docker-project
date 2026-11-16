import os
import time
import uuid

from flask import Flask, jsonify, request, g, Response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import SQLAlchemyError, IntegrityError

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
    "postgresql+psycopg://sidimed:p%40ssword@127.0.0.1:5432/customers_db"
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

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"]
)

HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "Number of HTTP requests currently being processed"
)

CUSTOMERS_CREATED = Counter(
    "customers_created_total",
    "Total number of customers created"
)

CUSTOMER_OPERATIONS = Counter(
    "customers_operations_total",
    "Total customer CRUD operations",
    ["operation", "status"]
)


# ==========================================
# CUSTOMER MODEL
# ==========================================

class Customer(db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    phone = db.Column(db.String(30), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "phone": self.phone
        }


# ==========================================
# CORRELATION ID AND REQUEST MONITORING
# ==========================================

@app.before_request
def before_request():
    correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())

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
        "Customer Service health route accessed",
        extra={"event": "service_home_accessed"}
    )

    return jsonify({
        "message": "Customer Service is running"
    }), 200


# ==========================================
# GET ALL CUSTOMERS
# ==========================================

@app.route("/customers", methods=["GET"])
def get_customers():
    try:
        customers = Customer.query.all()

        CUSTOMER_OPERATIONS.labels(
            operation="read_all",
            status="success"
        ).inc()

        logger.info(
            "Customers retrieved",
            extra={
                "event": "customers_retrieved",
                "count": len(customers),
                "operation": "read_all"
            }
        )

        return jsonify([
            customer.to_dict() for customer in customers
        ]), 200

    except SQLAlchemyError:
        db.session.rollback()
        CUSTOMER_OPERATIONS.labels(
            operation="read_all",
            status="failed"
        ).inc()

        logger.exception(
            "Could not retrieve customers",
            extra={
                "event": "customer_database_operation_failed",
                "operation": "read_all"
            }
        )

        return jsonify({"error": "Could not retrieve customers"}), 500


# ==========================================
# GET CUSTOMER BY ID
# ==========================================

@app.route("/customers/<int:customer_id>", methods=["GET"])
def get_customer(customer_id):
    try:
        customer = db.session.get(Customer, customer_id)

        if customer is None:
            CUSTOMER_OPERATIONS.labels(
                operation="read",
                status="failed"
            ).inc()

            logger.warning(
                "Customer not found",
                extra={
                    "event": "customer_not_found",
                    "customer_id": customer_id,
                    "operation": "read"
                }
            )

            return jsonify({
                "error": "Customer not found"
            }), 404

        CUSTOMER_OPERATIONS.labels(
            operation="read",
            status="success"
        ).inc()

        logger.info(
            "Customer retrieved",
            extra={
                "event": "customer_retrieved",
                "customer_id": customer_id,
                "operation": "read"
            }
        )

        return jsonify(customer.to_dict()), 200

    except SQLAlchemyError:
        db.session.rollback()
        CUSTOMER_OPERATIONS.labels(
            operation="read",
            status="failed"
        ).inc()

        logger.exception(
            "Could not retrieve customer",
            extra={
                "event": "customer_database_operation_failed",
                "customer_id": customer_id,
                "operation": "read"
            }
        )

        return jsonify({"error": "Could not retrieve customer"}), 500


# ==========================================
# CREATE CUSTOMER
# ==========================================

@app.route("/customers", methods=["POST"])
def create_customer():
    data = request.get_json(silent=True) or {}

    name = data.get("name")
    email = data.get("email")
    phone = data.get("phone")

    if not name or not email:
        CUSTOMER_OPERATIONS.labels(
            operation="create",
            status="failed"
        ).inc()

        logger.warning(
            "Customer creation rejected: missing required fields",
            extra={
                "event": "customer_validation_failed",
                "operation": "create",
                "missing_fields": [
                    field for field, value in (("name", name), ("email", email))
                    if not value
                ]
            }
        )

        return jsonify({
            "error": "name and email are required"
        }), 400

    customer = Customer(
        name=name,
        email=email,
        phone=phone
    )

    try:
        db.session.add(customer)
        db.session.commit()

        CUSTOMERS_CREATED.inc()

        CUSTOMER_OPERATIONS.labels(
            operation="create",
            status="success"
        ).inc()

        logger.info(
            "Customer created",
            extra={
                "event": "customer_created",
                "customer_id": customer.id,
                "operation": "create"
            }
        )

    except IntegrityError:
        db.session.rollback()

        CUSTOMER_OPERATIONS.labels(
            operation="create",
            status="failed"
        ).inc()

        logger.warning(
            "Customer creation failed because of a database constraint",
            extra={
                "event": "customer_creation_conflict",
                "operation": "create"
            }
        )

        return jsonify({
            "error": "Could not create customer. Check whether the email already exists."
        }), 409

    except SQLAlchemyError:
        db.session.rollback()

        CUSTOMER_OPERATIONS.labels(
            operation="create",
            status="failed"
        ).inc()

        logger.exception(
            "Database error while creating customer",
            extra={
                "event": "customer_database_operation_failed",
                "operation": "create"
            }
        )

        return jsonify({
            "error": "Could not create customer"
        }), 500

    return jsonify(customer.to_dict()), 201


# ==========================================
# UPDATE CUSTOMER
# ==========================================

@app.route("/customers/<int:customer_id>", methods=["PUT"])
def update_customer(customer_id):
    try:
        customer = db.session.get(Customer, customer_id)

        if customer is None:
            CUSTOMER_OPERATIONS.labels(
                operation="update",
                status="failed"
            ).inc()

            logger.warning(
                "Customer update rejected: customer not found",
                extra={
                    "event": "customer_not_found",
                    "customer_id": customer_id,
                    "operation": "update"
                }
            )

            return jsonify({
                "error": "Customer not found"
            }), 404

        data = request.get_json(silent=True) or {}

        if "name" in data:
            if not data["name"]:
                CUSTOMER_OPERATIONS.labels(
                    operation="update",
                    status="failed"
                ).inc()

                logger.warning(
                    "Customer update rejected: empty name",
                    extra={
                        "event": "customer_validation_failed",
                        "customer_id": customer_id,
                        "operation": "update",
                        "field": "name"
                    }
                )

                return jsonify({
                    "error": "name cannot be empty"
                }), 400

            customer.name = data["name"]

        if "email" in data:
            if not data["email"]:
                CUSTOMER_OPERATIONS.labels(
                    operation="update",
                    status="failed"
                ).inc()

                logger.warning(
                    "Customer update rejected: empty email",
                    extra={
                        "event": "customer_validation_failed",
                        "customer_id": customer_id,
                        "operation": "update",
                        "field": "email"
                    }
                )

                return jsonify({
                    "error": "email cannot be empty"
                }), 400

            customer.email = data["email"]

        if "phone" in data:
            customer.phone = data["phone"]

        db.session.commit()

        CUSTOMER_OPERATIONS.labels(
            operation="update",
            status="success"
        ).inc()

        logger.info(
            "Customer updated",
            extra={
                "event": "customer_updated",
                "customer_id": customer_id,
                "operation": "update"
            }
        )

        return jsonify(customer.to_dict()), 200

    except IntegrityError:
        db.session.rollback()

        CUSTOMER_OPERATIONS.labels(
            operation="update",
            status="failed"
        ).inc()

        logger.warning(
            "Customer update failed because of a database constraint",
            extra={
                "event": "customer_update_conflict",
                "customer_id": customer_id,
                "operation": "update"
            }
        )

        return jsonify({
            "error": "Could not update customer. Check whether the email already exists."
        }), 409

    except SQLAlchemyError:
        db.session.rollback()

        CUSTOMER_OPERATIONS.labels(
            operation="update",
            status="failed"
        ).inc()

        logger.exception(
            "Database error while updating customer",
            extra={
                "event": "customer_database_operation_failed",
                "customer_id": customer_id,
                "operation": "update"
            }
        )

        return jsonify({
            "error": "Could not update customer"
        }), 500


# ==========================================
# DELETE CUSTOMER
# ==========================================

@app.route("/customers/<int:customer_id>", methods=["DELETE"])
def delete_customer(customer_id):
    try:
        customer = db.session.get(Customer, customer_id)

        if customer is None:
            CUSTOMER_OPERATIONS.labels(
                operation="delete",
                status="failed"
            ).inc()

            logger.warning(
                "Customer deletion rejected: customer not found",
                extra={
                    "event": "customer_not_found",
                    "customer_id": customer_id,
                    "operation": "delete"
                }
            )

            return jsonify({
                "error": "Customer not found"
            }), 404

        db.session.delete(customer)
        db.session.commit()

        CUSTOMER_OPERATIONS.labels(
            operation="delete",
            status="success"
        ).inc()

        logger.info(
            "Customer deleted",
            extra={
                "event": "customer_deleted",
                "customer_id": customer_id,
                "operation": "delete"
            }
        )

    except SQLAlchemyError:
        db.session.rollback()

        CUSTOMER_OPERATIONS.labels(
            operation="delete",
            status="failed"
        ).inc()

        logger.exception(
            "Database error while deleting customer",
            extra={
                "event": "customer_database_operation_failed",
                "customer_id": customer_id,
                "operation": "delete"
            }
        )

        return jsonify({
            "error": "Could not delete customer"
        }), 500

    return jsonify({
        "message": "Customer deleted successfully"
    }), 200


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
# CREATE DATABASE TABLES
# ==========================================

with app.app_context():
    db.create_all()


# ==========================================
# RUN APPLICATION
# ==========================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5002,
        debug=False
    )
