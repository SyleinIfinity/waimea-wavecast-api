# NOAA observation arrival email

When PostgreSQL commits a new realtime Silver row with Bronze lineage, it adds
an outbox row and notifies the VM relay in the same transaction. The relay calls
the private Cloud Run function using a Google-signed identity token. The
function checks that the public Bronze API returns the exact timestamp and an
original raw line, that the frontend origin can read the response, and that the
frontend revision supports the link. It then sends the email through SMTP.
`observed_at` is the NOAA measurement time. `first_seen_at` is the timestamp
stored by PostgreSQL when the observation was first inserted.

## Cloud Run and SMTP

In Cloud Run, use **Write a function** to create `waimea-noaa-arrival-email` in
`asia-southeast1`, select Python, and require authentication. Deploy the
`main.py` and `requirements.txt` in this directory with entry point
`send_noaa_arrival_email`. In **Variables & Secrets** set the following.

| Name | Value |
| --- | --- |
| `WAIMEA_FRONTEND_URL` | `https://waimea.liems.io.vn` |
| `WAIMEA_API_V1_URL` | `https://apiwaimea.liems.io.vn/api/v1` |
| `SMTP_HOST` | `smtp.tino.vn`, or the submission host shown by the mailbox provider |
| `SMTP_PORT` | `587` for STARTTLS |
| `SMTP_USER` | The full address of the sending mailbox |
| `SMTP_PASSWORD` | Reference the mailbox password in Secret Manager |
| `EMAIL_TO` | The configured recipient |

Grant the function's runtime service account Secret Accessor on the SMTP secret.
Grant the Compute Engine VM service account Cloud Run Invoker on this function.
Use the service's default `run.app` URL as `WAIMEA_ARRIVAL_FUNCTION_URL` in the VM's
`.env.production`. Deploy the backend and frontend before enabling email.

## Activate and verify

Apply `database/migrations/17_observation_arrival_email.sql` to the deployment
database after the two public services and SMTP are ready. The migration starts
queuing future inserts without copying existing rows. Start the Compose service
`observation-arrival-relay` using the `arrival-email` profile and production
overlay. The service uses Docker's restart policy and wakes on committed
inserts. Its five-second fallback wakes the relay to recover missed signals.

Verify the next genuine arrival in the relay log, Cloud Run log, and destination
mailbox. Open the email link from a fresh browser tab. The Bronze table should
show the original line for the exact UTC timestamp. A failed public visibility
check or SMTP submission leaves the event pending. The relay uses a database
row lock and a stable SMTP Message-ID. If SMTP accepts a message and the
connection or process dies before PostgreSQL records that success, a retry can
still produce a duplicate because SMTP does not provide transactional delivery.
