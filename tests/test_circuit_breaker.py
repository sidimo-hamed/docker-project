
import time
from collections import deque
from threading import Lock


class CircuitBreaker:

    def __init__(
        self,
        failure_threshold=0.50,
        minimum_requests=10,
        reset_timeout=3
    ):
        self.failure_threshold = failure_threshold
        self.minimum_requests = minimum_requests
        self.reset_timeout = reset_timeout

        self.state = "CLOSED"
        self.results = deque(maxlen=100)
        self.opened_at = None
        self.half_open_test_running = False
        self.lock = Lock()

    def update_state(self, new_state):
        self.state = new_state
        print(f"Circuit Breaker state: {new_state}")

    def call(self, function):
        with self.lock:

            if self.state == "OPEN":
                if time.monotonic() - self.opened_at < self.reset_timeout:
                    raise RuntimeError("Circuit Breaker OPEN")

                self.update_state("HALF-OPEN")
                self.half_open_test_running = False

            if self.state == "HALF-OPEN":
                if self.half_open_test_running:
                    raise RuntimeError("Circuit Breaker HALF-OPEN")

                self.half_open_test_running = True

        try:
            result = function()

        except Exception:
            with self.lock:
                if self.state == "HALF-OPEN":
                    self.update_state("OPEN")
                    self.opened_at = time.monotonic()
                    self.half_open_test_running = False
                else:
                    self.results.append(False)
                    self.check_failure_rate()
            raise

        with self.lock:
            if self.state == "HALF-OPEN":
                self.update_state("CLOSED")
                self.results.clear()
                self.half_open_test_running = False
            else:
                self.results.append(True)
                self.check_failure_rate()

        return result

    def check_failure_rate(self):
        if len(self.results) < self.minimum_requests:
            return

        failures = sum(not result for result in self.results)
        failure_rate = failures / len(self.results)

        if failure_rate >= self.failure_threshold:
            self.update_state("OPEN")
            self.opened_at = time.monotonic()


def failing_function():
    raise RuntimeError("Simulated failure")


def working_function():
    return "Payment service recovered"


breaker = CircuitBreaker()

print("\nTEST 1: Initial state")
print(breaker.state)

print("\nTEST 2: Ten requests")

for i in range(10):
    try:
        if i < 5:
            breaker.call(failing_function)
        else:
            breaker.call(working_function)

    except RuntimeError:
        print(f"Request {i + 1}: FAILED")
    else:
        print(f"Request {i + 1}: SUCCESS")

print("\nTEST 3: Verify OPEN")
print("Current state:", breaker.state)

print("\nTEST 4: Verify rejection")

try:
    breaker.call(working_function)
except RuntimeError as error:
    print("Request rejected:", error)

print("\nTEST 5: Wait for recovery")
time.sleep(3.5)

print("\nTEST 6: Recovery test")

result = breaker.call(working_function)

print("Result:", result)
print("Final state:", breaker.state)
