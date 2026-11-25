# Calisthenics 1.5 shadow học theo lô 300 cặp

Timer `waimea-calisthenics-15-live.timer` kiểm tra số cặp 30 phút mới sau mốc thời gian cuối mà phiên bản đang chạy đã học. Mỗi mốc mục tiêu chỉ tính một dự báo đã phát trước hạn, đã lưu trên GCS và có kết quả đối chiếu khớp quan trắc Silver đã kiểm định.

Khi đủ 300 cặp, worker lấy đúng 300 cặp hợp lệ từ cũ đến mới và cập nhật từ phiên bản đang chạy với `shuffle=False`. Sau khi xác minh artifact trên GCS, worker đăng ký phiên bản `v1.5-shadow300-*` ở trạng thái `DRAFT`. Worker dự báo dùng phiên bản mới cho mốc đủ điều kiện tiếp theo. Lô sau bắt đầu sau mốc mục tiêu cuối của lô vừa học.

Chỉ số của lô 300 đo các dự báo shadow đã thực sự phát trước hạn. Độ chính xác của phiên bản mới được đánh giá riêng bằng các dự báo của phiên bản đó sau khi đăng ký.

Kiểm tra: `sudo systemctl status waimea-calisthenics-15-live.timer` và `sudo journalctl -u waimea-calisthenics-15-live.service -n 30 --no-pager`. Trường `milestone_300` cho biết `waiting_for_300_targets`, `already_trained` hoặc kết quả đăng ký model. Timer vẫn tự chạy sau khi VM khởi động lại.
