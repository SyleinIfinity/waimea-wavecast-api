# TL05 — Completed functions

| ID | Chức năng | Tệp | Kiểm thử |
|---|---|---|---|
| TL05-F01 | Feature T−6..T, target T+1 và split có gap | `packages/ml/features.py` | `test_features_never_use_target_in_x_and_split_has_gap` |
| TL05-F02 | Gold day/week/month, circular mean, trend và correlation | `packages/ml/analytics.py` | `test_analytics_hst_circular_and_undefined_correlation`, `test_gold_rebuild_uses_fake_gateway_without_training` |
| TL05-F03 | Fake/HTTP gateway cho TL01 | `packages/ml/gateway.py` | worker và rebuild fake |
| TL05-F04 | Parquet snapshot, regression baseline/RF, classifier, K-means | `training/` | `test_training_artifacts_and_inference_do_not_retrain` |
| TL05-F05 | GCS/local artifact generation, SHA-256, manifest | `packages/ml/artifacts.py`, `packages/gcs/models/local.py` | hash mismatch, immutable version |
| TL05-F06 | Inference chỉ nạp model `ready`, ghi Gold | `packages/ml/jobs.py` | train count bất biến sau ba inference |
| TL05-F07 | Worker auth + background job 202 | `packages/ml/worker.py`, `services/{analysis,clustering,prediction,classification}/app/main.py` | `test_worker_auth_and_background_job` |
| TL05-F08 | Scheduler poll/analysis/inference, không chạy train | `services/scheduler/app/` | `test_scheduler_registry_no_training` |

**Giới hạn:** các metrics fixture không phải kết quả NOAA thật; tích hợp GCS và PostgreSQL staging chưa kiểm chứng.
