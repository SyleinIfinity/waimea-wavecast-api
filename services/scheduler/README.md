# TL07-B pipeline scheduler

`pipeline-scheduler` is the production minute loop. It claims 00/30 UTC slots
through the authenticated Database API, then calls Ingestion and Preprocessing
sequentially. PostgreSQL `gold.pipeline_cycles` enforces one station/feed
lease, records attempts and resumes eligible work after container restart.

The separate `tl05-scheduler` profile is not enabled; it dispatches model
inference workers outside TL07-B. Keep exactly one `pipeline-scheduler`
container in normal operation. The database lease also fences accidental
duplicate replicas.

Primary polls occur at minute 00 and 30. Missing expected source points get
at most five one-minute retries, then four retries at slot+10/15/20/25.
NOAA 429 responses persist `Retry-After` backoff across slots. A failed
database/downstream stage can replay the recorded GCS generation. A complete
cycle does not retry. `NO_NEW_DATA` is a normal outcome.

Monitor the private `GET /internal/v1/pipeline/status` route using the
internal token from inside a container. It includes recent Silver QC counts,
open candidate gaps and Gold freshness. `AUTO_IMPUTATION_ENABLED=false` must
stay set until an approved policy and validated model exist.
