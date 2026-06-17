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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure gcloud is reachable even when bash does not inherit zsh PATH.
if ! command -v gcloud >/dev/null 2>&1; then
  for GCLOUD_BIN_DIR in \
    "$SCRIPT_DIR/../google-cloud-sdk/bin" \
    "$HOME/google-cloud-sdk/bin" \
    "/opt/homebrew/Caskroom/google-cloud-sdk/latest/google-cloud-sdk/bin"
  do
    if [ -x "$GCLOUD_BIN_DIR/gcloud" ]; then
      export PATH="$GCLOUD_BIN_DIR:$PATH"
      break
    fi
  done
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "❌ gcloud not found. Install Google Cloud SDK and re-run."
  echo "   macOS (Homebrew): brew install --cask google-cloud-sdk"
  exit 1
fi

# ── Config (override via env) ────────────────────────────────────────────────
PROJECT="${GOOGLE_CLOUD_PROJECT:-project-72fdf994-e492-4b76-83e}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-insurance-bot}"
SA_NAME="${SA_NAME:-insurance-bot-runtime}"
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
CLOUDBUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

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
  echo "▶ Creating service account ${SA_EMAIL}..."
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

# Source deployments may use compute or cloudbuild identities to pull source
# archive objects and emit build logs.
for BUILD_SA in "$COMPUTE_SA" "$CLOUDBUILD_SA"; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:${BUILD_SA}" --role "roles/storage.objectViewer" \
    --condition=None --quiet >/dev/null || true
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:${BUILD_SA}" --role "roles/logging.logWriter" \
    --condition=None --quiet >/dev/null || true
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:${BUILD_SA}" --role "roles/artifactregistry.writer" \
    --condition=None --quiet >/dev/null || true
done

# ── 4. Build + deploy from source (uses the repo Dockerfile) ─────────────────
echo "▶ Building & deploying to Cloud Run…"
gcloud run deploy "$SERVICE" \
  --source . \
  --project "$PROJECT" --region "$REGION" \
  --service-account "$SA_EMAIL" \
  --quiet \
  --allow-unauthenticated \
  --memory 1Gi --cpu 1 --timeout 600 \
  --min-instances 1 --max-instances 1 \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION},GCS_BUCKET=${DATA_BUCKET},SOS_BUCKET=${SOS_BUCKET},OFFER_BUCKET=${OFFER_BUCKET},CLAIMS_BUCKET=${CLAIMS_BUCKET},LLM_MODEL=${LLM_MODEL},BRAIN_MODEL=${BRAIN_MODEL}"

# ── 5. Done ──────────────────────────────────────────────────────────────────
URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format='value(status.url)')"
echo
echo "✅ Deployed: $URL"
echo "   Open ${URL}/dev-ui  (or just ${URL})"
