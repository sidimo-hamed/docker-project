import time
from collections import deque
from threading import Lock


class CircuitBreaker:
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF-OPEN"

    def __init__(
        self,
        failure_threshold=0.50,
        minimum_requests=10,
        reset_timeout=30
    ):
        self.failure_threshold = failure_threshold
        self.minimum_requests = minimum_requests
        self.reset_timeout = reset_timeout

        self.state = self.CLOSED
        self.results = deque(maxlen=100)
        self.opened_at = None
        self.lock = Lock()
        self.half_open_test_running = False

    def call(self, function, *args, **kwargs):
        with self.lock:
            now = time.monotonic()

            if self.state == self.OPEN:
                if now - self.opened_at >= self.reset_timeout:
                    self.state = self.HALF_OPEN
                    self.half_open_test_running = False
                    print("Circuit Breaker: HALF-OPEN")
                else:
                    raise RuntimeError("Circuit is OPEN")

            if self.state == self.HALF_OPEN:
                if self.half_open_test_running:
                    raise RuntimeError("Half-open test already running")

                self.half_open_test_running = True

        try:
            result = function(*args, **kwargs)

        except Exception:
            with self.lock:
                if self.state == self.HALF_OPEN:
                    self.state = self.OPEN
                    self.opened_at = time.monotonic()
                    self.half_open_test_running = False
                    print("Circuit Breaker: OPEN")
                else:
                    self.results.append(False)
                    self._check_failure_rate()

            raise

        else:
            with self.lock:
                if self.state == self.HALF_OPEN:
                    self.state = self.CLOSED
                    self.results.clear()
                    self.half_open_test_running = False
                    print("Circuit Breaker: CLOSED")
                else:
                    self.results.append(True)
                    self._check_failure_rate()

            return result

    def _check_failure_rate(self):
        if len(self.results) < self.minimum_requests:
            return

        failures = sum(not result for result in self.results)
        failure_rate = failures / len(self.results)

        if failure_rate >= self.failure_threshold:
            self.state = self.OPEN
            self.opened_at = time.monotonic()
            print("Circuit Breaker: OPEN")