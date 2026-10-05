"""Secrets handling, log redaction, input validation, safe outbound HTTP."""

import logging
import os
import re
import time
from logging.handlers import RotatingFileHandler
from urllib.parse import urlparse

import requests


class CredentialFilter(logging.Filter):
    """Redacts secrets (JWTs, Bearer tokens, hex keys) before they hit the log file."""

    PATTERNS = [
        re.compile(r"eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}"),
        re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{10,}", re.IGNORECASE),
        re.compile(r"\b[a-f0-9]{20,}\b", re.IGNORECASE),
        re.compile(r"\b\d{8,10}:[a-zA-Z0-9_\-]{30,}\b"),  # Telegram bot tokens
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        redacted = msg
        for pattern in self.PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        record.msg = redacted
        record.args = ()
        return True


def validate_config(require_telegram: bool = False) -> None:
    """Hard-fails on startup if required secrets are missing. Never logs values, only names."""
    missing = []
    if require_telegram and not os.getenv("TELEGRAM_BOT_TOKEN"):
        missing.append("TELEGRAM_BOT_TOKEN")
    if missing:
        raise EnvironmentError(
            "Missing required environment variables: " + ", ".join(missing)
        )


def validate_positive_int(value) -> int:
    iv = int(value)
    if iv <= 0:
        raise ValueError("Value must be a strictly positive integer.")
    return iv


def validate_positive_float(value) -> float:
    fv = float(value)
    if fv <= 0:
        raise ValueError("Value must be a strictly positive number.")
    return fv


def validate_https_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.hostname in ("localhost", "127.0.0.1"):
        return True
    return parsed.scheme == "https"


# Hosts this app is allowed to call directly via safe_request(). python-binance and
# python-telegram-bot manage their own HTTP internally (purpose-built SDKs that only
# ever talk to their respective API, by construction) and are a deliberate, narrow
# exception to this allowlist rather than being routed through it.
_ALLOWED_HOSTS_STATIC = {
    "api.coingecko.com",
}


def safe_request(method: str, url: str, allowed_hosts=None, timeout: int = 15, max_retries: int = 2, **kwargs):
    """HTTPS-only, host-allowlisted HTTP wrapper. Retries only on connection/timeout errors, never on 4xx/5xx."""
    if not validate_https_url(url):
        raise ValueError(f"Refused insecure URL (HTTPS required): {url}")

    host = urlparse(url).hostname or ""
    allowed = _ALLOWED_HOSTS_STATIC | set(allowed_hosts or [])
    if host not in allowed:
        raise ValueError(f"Host not in allowlist: {host}")

    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return requests.request(method, url, timeout=timeout, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise last_exc


def setup_logging(log_dir: str = "logs", level: int = logging.INFO) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "app.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.addFilter(CredentialFilter())
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Also log to stdout -- in Docker this is what `docker compose logs` shows for the
    # background jobs (scanner, outcome_updater), which have no other visible output.
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in logger.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.addFilter(CredentialFilter())
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    return logger
