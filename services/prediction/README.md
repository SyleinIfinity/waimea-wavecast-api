# Prediction worker

Authenticated `POST /internal/v1/prediction/run` predicts next HST-day median WVHT
using a promoted, hash-verified regression bundle. It never fits on a request.
