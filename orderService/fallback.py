from collections import deque

# Démonstration uniquement : files en mémoire
payment_queue = deque()
notification_queue = deque()

customer_cache = {}


def payment_fallback(order_data):
    payment_queue.append(order_data)

    return {
        "status": "PENDING_PAYMENT",
        "message": "Payment queued for later processing"
    }


def notification_fallback(notification_data):
    notification_queue.append(notification_data)

    return {
        "status": "QUEUED",
        "message": "Notification queued for later processing"
    }


def customer_fallback(customer_id):
    customer = customer_cache.get(customer_id)

    if customer:
        return customer

    raise RuntimeError(
        "Customer service unavailable and no cached customer data"
    )