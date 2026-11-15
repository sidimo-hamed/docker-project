import requests

from retry_handler import call_with_retry
from circuit_breaker import CircuitBreaker


PAYMENT_URL = "http://payment-service:5000"
CUSTOMER_URL = "http://customer-service:5000"
INVENTORY_URL = "http://inventory-service:5000"
SHIPPING_URL = "http://shipping-service:5000"
NOTIFICATION_URL = "http://notification-service:5000"


payment_breaker = CircuitBreaker(
    failure_threshold=0.50,
    minimum_requests=10,
    reset_timeout=30
)


def get_customer(customer_id):
    return call_with_retry(
        f"{CUSTOMER_URL}/customers/{customer_id}",
        timeout=3
    )


def update_inventory(product_id, quantity):
    return call_with_retry(
        f"{INVENTORY_URL}/products/{product_id}",
        method="PUT",
        json={"quantity": quantity},
        timeout=3
    )


def process_payment(data):
    def payment_request():
        return call_with_retry(
            f"{PAYMENT_URL}/payments",
            method="POST",
            json=data,
            timeout=5
        )

    return payment_breaker.call(payment_request)


def create_shipping(data):
    return call_with_retry(
        f"{SHIPPING_URL}/shipments",
        method="POST",
        json=data,
        timeout=5
    )


def send_notification(data):
    return call_with_retry(
        f"{NOTIFICATION_URL}/notifications",
        method="POST",
        json=data,
        timeout=10
    )