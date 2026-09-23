# ACBI — Conversational Business Intelligence

**English** · [Tiếng Việt](#tiếng-việt)

ACBI lets anyone ask questions about **revenue, production and quality** in plain
language (Vietnamese or English) and get a chart, a table and a one-line answer.
Every number comes from a vetted data query, not from the AI, and every answer shows
its source so you can check it. No SQL or programming knowledge is needed.

- **Live demo:** https://acbi-liard.vercel.app (Vercel + Neon, always on; see [docs/VERCEL_DEPLOY.md](docs/VERCEL_DEPLOY.md)).
  The login page has "Login as manager / sales / production_a / it_admin" buttons.
- **In-app guide:** click **Guide** in the top bar of the web app (also at `/#guide`),
  in English and Vietnamese. It covers everything below in more detail.

## The sample data, in plain words

The data is **AdventureWorks**, Microsoft's well-known sample about a fictional
bicycle company. Think of two big ledgers: one records every **sales order**, the other
every **production run**. Each database "table" is like an Excel sheet: rows are events,
columns describe them. The application only **reads** the data and never changes it.

| Ledger | What one row is | Size in this demo |
|---|---|---|
| Sales orders (+ order lines) | one customer order (and each item inside it) | 31,465 orders, 121,317 lines |
| Sales territories | one of 10 territories: Northwest, Northeast, Central, Southwest, Southeast, Canada, France, Germany, United Kingdom, Australia | 10 |
| Products and categories | one product; 4 categories (Bikes, Components, Clothing, Accessories) | 504 products |
| Work orders | one production run: product, units ordered, units scrapped, due date, finish date | 72,591 |
| Routing steps and locations | the steps a work order passes through, at 14 locations (Frame Welding, Paint, Subassembly, Final Assembly ...) | 14 locations |
| Factories A, B, C | **made up for the demo**: the 14 locations are assigned in rotation to 3 factories so factory-level access can be tried | 3 |

Data covers **May 2022 – June 2025** (dates were shifted so the data ends on
**2025-06-29**; "this month" means June 2025).

Revenue follows the sales side. Output, defects and on-time follow the production side.
The two sides meet at *Product*.

## What you can ask

| Metric | Plain meaning | How it is calculated |
|---|---|---|
| Revenue | merchandise sold | sum of order subtotals by order date (tax and shipping excluded) |
| Revenue growth | % change against the period just before | (this − previous) ÷ previous, for the latest month or quarter |
| Production output | good units finished | units ordered − units scrapped, by finish date |
| Defect rate | share of units scrapped | scrapped ÷ ordered |
| On-time completion rate | share of work orders finished by their due date | on time ÷ all orders |

Add "by ..." to break a figure down: by month / week / day, territory, product,
category, factory, production line, or scrap reason.

Examples that work:

- `Revenue 2024 by territory` → `What about 2023?` → `Only the top 3` → `Draw a pie chart`
- `Production output last month by factory`
- `Defect rate by product last month, top 5`
- `On-time rate last quarter by production line`
- `Forecast revenue for the next 6 months` (always labelled as a forecast)
- `Which metrics can you report? How is revenue calculated?`

Several tasks in one message: the first runs, the rest are listed; type "next" to continue.
Charts are chosen by the shape of the result (number card, line, pie/donut, bar, stacked
bar) and can be changed in words: "switch to a donut chart".

**Not supported:** explaining reasons ("why"), splitting by quarter, staff / salary / customer / profit questions. The system says so
instead of guessing.

**Comparing periods:** ask "Compare Canada revenue 2023 and 2024" (up to 4 years, quarters or
months of one kind, also by territory or factory). Each period is its own checked query.

## Roles

| Account | Can see | Cannot see |
|---|---|---|
| `manager` | everything | account administration |
| `sales` | revenue and revenue growth | production, quality |
| `production_a` | output, defects, on-time for Factory A | revenue; factories B and C |
| `it_admin` | administration page | all business figures |

## Known limitations

- Sample data only (AdventureWorks, up to 2025-06-29; June 2025 is thin). Factories A/B/C are made up for the demo.
- Five metrics. No profit, customers, staff or currency conversion, and no explanation of reasons ("why").
- No split by quarter; "growth" only exists for the latest month or quarter, but up to 4 years, quarters or months can be compared side by side.
- Forecasts are a simple trend with a 24–31% test error, at most 12 months ahead.
- Answers depend on free-tier AI providers (Gemini, LiteRouter, Groq): 2–8 s per answer, and "AI service is busy" appears when limits are hit.
- The demo's passwordless "Login as" buttons and temporary tunnel link are for demonstration only; turn them off with `DEMO_LOGIN_ENABLED=false`.

## Run it

```
docker compose -p acbi -f deploy/docker-compose.yml -f deploy/docker-compose.local.yml up -d --build --wait
```

Open http://localhost:8080. Add `-f deploy/docker-compose.demo.yml` when serving through
a tunnel (it sets the HTTPS forwarding header). Set `DEMO_LOGIN_ENABLED=false` to turn
off the passwordless demo buttons. Language-model keys go in `deploy/.env` (Groq) and
`deploy/llm-providers.local` (Gemini, LiteRouter); providers are tried in
`LLM_PROVIDER_ORDER` (default `gemini,groq,literouter`).

## More documentation

- [Assumptions and approval decisions](docs/ASSUMPTIONS.md) · [Architecture](docs/ARCHITECTURE.md) · [Runbook](docs/RUNBOOK.md) · [API](docs/API.md)
- [Current status and limitations](docs/CURRENT_STATUS.md) · [Local demo](docs/LOCAL_DEMO.md) · [Hosted demo](docs/VERCEL_DEPLOY.md)
- Phase reports: [0](docs/PHASE_0_REPORT.md), [1](docs/PHASE_1_REPORT.md), [2](docs/PHASE_2_REPORT.md), [3](docs/PHASE_3_REPORT.md), [4](docs/PHASE_4_REPORT.md)
- [Approved dictionary](data/business_dictionary/dictionary.yaml) · [Golden questions](data/eval/golden_questions.yaml)

---

# Tiếng Việt

**[English](#acbi--conversational-business-intelligence)** · Tiếng Việt

ACBI cho phép bất kỳ ai hỏi về **doanh thu, sản xuất và chất lượng** bằng lời thường
(tiếng Việt hoặc tiếng Anh) và nhận về biểu đồ, bảng số cùng một câu trả lời ngắn. Mọi
con số đều do câu lệnh dữ liệu đã được kiểm duyệt tính ra, không phải AI tự nghĩ, và mỗi
câu trả lời đều ghi nguồn để bạn kiểm chứng. Không cần biết SQL hay lập trình.

- **Bản demo trực tuyến:** https://acbi-liard.vercel.app (Vercel + Neon, chạy liên tục; xem [docs/VERCEL_DEPLOY.md](docs/VERCEL_DEPLOY.md)).
  Trang đăng nhập có các nút "Đăng nhập với manager / sales / production_a / it_admin".
- **Hướng dẫn trong ứng dụng:** bấm **Hướng dẫn** ở thanh trên cùng (hoặc mở `/#guide`),
  có cả tiếng Việt và tiếng Anh, chi tiết hơn phần dưới đây.

## Dữ liệu mẫu, giải thích đời thường

Dữ liệu là **AdventureWorks**, bộ dữ liệu mẫu nổi tiếng của Microsoft về một công ty xe
đạp tưởng tượng. Hãy hình dung hai cuốn sổ lớn: một cuốn ghi mọi **đơn bán hàng**, một
cuốn ghi mọi **đợt sản xuất**. Mỗi "bảng" trong cơ sở dữ liệu giống một trang tính
Excel: hàng là từng sự việc, cột mô tả sự việc đó. Ứng dụng chỉ **đọc** dữ liệu, không bao
giờ sửa.

| Sổ | Một hàng là gì | Quy mô trong bản demo |
|---|---|---|
| Đơn hàng (và dòng hàng) | một lần khách đặt hàng (và từng món trong đơn) | 31.465 đơn, 121.317 dòng |
| Khu vực bán hàng | một trong 10 khu vực: Northwest, Northeast, Central, Southwest, Southeast, Canada, France, Germany, United Kingdom, Australia | 10 |
| Sản phẩm và nhóm sản phẩm | một sản phẩm; 4 nhóm (Bikes, Components, Clothing, Accessories) | 504 sản phẩm |
| Lệnh sản xuất | một đợt sản xuất: sản phẩm, số đặt làm, số hỏng, hạn giao, ngày xong | 72.591 |
| Công đoạn và địa điểm | các bước một lệnh sản xuất đi qua, ở 14 địa điểm (Frame Welding, Paint, Subassembly, Final Assembly ...) | 14 địa điểm |
| Nhà máy A, B, C | **do demo tự đặt ra**: 14 địa điểm được gán xoay vòng vào 3 nhà máy để thử phân quyền theo nhà máy | 3 |

Dữ liệu trải từ **tháng 5/2022 đến tháng 6/2025** (ngày đã được dời để kết thúc vào
**29/06/2025**; "tháng này" nghĩa là tháng 6/2025).

Doanh thu đi theo nhánh bán hàng. Sản lượng, phế phẩm và đúng hạn đi theo nhánh sản
xuất. Hai nhánh gặp nhau ở *Sản phẩm*.

## Bạn có thể hỏi gì

| Chỉ số | Ý nghĩa | Cách tính |
|---|---|---|
| Doanh thu | tiền hàng bán ra | tổng tiền hàng các đơn theo ngày đặt (chưa gồm thuế và phí vận chuyển) |
| Tăng trưởng doanh thu | % thay đổi so với kỳ ngay trước | (kỳ này − kỳ trước) ÷ kỳ trước, cho tháng hoặc quý gần nhất |
| Sản lượng | số sản phẩm đạt yêu cầu đã làm xong | số đặt làm − số hỏng, theo ngày hoàn thành |
| Tỷ lệ phế phẩm | tỷ lệ sản phẩm bị hỏng | số hỏng ÷ số đặt làm |
| Tỷ lệ hoàn thành đúng hạn | tỷ lệ lệnh sản xuất xong đúng hoặc trước hạn giao | số lệnh đúng hạn ÷ tổng số lệnh |

Thêm "theo ..." để chia nhỏ số liệu: theo tháng / tuần / ngày, khu vực, sản phẩm, nhóm
sản phẩm, nhà máy, dây chuyền hoặc lý do phế phẩm.

Ví dụ hỏi được:

- `Doanh thu năm 2024 theo khu vực` → `Còn năm 2023?` → `Chỉ lấy top 3` → `Vẽ biểu đồ tròn`
- `Sản lượng tháng trước theo nhà máy`
- `Tỷ lệ phế phẩm theo sản phẩm tháng trước, top 5`
- `Tỷ lệ hoàn thành đúng hạn quý trước theo dây chuyền`
- `Dự báo doanh thu 6 tháng tới` (luôn ghi rõ là dự báo)
- `Bạn báo cáo được những chỉ số nào? Doanh thu tính thế nào?`

Nhiều việc trong một câu: hệ thống làm việc đầu tiên và liệt kê các việc còn lại; gõ
"tiếp đi" để làm tiếp. Biểu đồ được chọn theo hình dạng kết quả (thẻ số, đường, tròn hoặc
vành khuyên, cột, cột chồng) và đổi được bằng lời: "đổi sang biểu đồ vành khuyên".

**Chưa hỗ trợ:** giải thích nguyên nhân ("tại sao"), chia theo quý,
câu hỏi về nhân sự, lương, khách hàng, lợi nhuận. Hệ thống nói rõ điều đó thay vì đoán.

**So sánh các kỳ:** hỏi "So sánh doanh thu Canada năm 2023 và 2024" (tối đa 4 năm, quý hoặc tháng cùng loại, có thể chia theo khu vực hoặc nhà máy). Mỗi kỳ là một truy vấn riêng đã được kiểm tra.

## Vai trò

| Tài khoản | Thấy được | Không thấy |
|---|---|---|
| `manager` | mọi thứ | trang quản trị tài khoản |
| `sales` | doanh thu và tăng trưởng doanh thu | sản xuất, chất lượng |
| `production_a` | sản lượng, phế phẩm, đúng hạn của Factory A | doanh thu; nhà máy B và C |
| `it_admin` | trang quản trị | mọi số liệu kinh doanh |

## Hạn chế hiện tại

- Chỉ là dữ liệu mẫu (AdventureWorks, đến 29/06/2025; tháng 6/2025 rất mỏng). Nhà máy A/B/C là giả lập.
- Năm chỉ số. Không có lợi nhuận, khách hàng, nhân sự, quy đổi tiền tệ; không giải thích nguyên nhân ("tại sao").
- Chưa chia theo quý; "tăng trưởng" chỉ có cho tháng hoặc quý gần nhất, nhưng so sánh cạnh nhau tối đa 4 năm, quý hoặc tháng thì được.
- Dự báo là đường xu hướng đơn giản, sai số kiểm thử 24–31%, tối đa 12 tháng.
- Câu trả lời phụ thuộc dịch vụ AI miễn phí (Gemini, LiteRouter, Groq): 2–8 giây mỗi câu, và có lúc báo "Hệ thống AI đang quá tải".
- Nút "Đăng nhập với ..." không cần mật khẩu và link tunnel tạm thời chỉ dành cho demo; tắt bằng `DEMO_LOGIN_ENABLED=false`.

## Chạy thử

```
docker compose -p acbi -f deploy/docker-compose.yml -f deploy/docker-compose.local.yml up -d --build --wait
```

Mở http://localhost:8080. Thêm `-f deploy/docker-compose.demo.yml` khi chạy qua đường hầm
(nó bật header HTTPS). Đặt `DEMO_LOGIN_ENABLED=false` để tắt các nút đăng nhập nhanh
không cần mật khẩu. Khóa của mô hình ngôn ngữ đặt trong `deploy/.env` (Groq) và
`deploy/llm-providers.local` (Gemini, LiteRouter); thứ tự thử theo `LLM_PROVIDER_ORDER`
(mặc định `gemini,groq,literouter`).
