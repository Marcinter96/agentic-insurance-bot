#!/usr/bin/env bash
#
# One-shot deploy of the insurance bot to Cloud Run.
#
#   ./deploy.sh
#
# Idempotent: enables APIs, creates a least-privilege runtime service account,
# grants roles, then builds + deploys from source with all env vars wired.
# Override any value via environment, e.g.:  REGION=europe-west1 ./deploy.sh
#
set -euo pipefail

# ── Config (override via env) ────────────────────────────────────────────────
PROJECT="${GOOGLE_CLOUD_PROJECT:-project-72fdf994-e492-4b76-83e}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-insurance-bot}"
SA_NAME="${SA_NAME:-insurance-bot-runtime}"
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

DATA_BUCKET="${GCS_BUCKET:-adk-insurance-demo-data-mi}"
SOS_BUCKET="${SOS_BUCKET:-adk-insurance-sos-mi}"
OFFER_BUCKET="${OFFER_BUCKET:-adk-insurance-offer-mi}"
CLAIMS_BUCKET="${CLAIMS_BUCKET:-adk-insurance-claims-mi}"
LLM_MODEL="${LLM_MODEL:-gemini-2.5-flash}"
BRAIN_MODEL="${BRAIN_MODEL:-gemini-2.5-flash-lite}"

echo "▶ Project=$PROJECT  Region=$REGION  Service=$SERVICE  SA=$SA_EMAIL"

# ── 1. Enable APIs ───────────────────────────────────────────────────────────
echo "▶ Enabling APIs…"
gcloud services enable \
  run.googleapis.com aiplatform.googleapis.com cloudbuild.googleapis.com \
  storage.googleapis.com logging.googleapis.com \
  --project "$PROJECT"

# ── 2. Runtime service account (idempotent) ──────────────────────────────────
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT" >/dev/null 2>&1; then
  echo "▶ Creating service account $SA_EMAIL…"
  gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT" \
    --display-name "Insurance Bot Cloud Run runtime"
else
  echo "▶ Service account already exists."
fi

# ── 3. Grant least-privilege roles ───────────────────────────────────────────
# storage.admin is needed because the app auto-creates the SOS/offer/claims
# buckets on first write. If you pre-create them, downgrade to
# roles/storage.objectAdmin.
echo "▶ Granting roles…"
for ROLE in roles/aiplatform.user roles/storage.admin roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:${SA_EMAIL}" --role "$ROLE" \
    --condition=None --quiet >/dev/null
done

# ── 4. Build + deploy from source (uses the repo Dockerfile) ─────────────────
echo "▶ Building & deploying to Cloud Run…"
gcloud run deploy "$SERVICE" \
  --source . \
  --project "$PROJECT" --region "$REGION" \
  --service-account "$SA_EMAIL" \
  --allow-unauthenticated \
  --memory 1Gi --cpu 1 --timeout 600 \
  --min-instances 1 --max-instances 1 \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION},GCS_BUCKET=${DATA_BUCKET},SOS_BUCKET=${SOS_BUCKET},OFFER_BUCKET=${OFFER_BUCKET},CLAIMS_BUCKET=${CLAIMS_BUCKET},LLM_MODEL=${LLM_MODEL},BRAIN_MODEL=${BRAIN_MODEL}"

# ── 5. Done ──────────────────────────────────────────────────────────────────
URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format='value(status.url)')"
echo
echo "✅ Deployed: $URL"
echo "   Open ${URL}/dev-ui  (or just ${URL})"
