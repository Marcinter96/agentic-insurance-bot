# 🚀 Deploy to Google Cloud (Cloud Run)

The bot ships as a single Cloud Run service running the ADK web server. One
script does everything; this doc explains it and the alternatives.

## TL;DR
```bash
gcloud auth login
gcloud config set project project-72fdf994-e492-4b76-83e
./deploy.sh
```
Output ends with the public URL. Done.

## Prerequisites
- `gcloud` CLI authenticated with an account that can create service accounts
  and grant IAM (Owner, or Project IAM Admin + Service Account Admin).
- Billing enabled on the project.
- (Optional) seed the offer catalog once so the sales agent has data:
  `python -m scripts.generate_offers`

## What `deploy.sh` does
1. **Enables APIs** — Run, Vertex AI, Cloud Build, Storage, Logging.
2. **Creates a least-privilege runtime service account** `insurance-bot-runtime` (idempotent).
3. **Grants roles** to that SA:
   | Role | Why |
   |---|---|
   | `roles/aiplatform.user` | Gemini calls via Vertex AI |
   | `roles/storage.admin` | read/write data + auto-create the SOS/offer/claims buckets |
   | `roles/logging.logWriter` | the audit trail |
4. **Builds + deploys from source** (uses the repo `Dockerfile`) with the runtime SA.
5. **Wires env vars** (project, location, all four buckets, model names) and pins
   `--min-instances 1 --max-instances 1`.

Override anything via env, e.g. `REGION=europe-west1 SERVICE=insurance-bot-eu ./deploy.sh`.

## ⚠️ Important: session state
The app keeps multi-turn state (resume, identity, claim intake) in the ADK
**session**. The default session service is in-memory, so the deploy pins the
service to a **single instance** (`min=max=1`) — correct state for a demo, but it
won't survive a restart or scale horizontally.

For production, switch to a persistent session backend (Cloud SQL / Postgres)
and drop the instance pin:
```bash
gcloud run services update insurance-bot --region us-central1 \
  --min-instances 0 --max-instances 5 \
  --set-env-vars "...,SESSION_SERVICE_URI=postgresql://USER:PASS@/db?host=/cloudsql/CONN"
```
(and run the ADK server with `--session_service_uri` accordingly).

## Tighten security (recommended after the demo)
`deploy.sh` uses `--allow-unauthenticated` so the demo URL just works. To lock it:
```bash
gcloud run services update insurance-bot --region us-central1 --no-allow-unauthenticated
# then call it with an identity token, or front it with IAP.
```
If your SA shouldn't create buckets, pre-create the four buckets and swap
`roles/storage.admin` → `roles/storage.objectAdmin` in `deploy.sh`.

## Keep buckets + service co-located
Put the Cloud Run service and all GCS buckets in the **same region**
(`us-central1` by default) to minimise latency and egress.

## Alternative: ADK-native deploy
`adk deploy cloud_run` also works and generates its own container:
```bash
adk deploy cloud_run --project "$PROJECT" --region us-central1 \
  --service_name insurance-bot --with_ui insurance_bot
# then set the SA / env / instance pin with:
gcloud run services update insurance-bot --region us-central1 \
  --service-account insurance-bot-runtime@$PROJECT.iam.gserviceaccount.com \
  --min-instances 1 --max-instances 1 --set-env-vars "…"
```
`deploy.sh` uses the source-build path instead because it sets the SA, env vars,
and instance limits in a single reproducible command.

## Verify
```bash
URL=$(gcloud run services describe insurance-bot --region us-central1 --format='value(status.url)')
open "$URL"               # the ADK dev UI
gcloud run services logs read insurance-bot --region us-central1 --limit 50
```
You should see the same `INPUT GUARDRAIL | … / CLASSIFICATION | … / ROUTING | …`
lines you see locally.
