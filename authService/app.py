import os
from datetime import datetime, timedelta, timezone

import jwt
from flask import Flask, request, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"

# Comptes de démonstration uniquement.
USERS = {
    "admin": {
        "id": 1,
        "username": "admin",
        "password_hash": generate_password_hash("admincode!"),
        "role": "admin"
    },
    "sidimed": {
        "id": 2,
        "username": "sidimed",
        "password_hash": generate_password_hash("sidimed!"),
        "role": "customer"
    },
    "staff1": {
        "id": 3,
        "username": "staff1",
        "password_hash": generate_password_hash("ChangeMeStaff!"),
        "role": "staff"
    }
}


def create_token(user):
    now = datetime.now(timezone.utc)

    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "iat": now,
        "exp": now + timedelta(minutes=30)
    }

    return jwt.encode(
        payload,
        JWT_SECRET,
        algorithm=JWT_ALGORITHM
    )


@app.route("/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}

    username = data.get("username", "")
    password = data.get("password", "")

    user = USERS.get(username)

    if not user or not check_password_hash(
        user["password_hash"], password
    ):
        return jsonify({
            "error": "Identifiants invalides"
        }), 401

    token = create_token(user)

    return jsonify({
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": 1800,
        "role": user["role"]
    }), 200


@app.route("/auth/verify", methods=["GET"])
def verify():
    header = request.headers.get("Authorization", "")

    if not header.startswith("Bearer "):
        return "", 401

    token = header.split(" ", 1)[1]

    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM]
        )
    except jwt.PyJWTError:
        return "", 401

    path = request.headers.get("X-Original-URI", "")
    method = request.headers.get(
        "X-Original-Method", "GET"
    ).upper()

    role = payload.get("role")
    username = payload.get("username")

    # Exemple de permissions RBAC.
    if role == "admin":
        allowed = True


    elif role == "customer":
        allowed = (
        (
            path.startswith("/api/orders")
            and method in ("GET", "POST")
        )
        or (
            path == "/api/checkout"
            and method == "POST"
        )
    )

    else:
        allowed = False

    if not allowed:
        return "", 403

    response = make_response("", 200)
    response.headers["X-Auth-Request-User"] = username
    response.headers["X-Auth-Request-Role"] = role
    response.headers["X-Auth-Request-User-Id"] = payload["sub"]

    return response

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

# if admin == "admin" :


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5006)