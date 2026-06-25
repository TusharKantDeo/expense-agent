# 🧹 GCP Deployed Resources & Cleanup Reference

Below is a complete list of all active resources and services deployed in Google Cloud for the **Ambient Expense Agent** project, along with the corresponding `gcloud` commands to delete and clean them up.

---

## 📋 Active Resources List

| Service / Resource Type | Resource Name / ID | Region / Location | Purpose |
| :--- | :--- | :--- | :--- |
| **Vertex AI Reasoning Engine** | `projects/448647795185/locations/us-east1/reasoningEngines/4662457067351572480` | `us-east1` | Core Agent Runtime & Workflow |
| **Cloud Run Service** | `expense-manager-dashboard` | `us-east1` | Web UI Dashboard |
| **Pub/Sub Topic** | `expense-reports` | Global | Incoming expense report channel |
| **Pub/Sub Push Subscription** | `expense-reports-push` | Global | Push pipeline to Reasoning Engine |
| **Pub/Sub Dead-Letter Topic** | `expense-reports-dead-letter` | Global | Handles failed message deliveries |
| **Pub/Sub Pull Subscription** | `dl-pull-sub` | Global | Pull subscription for dead letters |
| **Service Account** | `pubsub-invoker@ambientagents.iam.gserviceaccount.com` | Global | Authorizes Pub/Sub OIDC calls |
| **GCS Storage Bucket** | `gs://run-sources-ambientagents-us-east1` | `us-east1` | Deployed Cloud Run source tarballs |

---

## 🗑️ Cleanup Deletion Commands

Run the following commands in sequence to completely delete all deployed resources from your GCP project:

### 1. Delete Vertex AI Reasoning Engine (Agent Runtime)
```bash
gcloud ai reasoning-engines delete 4662457067351572480 \
  --project=ambientagents \
  --location=us-east1 \
  --quiet
```

### 2. Delete Cloud Run Dashboard
```bash
gcloud run services delete expense-manager-dashboard \
  --project=ambientagents \
  --region=us-east1 \
  --quiet
```

### 3. Delete Pub/Sub Subscriptions
```bash
gcloud pubsub subscriptions delete expense-reports-push dl-pull-sub \
  --project=ambientagents
```

### 4. Delete Pub/Sub Topics
```bash
gcloud pubsub topics delete expense-reports expense-reports-dead-letter \
  --project=ambientagents
```

### 5. Delete Service Account
```bash
gcloud iam service-accounts delete pubsub-invoker@ambientagents.iam.gserviceaccount.com \
  --project=ambientagents \
  --quiet
```

### 6. Delete GCS Source Bucket
> [!WARNING]
> This command will permanently delete all contents inside the source bucket before deleting the bucket itself.
```bash
gcloud storage rm --recursive gs://run-sources-ambientagents-us-east1
```
