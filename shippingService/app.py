
from flask import Flask, request, jsonify

app = Flask(__name__)


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "service": "Shipping Service",
        "status": "running"
    }), 200


@app.route("/shipments", methods=["POST"])
def create_shipment():
    data = request.get_json(silent=True) or {}

    order_id = data.get("order_id")
    customer_id = data.get("customer_id")

    if order_id is None or customer_id is None:
        return jsonify({
            "error": "order_id and customer_id are required"
        }), 400

    return jsonify({
        "order_id": order_id,
        "customer_id": customer_id,
        "status": "PREPARING",
        "message": "Shipment created successfully"
    }), 201


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5004, debug=False)
