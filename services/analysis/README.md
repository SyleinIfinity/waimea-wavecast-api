# Analysis worker

Authenticated `POST /internal/v1/analysis/rebuild` computes Gold metrics, seasonal
components and correlations from bounded Silver reads through TL01. It never trains.
Use `X-Internal-Token` and `Idempotency-Key`; the request body includes
`from_hst`, `to_hst`, `pattern_type`, `retrain:false`.
