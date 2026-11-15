
from flask import Flask, request, jsonify

app = Flask(__name__)


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "service": "Notification Service",
        "status": "running"
    }), 200


@app.route("/notifications", methods=["POST"])
def send_notification():
    data = request.get_json(silent=True) or {}

    customer_id = data.get("customer_id")
    message = data.get("message")

    if customer_id is None or not message:
        return jsonify({
            "error": "customer_id and message are required"
        }), 400

    # Simulation : affichage dans la console du service.
    print(f"Notification for customer {customer_id}: {message}")

    return jsonify({
        "customer_id": customer_id,
        "message": message,
        "status": "SENT"
    }), 201


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5005, debug=False)
