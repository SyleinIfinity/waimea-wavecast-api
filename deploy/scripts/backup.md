# Backup runbook

Production PostgreSQL lives in Cloud SQL: enable automated backups/PITR in the console or Terraform; schedule exports to the GCS backup bucket as additional retention if necessary. Validate restores into a nonproduction instance. Do not dump plain-text passwords into backups.
