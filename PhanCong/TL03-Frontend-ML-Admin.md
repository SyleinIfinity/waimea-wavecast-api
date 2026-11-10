# TL03 — FRONTEND 2: DATA MINING & ADMIN DEMO

**Người phụ trách:** Thành viên 3 — Frontend 2. **Repo:** `waimea-wavecast-frontend`. **Nhánh:** `feature/tl03-fe-ml-admin`.

**Đọc:** `Waimea_WaveCast_Frontend_Spec_v1/docs/02_SCREEN_BY_SCREEN.md` phần S05–S08, S10–S11; `03_FRONTEND_BACKEND_API_MAPPING.md`; `04_ADMIN_DEMO_SAFETY.md`; `06_ACCEPTANCE_ROADMAP.md`.

**Độc lập với TL02:** tự export React pages từ `src/features/**/index.ts` và dùng dữ liệu fixture/API adapter của mình. Tạm dùng UI wrapper mẫu trong story/demo riêng, **không sửa shell/router/theme do TL02 sở hữu**; khi ghép TL02 chỉ đăng ký các page exports.

## TL03-01 — Data Mining pages (mock API trước)

- [ ] `src/features/correlations/`: correlation heatmap Pearson/Spearman, sample counts, click cell mở scatter; ô thiếu dữ liệu = N/A; GET correlations + scatter.
- [ ] `src/features/clustering/`: cluster timeline/calendar, distribution bar, centroid profile, select model version; phân biệt ID cụm với xác suất; GET clusters + cluster profiles (nếu backend hỗ trợ).
- [ ] `src/features/forecast/`: next-day **Regression WVHT daily median** và Classification thành hai panel; lịch sử actual-vs-predicted, scheme/threshold, cutoff HST, model version; GET predictions/classifications.
- [ ] `src/features/models/`: model registry, MAE/RMSE/R², Macro-F1, baseline, confusion matrix, sample-level chart chỉ khi API có; GET models/evaluations.

## TL03-02 — System Monitor & Demo Control

- [ ] `src/features/pipeline-monitor/`: card tình trạng service, lịch sử ingestion, job status, Retry admin khi API hỗ trợ; không gọi GET health mock là chứng cứ DB/GCS thật.
- [ ] `src/features/demo-control/`: preflight, phạm vi ngày/trạm, chọn pattern Gold, preview số hàng, xác nhận hai bước, rebuild theo jobs và progress 202→status, refresh GET sau success.
- [ ] Khóa route admin trên giao diện và kiểm thử 401/403; **Backend vẫn bắt buộc xác thực**, UI guard không phải lớp bảo vệ chính. **Không làm nút “Xóa DB”;** chỉ “Xóa kết quả Gold (demo)” có guardrails trong staging. Nếu thầy muốn xóa bằng tay DBeaver, UI chỉ hiển thị trạng thái và nút tái tạo.
- [ ] Không viết mã API nào train lại model từ nút rebuild; download artifact/DB credentials tuyệt đối không có trong browser.

## TL03-03 — Test và bàn giao

- [ ] Mock API trả `202` và `job_id`, kiểm tra không thông báo thành công sớm; poll đến trạng thái cuối; timeout/409/503; Protected tables không được xuất hiện trong lựa chọn delete.
- [ ] Vitest, contract tests mock; đủ bốn trạng thái Forecast: có dự báo/chưa có model/chưa đủ dữ liệu/actual chưa có.
- [ ] Tạo `docs/contributions/TL03_STATUS.md` và `docs/contributions/TL03_FUNCTIONS.md` với chức năng thật/stub phân biệt rõ.

**Bàn giao:** 6 feature pages (bao gồm admin), mock fixtures, các page exports độc lập, tests, hai báo cáo.

**Nghiệm thu TL03:** feature pages render được với mock không cần TL02/Backend; nhãn/model version đúng; Delete không tác động đến protected schemas; UI không hiển thị % tiến độ nếu API chưa trả.

**Không sở hữu:** App shell/design tokens/global router của TL02; quyền xóa thật trong PostgreSQL của TL01.
