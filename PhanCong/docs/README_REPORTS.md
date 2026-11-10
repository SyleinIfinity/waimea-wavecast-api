# Quy ước báo cáo bắt buộc

**Trong từng nhánh/PR:** Mỗi người tạo **hai file khác tên nhau** dưới `docs/contributions/` để không conflict Git: `TL01_STATUS.md`, `TL01_FUNCTIONS.md`, …, `TL05_STATUS.md`, `TL05_FUNCTIONS.md`.

**Trong repo sau khi hợp nhất:** script `scripts/aggregate_reports.py` đọc toàn bộ các báo cáo cá nhân, tạo ra **hai file chính thức** trong `docs/`:

- `STATUS_REPORT.md`: danh sách tiến độ, lỗi, blocker, kết quả chạy test theo người.
- `COMPLETED_FUNCTIONS.md`: catalog function/component/endpoint đã triển khai **và có bằng chứng kiểm thử**, không tính ý tưởng hay stub.

Lệnh chạy khi đã có 2 repo (Python standard library):

```bash
python scripts/aggregate_reports.py \
  --backend-repo /path/waimea-wavecast-backend \
  --frontend-repo /path/waimea-wavecast-frontend \
  --output /path/final-delivery/docs
```

Nếu không có tham số, script đọc các bản mẫu `docs/contributions` trong gói kế hoạch để tạo báo cáo ban đầu.

**Quy tắc điền:** báo cáo phải dựa trên lệnh test và log thật; trạng thái chỉ là `DONE`, `PARTIAL`, `BLOCKED`, `NOT_STARTED`, không báo `DONE` chỉ vì tạo file trống. Trong `FUNCTIONS.md`, một mục function phải có code path + input/output + bằng chứng test. Nếu chưa triển khai, ghi `(Chưa có function được nghiệm thu)`.

**Sau khi tạo:** reviewer đối chiếu số function với actual source/test, review Pull Request, chạy end-to-end; script gộp không tự xác nhận chất lượng code.
