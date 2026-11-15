
import os
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://sidimed:p%40ssword@127.0.0.1:5432/customers_db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


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


@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Customer Service is running"}), 200


@app.route("/customers", methods=["GET"])
def get_customers():
    customers = Customer.query.all()
    return jsonify([customer.to_dict() for customer in customers]), 200


@app.route("/customers/<int:customer_id>", methods=["GET"])
def get_customer(customer_id):
    customer = db.session.get(Customer, customer_id)

    if customer is None:
        return jsonify({"error": "Customer not found"}), 404

    return jsonify(customer.to_dict()), 200


@app.route("/customers", methods=["POST"])
def create_customer():
    data = request.get_json(silent=True) or {}

    name = data.get("name")
    email = data.get("email")
    phone = data.get("phone")

    if not name or not email:
        return jsonify({"error": "name and email are required"}), 400

    customer = Customer(name=name, email=email, phone=phone)

    try:
        db.session.add(customer)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({
            "error": "Could not create customer. Check whether the email already exists."
        }), 409

    return jsonify(customer.to_dict()), 201


@app.route("/customers/<int:customer_id>", methods=["PUT"])
def update_customer(customer_id):
    customer = db.session.get(Customer, customer_id)

    if customer is None:
        return jsonify({"error": "Customer not found"}), 404

    data = request.get_json(silent=True) or {}

    if "name" in data:
        if not data["name"]:
            return jsonify({"error": "name cannot be empty"}), 400
        customer.name = data["name"]

    if "email" in data:
        if not data["email"]:
            return jsonify({"error": "email cannot be empty"}), 400
        customer.email = data["email"]

    if "phone" in data:
        customer.phone = data["phone"]

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({
            "error": "Could not update customer. Check whether the email already exists."
        }), 409

    return jsonify(customer.to_dict()), 200


@app.route("/customers/<int:customer_id>", methods=["DELETE"])
def delete_customer(customer_id):
    customer = db.session.get(Customer, customer_id)

    if customer is None:
        return jsonify({"error": "Customer not found"}), 404

    db.session.delete(customer)
    db.session.commit()

    return jsonify({"message": "Customer deleted successfully"}), 200


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=False)
