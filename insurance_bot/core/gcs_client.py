import json
import logging
import re
from datetime import datetime
from google.cloud import storage
from insurance_bot.core.config import GCS_BUCKET

logger = logging.getLogger(__name__)

# Guardrail: ids are interpolated into GCS blob paths, so they must not contain
# path separators or traversal. Anything else is rejected (fails safe → "not
# found") so a crafted id like "../audit_logs/x" can't escape its prefix.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _safe_id(value: str) -> str | None:
    if value and _SAFE_ID.match(value):
        return value
    logger.warning("Rejected unsafe id for GCS path: %r", value)
    return None


class GCSClient:
    def __init__(self, bucket_name: str = GCS_BUCKET):
        # Lazy: do NOT create storage.Client() here. Importing this module must
        # never require GCP credentials (ADK imports it at `adk web` startup).
        # The real client/bucket are created on first read/write.
        self._bucket_name = bucket_name
        self._client = None
        self._bucket = None

    @property
    def bucket(self):
        if self._bucket is None:
            self._client = storage.Client()
            self._bucket = self._client.bucket(self._bucket_name)
        return self._bucket

    def _read(self, path: str) -> dict | list | None:
        try:
            blob = self.bucket.blob(path)
            if not blob.exists():
                return None
            return json.loads(blob.download_as_string())
        except Exception as e:
            logger.error(f"GCS read error [{path}]: {e}")
            return None

    def _write(self, path: str, data: dict) -> bool:
        try:
            blob = self.bucket.blob(path)
            blob.upload_from_string(json.dumps(data))
            return True
        except Exception as e:
            logger.error(f"GCS write error [{path}]: {e}")
            return False

    def find_customer_by_phone(self, phone: str) -> dict | None:
        index = self._read("indexes/phone_to_customer.json")
        if not index:
            return None
        customer_id = index.get(phone)
        return self.get_customer(customer_id) if customer_id else None

    def find_customer_by_policy(self, policy_number: str) -> dict | None:
        if not _safe_id(policy_number):
            return None
        policy = self._read(f"policies/{policy_number}.json")
        if not policy:
            return None
        return self.get_customer(policy.get("customer_id", ""))

    def find_customer_by_plate(self, plate: str) -> dict | None:
        index = self._read("indexes/plate_to_customer.json")
        if not index:
            return None
        customer_id = index.get(plate)
        return self.get_customer(customer_id) if customer_id else None

    def get_customer(self, customer_id: str) -> dict | None:
        if not _safe_id(customer_id):
            return None
        return self._read(f"customers/{customer_id}.json")

    def get_policy(self, policy_id: str) -> dict | None:
        if not _safe_id(policy_id):
            return None
        return self._read(f"policies/{policy_id}.json")

    def get_invoices(self, customer_id: str) -> list[dict]:
        if not _safe_id(customer_id):
            return []
        data = self._read(f"indexes/customer_invoices/{customer_id}.json")
        return data.get("invoices", []) if data else []

    def get_claims(self, customer_id: str) -> list[dict]:
        if not _safe_id(customer_id):
            return []
        data = self._read(f"indexes/customer_claims/{customer_id}.json")
        return data.get("claims", []) if data else []

    def log_action(self, action: dict) -> str:
        ts = datetime.now().isoformat()
        action["logged_at"] = ts
        self._write(f"audit_logs/{ts.replace(':', '-')}.json", action)
        return ts


gcs = GCSClient()
