# Infrastructure as Code - planned modules

This folder is intentionally a **design scaffold**, not ready-to-apply Terraform.

Implement after first manual deployment and record actual project IDs/regions:
- `network.tf`: VPC, subnet, Private Services Access for Cloud SQL.
- `firewall.tf`: allow HTTPS/HTTP to VM, management SSH only via IAP, no DB public ingress.
- `compute.tf`: VM, service account and static IP.
- `cloud_sql.tf`: PostgreSQL instance with private IP, backups, PITR.
- `storage.tf`: raw/models/backups buckets and IAM.
- `iam.tf`: minimum privileges for backend, CI/CD workload identities.
- `monitoring.tf`: health checks, metrics, alert policies.

Never commit tfstate or provider credentials.
