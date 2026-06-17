"""
Create the dedicated SOS bucket (if needed) and write + read back a real record.

Run this where GCP credentials are available (the machine where `adk web` works):

    python -m scripts.sos_bucket_smoketest

It uses SOS_BUCKET / GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION from config/env.
"""

import json

from insurance_bot.core.config import SOS_BUCKET, GCP_PROJECT, GCP_LOCATION
from insurance_bot.core.gcs_client import gcs
from insurance_bot.workflow import build_sos_record


def main():
    print(f"project={GCP_PROJECT} location={GCP_LOCATION} sos_bucket={SOS_BUCKET}")

    sos_id = "sos_smoketest01"
    record = build_sos_record(
        {
            "session_id": "smoketest-sess",
            "first_message": "I had a car accident on the highway",
            "classification": {"intent": "emergency", "sub_intent": "car accident on the highway"},
            "verification": {"customer_id": None, "verification_level": "EMERGENCY_BYPASS",
                             "customer_data": {}},
        },
        sos_id,
    )

    print(f"\nCreating/opening bucket gs://{SOS_BUCKET} …")
    gcs.get_or_create_bucket(SOS_BUCKET)

    print(f"Writing record {sos_id} …")
    ok = gcs.log_sos_interaction(record)
    print("write ok:", ok)

    print("\nReading it back …")
    bucket = gcs.get_or_create_bucket(SOS_BUCKET)
    blob = bucket.blob(f"{sos_id}.json")
    if blob.exists():
        print(json.dumps(json.loads(blob.download_as_string()), indent=2))
        print(f"\n✅ Success — record is at gs://{SOS_BUCKET}/{sos_id}.json")
    else:
        print("❌ Blob not found after write.")


if __name__ == "__main__":
    main()
