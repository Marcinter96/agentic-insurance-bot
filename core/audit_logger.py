import logging
from datetime import datetime, timezone
from core.config import GCP_PROJECT

logger = logging.getLogger("insurance-bot")

try:
    from google.cloud import logging as cloud_logging
    _cloud_client = cloud_logging.Client(project=GCP_PROJECT)
    _cloud_logger = _cloud_client.logger("insurance-bot")
    _cloud_enabled = True
except Exception:
    _cloud_enabled = False
    _cloud_logger = None


def log_action(
    *,
    session_id: str,
    customer_id: str | None,
    action: str,
    intent: str,
    risk_level: str,
    status: str,
    verification_level: str | None = None,
    duration_ms: int | None = None,
    extra: dict | None = None,
) -> str:
    ts = datetime.now(timezone.utc).isoformat()
    entry = {
        "timestamp": ts,
        "session_id": session_id,
        "customer_id": customer_id,
        "action": action,
        "intent": intent,
        "risk_level": risk_level,
        "status": status,
        "verification_level": verification_level,
        "duration_ms": duration_ms,
        **(extra or {}),
    }

    if _cloud_enabled and _cloud_logger:
        try:
            _cloud_logger.log_struct(entry)
        except Exception as e:
            logger.warning(f"Cloud Logging failed: {e}")

    logger.info("AUDIT | %s", entry)
    return ts
