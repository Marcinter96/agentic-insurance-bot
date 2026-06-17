"""
Create the offer bucket (adk-insurance-offer-mi) and upload the product catalog.

Run where GCP credentials are available:

    python -m scripts.generate_offers            # upload to the bucket
    python -m scripts.generate_offers --local    # also dump JSON to data/offers/

Each product is written to products/<product_id>.json, plus an index at
products/index.json listing all products.
"""

import argparse
import json
import os

from insurance_bot.core.config import OFFER_BUCKET, GCP_LOCATION
from insurance_bot.core.gcs_client import gcs
from insurance_bot.data.offer_catalog import OFFER_CATALOG


def _index() -> dict:
    return {"products": [
        {"product_id": p["product_id"], "name": p["name"], "category": p["category"],
         "base_premium_eur": p["base_premium_eur"], "tagline": p["tagline"]}
        for p in OFFER_CATALOG
    ]}


def upload() -> None:
    print(f"Creating/opening bucket gs://{OFFER_BUCKET} in {GCP_LOCATION} …")
    gcs.get_or_create_bucket(OFFER_BUCKET, location=GCP_LOCATION)
    for p in OFFER_CATALOG:
        ok = gcs.write_to(OFFER_BUCKET, f"products/{p['product_id']}.json", p)
        print(f"  {'✅' if ok else '❌'} products/{p['product_id']}.json ({p['name']})")
    ok = gcs.write_to(OFFER_BUCKET, "products/index.json", _index())
    print(f"  {'✅' if ok else '❌'} products/index.json ({len(OFFER_CATALOG)} products)")
    print(f"\nDone → gs://{OFFER_BUCKET}/products/")


def save_local(out_dir: str = "data/offers") -> None:
    os.makedirs(out_dir, exist_ok=True)
    for p in OFFER_CATALOG:
        with open(f"{out_dir}/{p['product_id']}.json", "w") as f:
            json.dump(p, f, indent=2)
    with open(f"{out_dir}/index.json", "w") as f:
        json.dump(_index(), f, indent=2)
    print(f"Saved {len(OFFER_CATALOG)} products + index → {out_dir}/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="also dump JSON locally for inspection")
    ap.add_argument("--local-only", action="store_true", help="skip GCS, just write locally")
    args = ap.parse_args()

    if not args.local_only:
        upload()
    if args.local or args.local_only:
        save_local()


if __name__ == "__main__":
    main()
