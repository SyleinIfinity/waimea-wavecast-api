# 04 — Machine Learning: regression, classification, clustering, evaluation, model serving

## 0. Phân biệt yêu cầu của thầy với thuật toán đề xuất

Theo slide tuần 10, thầy minh họa prediction bằng RandomForestRegressor, lag 7 ngày, split train/test theo thời gian, MAE; **đây là ví dụ minh họa**. Đề tài Waimea cần có dự đoán + phân loại ngày mai theo slide tuần 9, trong đó phương pháp và feature cụ thể do nhóm chứng minh. Chúng ta đề xuất hồi quy `WVHT` ngày mai và phân loại nhóm sóng theo scheme v1.

## A. Regression — mục tiêu chính

**Target đề xuất (phải chốt trong báo cáo):** `median_wave_height_m` của **ngày Hawaii T+1**; đơn vị m. Không trộn kết quả median cả ngày với WVHT 30-minute observation. Mỗi sample có anchor date **T** và target ngày **T+1**.

```text
Ngày HST T−6 ... T: features (7 ngày trước và hiện tại)
                              ↓
                  f(X_T) = dự đoán median WVHT ngày T+1
```

### 1. Feature engineering

| Nhóm | Feature candidates | Chú ý leakage / missing |
|---|---|---|
| Lagged WVHT | `wave_height_median_lag0..lag6`, max/p95 | chỉ dùng các ngày <=T |
| Lagged periods | `mean_dominant_period_s_lag0..lag6` | nếu mất dữ liệu thì imputation train-only hoặc skip |
| Wave direction | `sin_mwd`, `cos_mwd` per daily summary | không dùng MWD raw degrees như biến tuyến tính thường |
| External NOAA-only | SST, wind, pressure **nếu station thực sự có** | không buộc đủ 14 chỉ số |
| Calendar | `day_of_year_sin`, `day_of_year_cos`, month | từ ngày T/forecast day được biết, không nhìn target value |
| Quality | sample count, coverage flag nếu xác định được | thiếu -> mask; tránh dùng QC từ tương lai |
| Trend prior | rolling mean/median 3/7 ngày tính đến T | `.shift()` đúng; tuyệt đối không centered rolling |

**Tuyệt đối không dùng** `WVHT` của T+1, daily feature T+1, archive cập nhật sau prediction cutoff hoặc toàn-dataset imputation fit trên test. Để backtest có tính đúng thời gian thật, nếu cần phải mô phỏng *as-of snapshots*: nguồn nào được biết trước cutoff tại T, thay vì sử dụng dữ liệu NOAA được hiệu chỉnh trong tương lai như thể đã biết tại T (đây là nguồn leakage tinh vi). Phiên bản data cutoff trong `gold.dataset_splits.data_cutoff_at`.

### 2. Dataset / splits

- Tạo `dataset_name=wave_height_next_day_v1`, snapshot Parquet immutable `gs://bucket/datasets/training/...`.
- Mỗi row: `(station_id, anchor_date_hst, target_date_hst, features..., actual_next_day_median_m)`; loại row thiếu target khỏi supervised dataset, ghi số bị loại.
- Chia theo **thời gian** train 70%, validation 15%, test 15% (chỉ là cấu hình khởi điểm), không shuffle.
- Với overlap lag horizon, dùng **gap/embargo** phù hợp để training labels không quan sát tương lai của validation/test; phụ thuộc target horizon, không chỉ size split. Cuối cùng freeze test riêng.
- Khi chọn hyperparameters, chạy TimeSeriesSplit/expanding-window trên train (có gap) hoặc chỉ validation; **không tune bằng test**.
- Preprocessor Imputer/Scaler (nếu sử dụng) phải `fit` ở train fold; transformer pipeline phát hành cùng model.
- Baselines: dự đoán `median_wave_height_m(T)` (persistence) và seasonal-naive (tùy khả năng). Không chỉ công bố MAE của Random Forest mà không đối chiếu baseline.

### 3. Thuật toán ứng viên

| Thuật toán | Vai trò | Cần viết |
|---|---|---|
| Persistence baseline | Kiểm chứng liệu ML có cải thiện | `training/baselines.py` |
| Ridge Regression | baseline tuyến tính, dễ giải thích | `training/train_regression.py` |
| RandomForestRegressor | ứng viên chính vì thầy đã minh họa | `training/train_regression.py` |
| HistGradientBoostingRegressor | tùy chọn nâng cao, chỉ khi có bằng chứng | `training/train_regression.py` |

**Đầu ra bắt buộc:** MAE (m), RMSE (m), R² (không có đơn vị), n_test, coverage các ngày, so baseline; scatter actual-vs-predicted, residual/time error profile và lỗi các ngày sóng lớn. Với target hằng số/R² không xác định, xử lý có chủ đích. Không báo cáo số liệu benchmark khi chưa train thật.

### 4. Mẫu mã hướng dẫn (pseudocode đầy đủ ý, không phải số liệu đã train)

```python
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline

# train_df, validation_df, test_df đã được CHIA THEO THỜI GIAN
# và feature_rows được tạo KHÔNG NHÌN target của ngày T+1.
model = Pipeline([
    ("imputer", SimpleImputer(strategy="median")),
    ("regressor", RandomForestRegressor(n_estimators=200, random_state=42, n_jobs=-1)),
])
model.fit(train_df[feature_cols], train_df["target_next_day_m"])
val_pred = model.predict(validation_df[feature_cols])
val_mae = mean_absolute_error(validation_df["target_next_day_m"], val_pred)
# Nếu chốt model dựa trên validation, chỉ đánh giá test 1 lần cuối.
test_pred = model.predict(test_df[feature_cols])
mae = mean_absolute_error(test_df["target_next_day_m"], test_pred)
rmse = mean_squared_error(test_df["target_next_day_m"], test_pred) ** 0.5
r2 = r2_score(test_df["target_next_day_m"], test_pred)
```

