
import os
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://sidimed:p%40ssword@127.0.0.1:5432/inventory_db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


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


@app.route("/products", methods=["GET"])
def get_products():
    products = Product.query.all()
    return jsonify([product.to_dict() for product in products]), 200


@app.route("/products/<int:product_id>", methods=["GET"])
def get_product(product_id):
    product = db.session.get(Product, product_id)

    if product is None:
        return jsonify({"error": "Product not found"}), 404

    return jsonify(product.to_dict()), 200


@app.route("/products", methods=["POST"])
def create_product():
    data = request.get_json(silent=True) or {}

    if not data.get("name") or "price" not in data or "quantity" not in data:
        return jsonify({
            "error": "name, price and quantity are required"
        }), 400

    try:
        price = float(data["price"])
        quantity = int(data["quantity"])

        if price < 0 or quantity < 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({
            "error": "price and quantity must be non-negative numbers"
        }), 400

    product = Product(
        name=data["name"],
        price=price,
        quantity=quantity
    )

    db.session.add(product)
    db.session.commit()

    return jsonify(product.to_dict()), 201


@app.route("/products/<int:product_id>", methods=["PUT"])
def update_product(product_id):
    product = db.session.get(Product, product_id)

    if product is None:
        return jsonify({"error": "Product not found"}), 404

    data = request.get_json(silent=True) or {}

    if "name" in data:
        if not data["name"]:
            return jsonify({"error": "name cannot be empty"}), 400
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
        return jsonify({
            "error": "price and quantity must be non-negative numbers"
        }), 400

    db.session.commit()
    return jsonify(product.to_dict()), 200


@app.route("/products/<int:product_id>", methods=["DELETE"])
def delete_product(product_id):
    product = db.session.get(Product, product_id)

    if product is None:
        return jsonify({"error": "Product not found"}), 404

    db.session.delete(product)
    db.session.commit()

    return jsonify({"message": "Product deleted successfully"}), 200


@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Inventory Service is running"}), 200


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
