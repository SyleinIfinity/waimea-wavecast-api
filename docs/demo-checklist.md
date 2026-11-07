# Course demonstration checklist

- [ ] Docker Compose up and show independent services.
- [ ] Browser UI fetches patterns via API (Network tab).
- [ ] Data refresh works with a defined interval.
- [ ] Time-series day/week/month; line/scatter/histogram.
- [ ] Trend, seasonal, residual, correlation.
- [ ] K-means clustering and interpretation.
- [ ] Predict tomorrow and classify tomorrow with pre-trained models.
- [ ] Delete **only Gold results in demo DB**, refresh UI -> no patterns.
- [ ] Call secured internal processing endpoints -> repopulate Gold without model retraining.
- [ ] Refresh Dashboard -> patterns reappear.

Because production Cloud SQL is external to Compose, record the difference between demo and cloud topology. Never run destructive demo actions against production SQL.