**Dự báo realtime:** mỗi ngày sau khi ngày HST T đã kết thúc và có đủ dữ liệu/độ trễ quan trắc, tải `active` model từ GCS cache, chuẩn hóa feature theo manifest, infer T+1, POST `gold.predictions`. Model chỉ được update khi đã promotion, không train on request. Output không bảo đảm dùng cho cảnh báo an toàn hàng hải.

### 5. Đánh giá sau triển khai (online evaluation)

Khi NOAA quan trắc đủ cho ngày mục tiêu, tính actual daily median với policy chất lượng tương đương, ghi `actual_value`, `absolute_error`, `evaluated_at` trong `gold.predictions`. Không sửa giá trị `predicted_value` đã phát hành. Về lâu dài thống kê drift MAE theo 30/90 ngày, coverage và lỗi theo mức sóng.

## B. Classification — mức sóng ngày mai

- `target`: class từ *median wave height ngày T+1*, theo **versioned `gold.classification_schemes`**. Chốt ngưỡng trên train sample (ví dụ percentiles 33/66 của train) **chỉ là lựa chọn nhóm**, không phải chuẩn an toàn bãi biển. Cố định boundary/version khi evaluate test/production.
- Candidate: LogisticRegression (baseline), RandomForestClassifier. Chia thời gian, xử lý mất cân bằng class, macro-F1, Accuracy, precision/recall từng lớp, confusion matrix; nếu dữ liệu quá ít cho một class thì báo rõ.
- Feature builder giống regression (có thể khác subset), model artifact riêng với label order và probabilities.
- Kết quả lưu `gold.classifications` + `model_id`, `scheme_id`, `issued_at`, target HST day, predicted_label, optional confidence/probabilities, eventual actual_label.
- **Không được gọi class thấp/cao là mức độ an toàn cho người bơi** nếu không có nghiên cứu và điều kiện vật lý bổ sung.

## C. Clustering — K-means

- Unit: một **ngày HST** có daily features đủ điều kiện, không cluster tất cả hàng 30 phút và đồng thời mô tả là “cụm ngày” mà không ghi rõ grain.
- Training k chọn qua diagnostics: k=2..8 (điều chỉnh số mẫu), silhouette, Davies–Bouldin, cluster sizes, stability; scaler fit only train.
- Model artifact lưu pipeline + centroid labels, feature version, `k`, random_state. Assign daily trên service `clustering` chỉ transform + predict (không fit).
- `gold.cluster_centroids` và `gold.cluster_assignments` chứa feature centroids, cluster ID, distance. Khoảng cách centroid KHÔNG tự động là xác suất trạng thái.
- Đặt tên cụm hậu kiểm ví dụ “sóng thấp/chu kỳ ngắn” dựa centroid thực tế; không ép cụm tương ứng mùa bằng việc sửa nhãn thủ công rồi tuyên bố là K-means phát hiện.

## D. Model registry + reproducibility + GCS

Lưu trong `gold.model_registry`: `model_name`, `model_version`, `task_type`, `algorithm`, `feature_names`, `hyperparameters`, `artifact_gcs_uri`, `artifact_gcs_generation`, `artifact_sha256`, `status`; liên kết `gold.training_runs` và `gold.model_evaluations`. Dùng `gold.dataset_splits` lưu `dataset_version`, `dataset_sha256`, `data_cutoff_at`, GCS URI + generation, splits.

Cấu trúc artifact:

```text
gs://BUCKET/models/regression/wave-height/v2026-.../
  model.joblib
  manifest.json       # target, features, lag days, HST, input data cutoff, package versions, SHA
  metrics.json        # benchmark on holdout (thực đo, không giả)
```

- **Quan trọng:** model pickle/joblib chỉ load từ bucket riêng do nhóm kiểm soát, verify SHA/generation và dependency compatibility, vì unpickle có thể chạy mã.
- Promote candidate → active sau khi test tốt hơn baseline và QA pass, rollback bằng version khác. Tránh nhiều model `active` cho cùng task/target nếu chính sách chỉ cho một active; hiện SQL v2 chưa ràng buộc unique partial cho task/target, nên kiểm tra bằng transaction trong app/migration sau.
- Không xóa model registry, training logs, evaluations, GCS model/dataset trong kịch bản xóa Gold pattern của thầy.

## E. Chi tiết đường nạp/xuất ML

```text
silver.daily_wave_features (HST + quality gates)
    → stable feature engineering + cutoff
    → frozen Parquet dataset (GCS)
    → time split (gold.dataset_splits)
    → train candidates + baselines
    → evaluations + test samples (gold.model_evaluations / evaluation_samples)
    → immutable artifact (GCS) + gold.model_registry
    → promote active (reviewed)
    → inference service (GCS model cached + latest Silver)
    → gold.predictions / gold.classifications
    → public Database API → React dashboard
```

## F. Acceptance criteria ML

1. Xóa các hàng `gold.predictions` được chọn; gọi run inference; chúng được tái tạo, `training_runs` count không đổi.
2. Một feature có timestamp > cutoff lập tức bị reject (test).
3. Train/test split theo thời gian, không shuffle, không encode target theo toàn bộ dataset.
4. Metrics có sample_count, target units, model_id/dataset snapshot/commit version.
5. Huấn luyện lại model v2 không ghi đè v1; inference reproducible khi pin generation.
6. Tên class và threshold được mô tả và version hóa.
7. Nếu model trễ hoặc không load GCS, API trả trạng thái lỗi/không sẵn sàng, không tạo prediction giả.
