# Chạy 10 câu trên web demo

Ngày chạy: 2026-10-14. Địa chỉ: https://acbi-liard.vercel.app. Chạy qua giao diện web trong trình duyệt, đăng nhập bằng nút demo (không dùng mật khẩu), chọn AI "Tự động" (Gemini và LiteRouter đều "sẵn sàng"). Mỗi câu hỏi mở một cuộc trò chuyện mới, trừ câu 6 (hỏi nối tiếp câu 1).

Con số được tính lại bằng SQL viết tay, chỉ đọc, trên kho dữ liệu AdventureWorks (không dùng bộ dựng truy vấn của ứng dụng).

## Kết quả

| # | Tài khoản | Câu hỏi | Hệ thống trả lời | Đối chiếu SQL độc lập | Kết luận |
|---|---|---|---|---|---|
| 1 | manager | Doanh thu theo khu vực năm 2024 | 10 nhóm; cao nhất Southwest 9.121.931,65; thấp nhất Southeast 2.399.947,38; tổng 43.671.889,50; biểu đồ cột | Khớp: 10 nhóm, cùng các số, tổng 43.671.889,50 | Đạt |
| 2 | manager | Doanh thu theo tuần trong tháng 3 năm 2025 | 6 tuần, bảng và biểu đồ. Lần 1: thấp nhất 31/03 và "giảm 70,9%" (so hai tuần lẻ). **Sau khi sửa (chạy lại 14/10):** cao nhất 24/03 (3.767.708,93), thấp nhất 10/03 (308.973,20), kèm ghi chú tuần 24/02 và 31/03 chưa đủ 7 ngày nên không tính; xu hướng từ 03/03 đến 24/03 tăng 987,5%. Bảng và biểu đồ vẫn đủ 6 tuần | Khớp cả 6 tuần | Đạt, đã sửa lỗi so sánh tuần lẻ |
| 3 | manager | Cho tôi xem số liệu | Hỏi lại: "Bạn muốn xem chỉ số nào?" kèm 5 chỉ số hỗ trợ; không đoán | Không có số để đối chiếu | Đạt |
| 4 | manager | Doanh thu Canada năm 2023 và 2024, mức tăng bao nhiêu? | 2023: 5.580.848,05; 2024: 6.231.209,96; tăng 11,7% | Khớp; 6.231.209,96 ÷ 5.580.848,05 = 1,1165 | Đạt |
| 5 | manager | Doanh thu quý trước, rồi sản lượng của Factory A tháng trước | Hai khối đánh số: (1) doanh thu 01/01 đến 31/03/2025 = 12.830.208,58; (2) sản lượng Factory A 01/05 đến 31/05/2025 = 40.942 | Khớp cả hai (Factory A tháng 5/2025 = 40.942) | Đạt |
| 6 | manager (nối tiếp câu 1) | Còn Pháp thì sao? | Giữ chỉ số và kỳ, đổi khu vực: France 2024 = 3.816.543,33 | Khớp (France 2024 = 3.816.543,33) | Đạt |
| 7 | manager | Doanh thu của khu vực Atlantis năm 2024 | "Tôi không nhận ra khu vực này", liệt kê 10 khu vực có dữ liệu; không tạo số | Không có số để đối chiếu | Đạt |
| 8 | sales | Sản lượng Factory A năm 2024 | "Yêu cầu nằm ngoài quyền truy cập của bạn."; không có dữ liệu | Không có số để đối chiếu | Đạt |
| 9 | it_admin | Doanh thu năm 2024 | Giao diện của it_admin chỉ có trang "Quản trị hệ thống", không có ô chat. Gọi thẳng API bằng phiên demo của it_admin: HTTP 403, trạng thái `denied`, "Business data is outside your scope", không có dòng dữ liệu | Không có số để đối chiếu | Đạt (kiểm bằng API) |
| 10 | manager | Dự báo doanh thu sáu tháng tới | Mở đầu "Dự báo, không phải số liệu đã ghi nhận"; 06/2025 đến 11/2025 khoảng 22.637.850; khoảng 95% mỗi tháng 1.497.680 đến 6.048.270; ETS(A,N,N), 36 tháng, sai số kiểm thử 29%; bảng và biểu đồ thực tế với dự báo | Không đối chiếu: số dự báo do mô hình tính, chuỗi thực tế chưa kiểm lại trong lượt này | Đạt về nhãn và cách trình bày |

Tổng: 10 trên 10 câu đúng kỳ vọng. Chưa đo thời gian trả lời chính xác (mỗi câu tôi chờ cố định 8 đến 15 giây rồi đọc kết quả).

## Điều quan sát thêm (không làm câu nào trượt)

1. **Câu 2, tuần đầu và tuần cuối chỉ là một phần tuần (đã sửa).** Tuần 24/02 chỉ có 01 và 02/03; tuần 31/03 chỉ có 1 ngày. Lần chạy đầu câu trả lời nói "từ 24/02/2025 đến 31/03/2025, doanh thu giảm 70,9%", tức so hai tuần không đầy đủ. Đã sửa trong code (commit `b2f12a5`) và deploy lên Vercel: các tuần chưa đủ 7 ngày trong kỳ bị loại khỏi mọi so sánh và được nêu trong một ghi chú; chạy lại trên web cho kết quả như bảng trên. Có thêm một test tự động cho trường hợp này (288 test đạt).
2. **Câu 2, tuần 24/03 gấp khoảng 10 lần các tuần khác** (3,77 triệu so với khoảng 0,3 đến 0,4 triệu). Số đúng theo dữ liệu mẫu, nhưng nếu bị hỏi thì cần giải thích đó là đặc điểm dữ liệu AdventureWorks.
3. **Câu 9, thông báo của API bằng tiếng Anh** ("Business data is outside your scope"), trong khi giao diện sales hiển thị tiếng Việt. it_admin không gặp thông báo này vì không có ô chat.
4. **Danh sách "Kết quả đã lưu" của tài khoản manager** đang chứa nhiều cuộc trò chuyện cũ từ các lần kiểm thử trước. Nếu chiếu lên màn hình trước hội đồng, nên dọn cho gọn.
5. Trình duyệt trong ứng dụng đang hẹp nên trang hiển thị theo bố cục điện thoại; không ảnh hưởng kết quả.

## Bằng chứng

Ảnh chụp màn hình các câu 1, 2, 3, 4, 8, 9 nằm trong thư mục `docs/demo_10_cau/`. Câu 5, 6, 7, 10 được đọc trực tiếp từ nội dung trang (văn bản đầy đủ ghi ở bảng trên).
