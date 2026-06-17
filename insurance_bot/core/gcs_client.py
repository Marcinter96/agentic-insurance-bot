import json
import logging
import re
from datetime import datetime
from google.cloud import storage
from insurance_bot.core.config import GCS_BUCKET, SOS_BUCKET, GCP_LOCATION

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


def _digits(value: str) -> str:
    """Phone/number → digits only, so formatting (dashes, spaces) doesn't matter."""
    return re.sub(r"\D", "", value or "")


def _alnum(value: str) -> str:
    """Plate → uppercase alphanumerics only."""
    return re.sub(r"[^A-Za-z0-9]", "", value or "").upper()


def _lookup_normalized(index: dict, raw: str, normalizer) -> str | None:
    """Match `raw` against an index whose keys are formatted strings.

    Tries an exact hit first, then a format-insensitive match (e.g. a phone
    typed as '0457 123 456' matches the stored '0457-123-456')."""
    if not index:
        logger.warning("LOOKUP | index empty/missing for %r", raw)
        return None
    if not raw:
        return None
    if raw in index:
        return index[raw]
    target = normalizer(raw)
    if not target:
        return None
    for key, cid in index.items():
        if normalizer(key) == target:
            return cid
    logger.info("LOOKUP | no match for %r (normalized=%r) among %d index entries",
                raw, target, len(index))
    return None


class GCSClient:
    def __init__(self, bucket_name: str = GCS_BUCKET):
        # Lazy: do NOT create storage.Client() here. Importing this module must
        # never require GCP credentials (ADK imports it at `adk web` startup).
        # The real client/bucket are created on first read/write.
        self._bucket_name = bucket_name
        self._client = None
        self._bucket = None
        self._extra_buckets: dict[str, object] = {}

    @property
    def client(self):
        if self._client is None:
            self._client = storage.Client()
        return self._client

    @property
    def bucket(self):
        if self._bucket is None:
            self._bucket = self.client.bucket(self._bucket_name)
        return self._bucket

    def get_or_create_bucket(self, name: str, location: str = GCP_LOCATION):
        """Return a bucket handle, creating the bucket if it doesn't exist yet."""
        if name in self._extra_buckets:
            return self._extra_buckets[name]
        bucket = self.client.bucket(name)
        if not bucket.exists():
            bucket = self.client.create_bucket(name, location=location)
            logger.info("GCS | created bucket gs://%s in %s", name, location)
        self._extra_buckets[name] = bucket
        return bucket

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
        customer_id = _lookup_normalized(index, phone, _digits)
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
        customer_id = _lookup_normalized(index, plate, _alnum)
        return self.get_customer(customer_id) if customer_id else None

    def get_customer(self, customer_id: str) -> dict | None:
        if not _safe_id(customer_id):
            return None
        return self._read(f"customers/{customer_id}.json")

    def get_policy(self, policy_id: str) -> dict | None:
        if not _safe_id(policy_id):
            return None
        return self._read(f"policies/{policy_id}.json")

    def get_vehicle(self, vehicle_id: str) -> dict | None:
        if not _safe_id(vehicle_id):
            return None
        return self._read(f"vehicle_registrations/{vehicle_id}.json")

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

    def log_sos_interaction(self, record: dict, bucket_name: str = SOS_BUCKET) -> bool:
        """Persist an emergency (SOS) interaction to the dedicated SOS bucket.

        The bucket is created on first use if it doesn't exist."""
        sos_id = record.get("sos_id")
        if not _safe_id(sos_id or ""):
            logger.error("SOS | refusing to write record with unsafe sos_id=%r", sos_id)
            return False
        try:
            bucket = self.get_or_create_bucket(bucket_name)
            bucket.blob(f"{sos_id}.json").upload_from_string(json.dumps(record))
            logger.info("SOS | wrote gs://%s/%s.json", bucket_name, sos_id)
            return True
        except Exception as e:
            logger.error("SOS | write failed for %s in %s: %s", sos_id, bucket_name, e)
            return False

    def log_action(self, action: dict) -> str:
        ts = datetime.now().isoformat()
        action["logged_at"] = ts
        self._write(f"audit_logs/{ts.replace(':', '-')}.json", action)
        return ts


gcs = GCSClient()
