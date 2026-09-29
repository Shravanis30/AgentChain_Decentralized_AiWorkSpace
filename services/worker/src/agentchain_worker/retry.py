import math
from typing import Any

# Errors explicitly classified as TRANSIENT (Safe to automatically retry)
TRANSIENT_ERROR_CODES = {
    "WORKER_LOST",
    "INFRASTRUCTURE_ERROR",
    "CONTAINER_START_FAILURE",
    "DOCKER_DAEMON_ERROR",
    "TEMPORARY_QUEUE_FAILURE",
    "DATABASE_CONNECTION_ERROR",
    "REDIS_CONNECTION_ERROR",
    "HOST_RESOURCE_EXHAUSTED",
    "STALE_LEASE_RECLAIMED",
}

# Errors explicitly classified as PERMANENT (Must NOT be retried)
PERMANENT_ERROR_CODES = {
    "INPUT_VALIDATION_ERROR",
    "OUTPUT_VALIDATION_ERROR",
    "AGENT_LOGIC_ERROR",
    "AGENT_EXECUTION_ERROR",
    "EXECUTION_TIMEOUT",
    "EXECUTION_CANCELLED",
    "AUTHORIZATION_FAILURE",
    "POLICY_VIOLATION",
    "MALFORMED_MANIFEST",
    "UNSUPPORTED_RUNTIME",
    "SCHEMA_MISMATCH",
    "PAYLOAD_LIMIT_EXCEEDED",
}


def is_transient_error(error_code: str | None, exc: Exception | None = None) -> bool:
    """
    Determines whether a failure is transient and eligible for automatic retry.
    Never retries user code exceptions, schema validation errors, timeouts, or policy errors.
    """
    if not error_code:
        return False

    code = error_code.upper()
    if code in TRANSIENT_ERROR_CODES:
        return True

    if code in PERMANENT_ERROR_CODES:
        return False

    # Check exception types if available
    if exc:
        exc_name = type(exc).__name__
        if "Timeout" in exc_name:
            return False
        if "Validation" in exc_name:
            return False
        if "Connection" in exc_name or "OperationalError" in exc_name:
            return True

    return False


def calculate_backoff_delay(
    attempt: int,
    base_seconds: float = 1.0,
    factor: float = 2.0,
    max_seconds: float = 60.0,
) -> float:
    """
    Computes exponential backoff with a maximum delay ceiling:
    delay = min(base * (factor ** attempt), max_seconds)
    """
    delay = base_seconds * (factor ** max(0, attempt))
    return min(delay, max_seconds)
