"""
Generates mock data and uploads to GCS bucket (primary) or saves locally (dev fallback).

Usage:
  # Upload directly to GCS (recommended):
  python data/mock_data_generator.py --bucket adk-insurance-demo-data-mi

  # Save locally for inspection:
  python data/mock_data_generator.py
"""

import json
import random
from datetime import date, timedelta
from collections import defaultdict

FIRST_NAMES = ["Alice", "Bob", "Carol", "David", "Emma", "Frank", "Grace", "Henri",
               "Irene", "Jacques", "Kate", "Luc", "Marie", "Nicolas", "Olivia",
               "Pierre", "Quentin", "Rachel", "Sophie", "Thomas", "Ursula", "Victor"]
LAST_NAMES = ["Dupont", "Martin", "Bernard", "Thomas", "Laurent", "Simon", "Michel",
              "Leroy", "Moreau", "Dubois", "Garcia", "Roux", "Vincent", "Fontaine",
              "Girard", "Bonnet", "Mercier", "Petit", "Durand", "Lambert", "Morel"]
CITIES = ["Brussels", "Antwerp", "Ghent", "Liège", "Bruges", "Namur", "Leuven",
          "Charleroi", "Mons", "Aalst", "Mechelen", "La Louvière", "Kortrijk"]
STREETS = ["Rue de la Loi", "Avenue Louise", "Chaussée de Mons", "Rue Neuve",
           "Boulevard du Roi Albert", "Rue Belliard", "Place Flagey",
           "Chaussée de Wavre", "Avenue de Tervueren", "Rue Royale"]
COVERAGE_TYPES = [
    "Comprehensive Auto Insurance",
    "Third-Party Liability Auto",
    "Home Insurance Standard",
    "Home Insurance Premium",
    "Travel Insurance Worldwide",
]
CLAIM_DESCRIPTIONS = [
    "Rear-end collision on highway E40",
    "Storm damage to roof",
    "Vehicle theft from parking lot",
    "Water damage in kitchen due to pipe burst",
    "Collision in underground parking",
    "Windshield crack from road debris",
    "Fire damage to garage",
    "Bicycle collision with parked car",
    "Hail damage to vehicle",
    "Flooding damage to basement",
]
PLATE_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def random_date(start_year: int, end_year: int) -> str:
    start = date(start_year, 1, 1)
    end = date(end_year, 12, 31)
    delta = (end - start).days
    return (start + timedelta(days=random.randint(0, delta))).isoformat()


def random_plate() -> str:
    digits = str(random.randint(100, 999))
    letters = "".join(random.choices(PLATE_LETTERS, k=3))
    return f"{digits}-{letters}"


def generate_customers(count: int = 100) -> list[dict]:
    customers = []
    for i in range(count):
        cid = f"cust_{i + 1:03d}"
        customers.append({
            "id": cid,
            "name": f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}",
            "phone": f"04{random.randint(10, 99)}-{random.randint(100, 999)}-{random.randint(100, 999)}",
            "birthdate": random_date(1955, 2000),
            "address": {
                "street": f"{random.randint(1, 150)} {random.choice(STREETS)}",
                "city": random.choice(CITIES),
                "zip": str(random.randint(1000, 9999)),
                "country": "Belgium",
            },
            "verification_level": random.choices(
                ["VERIFIED_RETURNING", "VERIFIED_NEW", "ESCALATED"],
                weights=[70, 25, 5],
            )[0],
            "policy_ids": [],
            "vehicle_ids": [],
            "account_status": random.choices(["ACTIVE", "SUSPENDED"], weights=[95, 5])[0],
            "created_at": random_date(2018, 2024),
        })
    return customers


