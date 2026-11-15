
from flask import Flask, request, jsonify

app = Flask(__name__)


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "service": "Payment Service",
        "status": "running"
    }), 200


@app.route("/payments", methods=["POST"])
def create_payment():
    data = request.get_json(silent=True) or {}

    order_id = data.get("order_id")
    amount = data.get("amount")

    if order_id is None or amount is None:
        return jsonify({
            "error": "order_id and amount are required"
        }), 400

    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid amount"}), 400

    if amount <= 0:
        return jsonify({"error": "Amount must be positive"}), 400

    # Simulation : aucun vrai paiement bancaire.
    return jsonify({
        "order_id": order_id,
        "amount": amount,
        "status": "COMPLETED",
        "message": "Payment simulated successfully"
    }), 201


@app.get("/health")
def health():
    return jsonify({
        "service": "payment-service",
        "status": "healthy"
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5003, debug=False)
