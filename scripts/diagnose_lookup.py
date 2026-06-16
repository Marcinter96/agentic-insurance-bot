"""
Diagnose a customer lookup against the live GCS data.

Checks whether a customer's stored phone/plate actually appear in the lookup
indexes, then runs the real verify_customer() so you can see exactly where a
match succeeds or fails.

Usage (from the repo root, with GCP creds):
    python -m scripts.diagnose_lookup cust_005
    python -m scripts.diagnose_lookup cust_005 --birthdate 1955-02-17
"""

import argparse
import json

from insurance_bot.core.gcs_client import gcs, _digits, _alnum
from insurance_bot.core import guardrails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("customer_id")
    ap.add_argument("--birthdate", default=None, help="override birthdate to test the cross-check")
    args = ap.parse_args()

    cust = gcs.get_customer(args.customer_id)
    if not cust:
        print(f"❌ customers/{args.customer_id}.json not found in the bucket.")
        return
    phone = cust.get("phone")
    birthdate = args.birthdate or cust.get("birthdate")
    print(f"customer {args.customer_id}: phone={phone!r} birthdate={cust.get('birthdate')!r}")

    # 1) Is the phone in the phone index?
    pidx = gcs._read("indexes/phone_to_customer.json") or {}
    print(f"\nphone index: {len(pidx)} entries")
    print(f"  exact key {phone!r} present? {phone in pidx}"
          + (f" -> {pidx.get(phone)}" if phone in pidx else ""))
    norm = {_digits(k): v for k, v in pidx.items()}
    print(f"  digits-normalized {_digits(phone)!r} present? {_digits(phone) in norm}"
          + (f" -> {norm.get(_digits(phone))}" if _digits(phone) in norm else ""))

    # 2) Run the real search
    print("\n--- verify_customer(phone, birthdate) ---")
    res = guardrails.verify_customer(phone=phone, birthdate=birthdate)
    print(json.dumps({k: res[k] for k in ("customer_id", "verification_level", "failure_reason")}, indent=2))

    if res["verification_level"] == "UNVERIFIED":
        print("\n➡️  The phone is not resolving. Most likely the bucket index is stale or was")
        print("   generated from a different data set than this customer file. Re-seed with:")
        print("   python data/mock_data_generator.py --bucket <your-bucket>")


if __name__ == "__main__":
    main()
