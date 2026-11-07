# Cloud SQL PostgreSQL

Production database lives in managed **Cloud SQL**, not in this git repository or an always-on DB container in Compose.

`migrations/001_init.sql` creates bronze/silver/gold schemas. Run SQL migrations explicitly using a migration account and a protected connection. Cloud SQL automated backup/PITR should be enabled separately.

`dbwaimea.liems.io.vn` is an **optional private DNS alias**, not a requirement when using Cloud SQL Auth Proxy.