def generate_policies(customers: list[dict], per_customer_max: int = 3) -> list[dict]:
    policies = []
    counter = 1
    for customer in customers:
        count = random.randint(1, per_customer_max)
        for _ in range(count):
            pid = f"pol_{counter:04d}"
            start = date(2023, 1, 1) + timedelta(days=random.randint(0, 730))
            expiry = start + timedelta(days=365)
            policies.append({
                "policy_id": pid,
                "customer_id": customer["id"],
                "type": random.choice(COVERAGE_TYPES),
                "coverage": random.choice(COVERAGE_TYPES),
                "start_date": start.isoformat(),
                "expiry": expiry.isoformat(),
                "premium": round(random.uniform(250, 1500), 2),
                "status": random.choices(
                    ["ACTIVE", "EXPIRED", "CANCELLED"], weights=[80, 15, 5]
                )[0],
            })
            customer["policy_ids"].append(pid)
            counter += 1
    return policies


def generate_vehicles(customers: list[dict]) -> list[dict]:
    vehicles = []
    counter = 1
    sample = random.sample(customers, k=len(customers) // 2)
    for customer in sample:
        vid = f"vreg_{counter:04d}"
        vehicles.append({
            "vehicle_id": vid,
            "customer_id": customer["id"],
            "make": random.choice(["Toyota", "Volkswagen", "BMW", "Renault", "Audi", "Ford", "Peugeot"]),
            "model": random.choice(["Corolla", "Golf", "3 Series", "Clio", "A4", "Focus", "308"]),
            "year": random.randint(2010, 2024),
            "license_plate": random_plate(),
            "value": round(random.uniform(5000, 60000), 2),
        })
        customer["vehicle_ids"].append(vid)
        counter += 1
    return vehicles


def generate_invoices(policies: list[dict]) -> list[dict]:
    invoices = []
    counter = 1
    for policy in policies:
        for q in range(random.randint(1, 4)):
            iid = f"inv_{counter:05d}"
            due = date(2025, 1 + q * 3, 1)
            invoices.append({
                "invoice_id": iid,
                "policy_id": policy["policy_id"],
                "customer_id": policy["customer_id"],
                "amount": round(policy["premium"] / 4, 2),
                "due_date": due.isoformat(),
                "status": random.choices(["PAID", "DUE", "OVERDUE"], weights=[70, 20, 10])[0],
            })
            counter += 1
    return invoices


def generate_claims(policies: list[dict]) -> list[dict]:
    claims = []
    counter = 1
    sample = random.sample(policies, k=max(1, len(policies) // 5))
    for policy in sample:
        cid = f"clm_{counter:04d}"
        claims.append({
            "claim_id": cid,
            "policy_id": policy["policy_id"],
            "customer_id": policy["customer_id"],
            "status": random.choice(["SUBMITTED", "IN_REVIEW", "APPROVED", "REJECTED", "CLOSED"]),
            "date_filed": random_date(2024, 2026),
            "description": random.choice(CLAIM_DESCRIPTIONS),
            "amount": round(random.uniform(500, 15000), 2),
        })
        counter += 1
    return claims


def build_indexes(
    customers: list[dict],
    vehicles: list[dict],
    invoices: list[dict],
    claims: list[dict],
) -> dict[str, dict]:
    """Build lookup indexes for fast GCS queries."""
    phone_idx = {c["phone"]: c["id"] for c in customers}
    plate_idx = {v["license_plate"]: v["customer_id"] for v in vehicles}

    cust_invoices: dict[str, list] = defaultdict(list)
    for inv in invoices:
        cust_invoices[inv["customer_id"]].append(inv)

    cust_claims: dict[str, list] = defaultdict(list)
    for clm in claims:
        cust_claims[clm["customer_id"]].append(clm)

    return {
        "phone_to_customer": phone_idx,
        "plate_to_customer": plate_idx,
        "customer_invoices": dict(cust_invoices),
        "customer_claims": dict(cust_claims),
    }


def upload_to_gcs(
    customers, policies, vehicles, invoices, claims, indexes, bucket_name
) -> None:
    from google.cloud import storage
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    def upload_list(data, prefix, id_field):
        for item in data:
            bucket.blob(f"{prefix}/{item[id_field]}.json").upload_from_string(json.dumps(item))
        print(f"  Uploaded {len(data):4d} objects → gs://{bucket_name}/{prefix}/")

    def upload_blob(path, data):
        bucket.blob(path).upload_from_string(json.dumps(data))

    upload_list(customers, "customers", "id")
    upload_list(policies, "policies", "policy_id")
    upload_list(vehicles, "vehicle_registrations", "vehicle_id")
    upload_list(invoices, "invoices", "invoice_id")
    upload_list(claims, "claims", "claim_id")
    upload_blob("indexes/phone_to_customer.json", indexes["phone_to_customer"])
    upload_blob("indexes/plate_to_customer.json", indexes["plate_to_customer"])
    for cust_id, inv_list in indexes["customer_invoices"].items():
        upload_blob(f"indexes/customer_invoices/{cust_id}.json", {"invoices": inv_list})
    for cust_id, clm_list in indexes["customer_claims"].items():
        upload_blob(f"indexes/customer_claims/{cust_id}.json", {"claims": clm_list})
    print(f"  Uploaded indexes (phone, plate, {len(indexes['customer_invoices'])} invoice sets, "
          f"{len(indexes['customer_claims'])} claim sets)")


def save_locally(
    customers, policies, vehicles, invoices, claims, indexes, output_dir="data/mock"
) -> None:
    import os

    def save_list(data, folder, id_field):
        os.makedirs(f"{output_dir}/{folder}", exist_ok=True)
        for item in data:
            with open(f"{output_dir}/{folder}/{item[id_field]}.json", "w") as f:
                json.dump(item, f, indent=2)
        print(f"  Saved {len(data):4d} records → {output_dir}/{folder}/")

    def save_blob(path, data):
        full_path = f"{output_dir}/{path}"
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            json.dump(data, f, indent=2)

    save_list(customers, "customers", "id")
    save_list(policies, "policies", "policy_id")
    save_list(vehicles, "vehicle_registrations", "vehicle_id")
    save_list(invoices, "invoices", "invoice_id")
    save_list(claims, "claims", "claim_id")
    save_blob("indexes/phone_to_customer.json", indexes["phone_to_customer"])
    save_blob("indexes/plate_to_customer.json", indexes["plate_to_customer"])
    for cust_id, inv_list in indexes["customer_invoices"].items():
        save_blob(f"indexes/customer_invoices/{cust_id}.json", {"invoices": inv_list})
    for cust_id, clm_list in indexes["customer_claims"].items():
        save_blob(f"indexes/customer_claims/{cust_id}.json", {"claims": clm_list})
    print(f"  Saved indexes")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate and upload insurance mock data")
    parser.add_argument("--num-customers", type=int, default=100)
    parser.add_argument("--bucket", type=str, default=None,
                        help="GCS bucket name (e.g. adk-insurance-demo-data-mi). "
                             "If omitted, saves locally to data/mock/")
    args = parser.parse_args()

    print(f"Generating mock data ({args.num_customers} customers)...")
    customers = generate_customers(args.num_customers)
    policies = generate_policies(customers)
    vehicles = generate_vehicles(customers)
    invoices = generate_invoices(policies)
    claims = generate_claims(policies)
    indexes = build_indexes(customers, vehicles, invoices, claims)

    print(f"  {len(customers):4d} customers | {len(policies):4d} policies | "
          f"{len(vehicles):4d} vehicles | {len(invoices):4d} invoices | {len(claims):4d} claims")

    if args.bucket:
        print(f"\nUploading to gs://{args.bucket}/ ...")
        upload_to_gcs(customers, policies, vehicles, invoices, claims, indexes, args.bucket)
        print(f"\nDone. Verify at: https://console.cloud.google.com/storage/browser/{args.bucket}")
    else:
        print("\nSaving locally to data/mock/ ...")
        save_locally(customers, policies, vehicles, invoices, claims, indexes)
        print("\nTo upload to GCS: python data/mock_data_generator.py --bucket adk-insurance-demo-data-mi")
