import random
import requests

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
)


def is_retryable_error(exception):
    # Erreurs réseau et délais dépassés
    if isinstance(
        exception,
        (requests.ConnectionError, requests.Timeout)
    ):
        return True

    # Réessayer uniquement les réponses HTTP 5xx
    if isinstance(exception, requests.HTTPError):
        status = exception.response.status_code
        return status >= 500

    # Ne pas réessayer les autres erreurs
    return False


def exponential_backoff(retry_state):
    base_delay = wait_exponential(
        multiplier=1,
        min=1,
        max=4
    )(retry_state)

    # Jitter de ±30 %
    return random.uniform(
        base_delay * 0.7,
        base_delay * 1.3
    )


@retry(
    retry=retry_if_exception(is_retryable_error),
    wait=exponential_backoff,
    stop=stop_after_attempt(4),
    reraise=True
)
def call_with_retry(url, method="GET", **kwargs):
    response = requests.request(
        method,
        url,
        **kwargs
    )

    response.raise_for_status()

    return response