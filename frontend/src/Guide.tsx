import React from 'react';

type Text = {vi: string; en: string};
type Card = {title: Text; body: Text; example?: Text; note?: Text};

const t = (vi: string, en: string): Text => ({vi, en});

const STEPS: Card[] = [
  {title: t('1. Đăng nhập', '1. Sign in'), body: t('Chọn tài khoản theo vai trò của bạn. Mỗi vai trò chỉ thấy phần dữ liệu được phép.', 'Pick the account that matches your role. Each role only sees the data it is allowed to see.')},
  {title: t('2. Đặt câu hỏi bằng lời thường', '2. Ask in plain words'), body: t('Gõ như khi nhắn tin cho đồng nghiệp, bằng tiếng Việt hoặc tiếng Anh. Không cần biết SQL hay tên bảng.', 'Type as you would message a colleague, in Vietnamese or English. No SQL or table names needed.'), example: t('Doanh thu năm 2024 theo khu vực', 'Revenue by territory in 2024')},
  {title: t('3. Đọc kết quả và hỏi tiếp', '3. Read the result and follow up'), body: t('Mỗi câu trả lời có một câu tóm tắt, biểu đồ, bảng số và phần "Nguồn dữ liệu & cách tính" để kiểm chứng. Hỏi nối tiếp như đang trò chuyện.', 'Every answer has a one-line summary, a chart, a table and a "Data source & method" section so you can verify it. Keep asking, as in a conversation.'), example: t('Chỉ lấy top 3 → Vẽ biểu đồ tròn', 'Only the top 3 → Draw a pie chart')},
];

type Fact = {value: string; label: Text};
const FACTS: Fact[] = [
  {value: '31.465', label: t('đơn hàng bán ra', 'sales orders')},
  {value: '121.317', label: t('dòng hàng trong các đơn', 'order lines')},
  {value: '504', label: t('sản phẩm', 'products')},
  {value: '10', label: t('khu vực bán hàng', 'sales territories')},
  {value: '72.591', label: t('lệnh sản xuất', 'work orders')},
  {value: '05/2022 – 06/2025', label: t('khoảng thời gian có dữ liệu', 'period covered')},
];

type Row = {name: Text; holds: Text; example: Text; tech: string};
const TABLES: Row[] = [
  {name: t('Đơn hàng', 'Sales orders'), holds: t('Mỗi dòng là một lần khách đặt hàng: ngày đặt, khu vực bán, tổng tiền hàng.', 'One row per customer order: order date, territory, merchandise total.'), example: t('Ví dụ minh họa: một đơn ngày 15/03/2024, khu vực Canada, tổng tiền hàng 5.400', 'Illustration: an order on 15 Mar 2024, Canada, merchandise total 5,400'), tech: 'sales.salesorderheader'},
  {name: t('Dòng hàng của đơn', 'Order lines'), holds: t('Chi tiết từng món trong một đơn: sản phẩm nào, bao nhiêu cái, giá bao nhiêu.', 'Each item inside an order: which product, how many, at what price.'), example: t('Ví dụ minh họa: một đơn có 3 dòng, mỗi dòng là một loại sản phẩm', 'Illustration: an order with 3 lines, one per product type'), tech: 'sales.salesorderdetail'},
  {name: t('Khu vực bán hàng', 'Sales territories'), holds: t('10 khu vực: Northwest, Northeast, Central, Southwest, Southeast, Canada, France, Germany, United Kingdom, Australia.', '10 territories: Northwest, Northeast, Central, Southwest, Southeast, Canada, France, Germany, United Kingdom, Australia.'), example: t('Bạn có thể gọi là "Đức", "Pháp", "Úc"', 'You can say "Germany", "France", "Australia"'), tech: 'sales.salesterritory'},
  {name: t('Sản phẩm, nhóm sản phẩm', 'Products and categories'), holds: t('504 sản phẩm thuộc 4 nhóm: Bikes (xe đạp), Components (linh kiện), Clothing (quần áo), Accessories (phụ kiện).', '504 products in 4 categories: Bikes, Components, Clothing, Accessories.'), example: t('HL Headset, LL Fork, Road-150 Red', 'HL Headset, LL Fork, Road-150 Red'), tech: 'production.product, productsubcategory, productcategory'},
  {name: t('Lệnh sản xuất', 'Work orders'), holds: t('Mỗi dòng là một đợt sản xuất: làm sản phẩm nào, đặt làm bao nhiêu, hỏng bao nhiêu, hạn giao và ngày xong thật.', 'One row per production run: which product, how many ordered, how many scrapped, the due date and the actual finish date.'), example: t('Đặt làm 1.872 cái LL Fork, hỏng 42 cái (tháng 5/2025)', 'Ordered 1,872 LL Fork, 42 scrapped (May 2025)'), tech: 'production.workorder'},
  {name: t('Công đoạn và dây chuyền', 'Routing steps and production lines'), holds: t('Lệnh sản xuất đi qua các công đoạn ở 14 địa điểm như Frame Welding, Paint, Subassembly, Final Assembly. Hệ thống tính theo công đoạn cuối cùng của mỗi lệnh; lệnh chưa có công đoạn ghi nhận nằm ở nhóm "Unassigned".', 'A work order passes through steps at 14 locations such as Frame Welding, Paint, Subassembly, Final Assembly. The system uses the last step of each order; orders with no recorded step fall under "Unassigned".'), example: t('Subassembly, Final Assembly, Unassigned', 'Subassembly, Final Assembly, Unassigned'), tech: 'production.workorderrouting, production.location'},
  {name: t('Nhà máy A, B, C', 'Factories A, B, C'), holds: t('AdventureWorks gốc không có nhà máy. Bản demo tự gán 14 địa điểm xoay vòng vào 3 nhà máy giả lập để bạn thử phân quyền theo nhà máy.', 'The original AdventureWorks has no factories. The demo assigns the 14 locations in rotation to 3 made-up factories so you can try factory-level access.'), example: t('Tool Crib → Factory A, Sheet Metal Racks → Factory B', 'Tool Crib → Factory A, Sheet Metal Racks → Factory B'), tech: 'acbi_demo.factory, acbi_demo.location_factory'},
];

type Metric = {name: Text; means: Text; how: Text; ask: Text; note?: Text};
const METRICS: Metric[] = [
  {name: t('Doanh thu', 'Revenue'), means: t('Tổng tiền hàng bán ra trong kỳ.', 'Total merchandise sold in the period.'), how: t('Cộng tổng tiền hàng của các đơn theo ngày đặt. Chưa gồm thuế và phí vận chuyển.', 'Adds up the merchandise total of orders by order date. Tax and shipping are not included.'), ask: t('Doanh thu tháng này là bao nhiêu?', 'What is revenue this month?'), note: t('Đơn vị là đơn vị tiền của dữ liệu gốc, không quy đổi.', 'The unit is the currency of the source data, not converted.')},
  {name: t('Tăng trưởng doanh thu', 'Revenue growth'), means: t('Doanh thu tăng hay giảm bao nhiêu phần trăm so với kỳ ngay trước.', 'How many percent revenue rose or fell against the period just before.'), how: t('(doanh thu kỳ này − doanh thu kỳ trước) ÷ doanh thu kỳ trước. Chỉ tính cho tháng hoặc quý gần nhất.', '(this period − previous period) ÷ previous period. Only for the latest month or quarter.'), ask: t('Tăng trưởng doanh thu quý trước so với quý liền trước', 'Revenue growth last quarter against the quarter before'), note: t('Muốn so sánh hai năm, quý hoặc tháng, hãy hỏi: “So sánh doanh thu năm 2023 và 2024”.', 'To compare two years, quarters or months ask: “Compare revenue 2023 and 2024”.')},
  {name: t('Sản lượng', 'Production output'), means: t('Số sản phẩm đạt yêu cầu đã làm xong.', 'Number of good units finished.'), how: t('Số đặt làm trừ số hỏng, tính theo ngày hoàn thành lệnh.', 'Units ordered minus units scrapped, by the date the order finished.'), ask: t('Sản lượng tháng trước theo nhà máy', 'Production output last month by factory')},
  {name: t('Tỷ lệ phế phẩm', 'Defect rate'), means: t('Trong 100 sản phẩm đặt làm thì bao nhiêu cái bị hỏng.', 'Out of every 100 units ordered, how many were scrapped.'), how: t('Số hỏng ÷ số đặt làm.', 'Scrapped units ÷ ordered units.'), ask: t('Tỷ lệ phế phẩm theo sản phẩm tháng trước, top 5', 'Defect rate by product last month, top 5')},
  {name: t('Tỷ lệ hoàn thành đúng hạn', 'On-time completion rate'), means: t('Bao nhiêu phần trăm lệnh sản xuất xong đúng hoặc trước hạn giao.', 'What share of work orders finished on or before the due date.'), how: t('Số lệnh xong trước hoặc đúng hạn ÷ tổng số lệnh.', 'Orders finished on or before due date ÷ all orders.'), ask: t('Tỷ lệ hoàn thành đúng hạn quý trước theo dây chuyền', 'On-time rate last quarter by production line')},
];

type Split = {by: Text; what: Text; works: Text};
const SPLITS: Split[] = [
  {by: t('Theo tháng, tuần, ngày', 'By month, week, day'), what: t('Xem diễn biến theo thời gian (biểu đồ đường).', 'See the trend over time (line chart).'), works: t('Tất cả chỉ số (tuần và ngày có giới hạn ở một số chỉ số)', 'All metrics (week and day are limited for some)')},
  {by: t('Theo khu vực', 'By territory'), what: t('So sánh giữa các nước và vùng.', 'Compare countries and regions.'), works: t('Doanh thu, tăng trưởng doanh thu', 'Revenue, revenue growth')},
  {by: t('Theo sản phẩm hoặc nhóm sản phẩm', 'By product or category'), what: t('Sản phẩm nào bán chạy, làm nhiều, hỏng nhiều.', 'Which products sell, get made or fail the most.'), works: t('Doanh thu, sản lượng, phế phẩm, đúng hạn', 'Revenue, output, defects, on-time')},
  {by: t('Theo nhà máy hoặc dây chuyền', 'By factory or production line'), what: t('So sánh các nơi sản xuất.', 'Compare places of production.'), works: t('Sản lượng, phế phẩm, đúng hạn', 'Output, defects, on-time')},
  {by: t('Theo lý do phế phẩm', 'By scrap reason'), what: t('Vì sao hàng bị hỏng, ví dụ "Gouge in metal", "Paint process failed".', 'Why units were scrapped, for example "Gouge in metal", "Paint process failed".'), works: t('Tỷ lệ phế phẩm', 'Defect rate')},
  {by: t('Theo tháng và khu vực cùng lúc', 'By month and territory together'), what: t('Biểu đồ cột chồng.', 'Stacked bar chart.'), works: t('Doanh thu, sản lượng', 'Revenue, output')},
];

type Tip = {title: Text; good: Text; bad?: Text};
const TIPS: Tip[] = [
  {title: t('Nói rõ chỉ số và thời gian', 'Name the metric and the time'), good: t('Doanh thu tháng trước của Canada', 'Revenue last month for Canada'), bad: t('Cho tôi xem số liệu (thiếu chỉ số, hệ thống sẽ hỏi lại)', 'Show me the numbers (no metric, the system will ask)')},
  {title: t('Dùng mốc thời gian quen thuộc', 'Use familiar time words'), good: t('tháng này, tháng trước, quý trước, năm 2024, Q1 2025, từ 2023-01-01 đến 2023-06-30', 'this month, last month, last quarter, 2024, Q1 2025, from 2023-01-01 to 2023-06-30')},
  {title: t('Hỏi nối tiếp', 'Follow up'), good: t('Doanh thu năm 2024 theo khu vực → Còn năm 2023? → Chỉ lấy top 3', 'Revenue 2024 by territory → What about 2023? → Only the top 3')},
  {title: t('Chọn biểu đồ bằng lời', 'Choose the chart in words'), good: t('Đổi sang biểu đồ tròn / vành khuyên / cột / đường / bảng', 'Switch to a pie / donut / bar / line chart / table')},
  {title: t('Dự báo', 'Forecast'), good: t('Dự báo doanh thu 6 tháng tới. Kết quả luôn ghi rõ là dự báo, kèm khoảng sai số.', 'Forecast revenue for the next 6 months. Results are always labelled as forecasts, with an error range.')},
  {title: t('Nhiều việc trong một câu', 'Several tasks in one message'), good: t('Hệ thống làm tất cả (tối đa 5 việc) và hiện từng kết quả theo thứ tự, có đánh số.', 'The system does every task (up to 5) and shows each result in order, numbered.')},
  {title: t('Hỏi về chính dữ liệu', 'Ask about the data itself'), good: t('Bạn báo cáo được những chỉ số nào? Dữ liệu có từ khi nào? Doanh thu tính thế nào?', 'Which metrics can you report? How far back does the data go? How is revenue calculated?')},
];

type Role = {who: string; sees: Text; cannot: Text};
const ROLES: Role[] = [
  {who: 'manager', sees: t('Doanh thu, tăng trưởng, sản lượng, phế phẩm, đúng hạn của mọi nhà máy và khu vực.', 'Revenue, growth, output, defects and on-time for every factory and territory.'), cannot: t('Trang quản trị tài khoản.', 'The account administration page.')},
  {who: 'sales', sees: t('Chỉ doanh thu và tăng trưởng doanh thu.', 'Revenue and revenue growth only.'), cannot: t('Sản lượng, phế phẩm, đúng hạn: hệ thống trả lời "ngoài quyền truy cập".', 'Output, defects, on-time: the system answers "outside your access scope".')},
  {who: 'production_a', sees: t('Sản lượng, phế phẩm, đúng hạn của Factory A.', 'Output, defects and on-time for Factory A.'), cannot: t('Doanh thu, và các nhà máy B, C.', 'Revenue, and factories B and C.')},
  {who: 'it_admin', sees: t('Trang quản trị: tạo tài khoản, xem nhật ký.', 'Administration page: create accounts, view the log.'), cannot: t('Mọi số liệu kinh doanh.', 'All business figures.')},
];

type Faq = {q: Text; a: Text};
const FAQS: Faq[] = [
  {q: t('"Tháng này" là tháng nào?', 'Which month is "this month"?'), a: t('Tính theo ngày dữ liệu (hiện ở thanh trên khung chat, hiện là 29/06/2025), không phải ngày hôm nay.', 'It counts from the data date (shown above the chat, currently 2025-06-29), not from today.')},
  {q: t('Sao báo "Không có dữ liệu trong kỳ đã chọn"?', 'Why "No data for the period"?'), a: t('Kỳ bạn hỏi nằm ngoài khoảng dữ liệu (05/2022 – 06/2025) hoặc không có bản ghi nào. Hệ thống không đoán số 0.', 'The period is outside the data (May 2022 – June 2025) or has no records. The system never invents a zero.')},
  {q: t('Sao hệ thống hỏi lại tôi?', 'Why does the system ask me a question?'), a: t('Câu hỏi thiếu chỉ số, thiếu thời gian hoặc nhắc tới thứ không có trong dữ liệu (ví dụ "Factory D"). Trả lời ngắn gọn là đủ.', 'The question lacks a metric or a time, or names something that is not in the data (for example "Factory D"). A short reply is enough.')},
  {q: t('Sao báo "Hệ thống AI đang quá tải"?', 'Why "The AI service is busy"?'), a: t('Dịch vụ AI miễn phí có giới hạn số lượt mỗi phút. Chờ khoảng một phút rồi gửi lại.', 'The free AI service has a per-minute limit. Wait about a minute and send again.')},
  {q: t('Tôi có tin được con số không?', 'Can I trust the numbers?'), a: t('Con số do câu lệnh dữ liệu đã được kiểm duyệt tính ra, không phải AI tự nghĩ. Mở "Nguồn dữ liệu & cách tính" để xem câu lệnh, khoảng thời gian và phiên bản định nghĩa.', 'Numbers come from vetted data queries, not from the AI. Open "Data source & method" to see the query, period and definition version.')},
  {q: t('Hệ thống không làm được gì?', 'What can it not do?'), a: t('Không giải thích nguyên nhân ("tại sao"), không chia theo quý, không trả lời về nhân sự, lương, khách hàng hay lợi nhuận, và không sửa dữ liệu (chỉ đọc).', 'It cannot explain reasons ("why"), split by quarter, or answer about staff, salaries, customers or profit, and it never changes data (read-only).')},
];

const flow: Text[][] = [
  [t('Khách đặt hàng', 'Customer orders'), t('Đơn hàng', 'Sales order'), t('Dòng hàng', 'Order lines'), t('Sản phẩm', 'Product'), t('Nhóm sản phẩm', 'Category')],
  [t('Khu vực bán hàng', 'Sales territory'), t('gắn với mỗi đơn hàng', 'attached to every order')],
  [t('Lệnh sản xuất', 'Work order'), t('Công đoạn', 'Routing step'), t('Địa điểm / dây chuyền', 'Location / line'), t('Nhà máy A, B, C', 'Factory A, B, C')],
];

export function Guide({language, onClose}: {language: 'vi' | 'en'; onClose: () => void}) {
  const vi = language === 'vi';
  const x = (v: Text) => (vi ? v.vi : v.en);
  return (
    <article className="guide-page" aria-label={vi ? 'Hướng dẫn sử dụng' : 'User guide'}>
      <div className="guide-inner">
        <button className="quiet guide-close" onClick={onClose}>{vi ? '← Quay lại' : '← Back'}</button>
        <span className="eyebrow">{vi ? 'HƯỚNG DẪN' : 'GUIDE'}</span>
        <h1>{vi ? 'Hiểu dữ liệu và hỏi đáp trong 10 phút' : 'Understand the data and ask questions in 10 minutes'}</h1>
        <p className="guide-lead">{vi
          ? 'ACBI là trợ lý cho phép bạn hỏi về doanh thu, sản xuất và chất lượng bằng lời thường. Trang này giải thích dữ liệu mẫu đứng sau nó và cách hỏi để có câu trả lời tốt, không cần biết lập trình.'
          : 'ACBI is an assistant that answers questions about revenue, production and quality in plain language. This page explains the sample data behind it and how to ask well. No programming needed.'}</p>

        <nav className="guide-toc" aria-label={vi ? 'Mục lục' : 'Contents'}>
          {[['start', vi ? 'Bắt đầu nhanh' : 'Quick start'], ['data', vi ? 'Dữ liệu mẫu là gì' : 'What the sample data is'], ['tables', vi ? 'Các "sổ" dữ liệu' : 'The data "ledgers"'], ['metrics', vi ? 'Năm chỉ số' : 'Five metrics'], ['splits', vi ? 'Chia nhỏ số liệu' : 'Breaking numbers down'], ['ask', vi ? 'Cách hỏi' : 'How to ask'], ['read', vi ? 'Đọc kết quả' : 'Reading results'], ['roles', vi ? 'Vai trò và quyền' : 'Roles and access'], ['faq', vi ? 'Hỏi nhanh' : 'FAQ']].map(([id, label]) => <a key={id} href={`#guide-${id}`}>{label}</a>)}
        </nav>

        <section id="guide-start"><h2>{vi ? 'Bắt đầu nhanh' : 'Quick start'}</h2>
          <div className="guide-cards">{STEPS.map(s => <div className="guide-card" key={s.title.en}><h3>{x(s.title)}</h3><p>{x(s.body)}</p>{s.example && <code>{x(s.example)}</code>}</div>)}</div>
        </section>

        <section id="guide-data"><h2>{vi ? 'Dữ liệu mẫu là gì?' : 'What is the sample data?'}</h2>
          <p>{vi
            ? 'Đó là AdventureWorks, bộ dữ liệu mẫu nổi tiếng của Microsoft về một công ty xe đạp tưởng tượng tên Adventure Works. Công ty vừa bán hàng ra nhiều nước vừa tự sản xuất. Hãy hình dung công ty giữ hai cuốn sổ lớn: một cuốn ghi mọi đơn bán hàng, một cuốn ghi mọi đợt sản xuất. Mỗi "bảng" trong cơ sở dữ liệu giống một trang tính Excel: hàng là từng sự việc, cột là thông tin về sự việc đó.'
            : 'It is AdventureWorks, Microsoft\'s well-known sample data about a fictional bicycle company called Adventure Works. The company sells to many countries and also manufactures. Picture two big ledgers: one records every sales order, the other every production run. Each "table" in the database is like an Excel sheet: rows are individual events, columns describe them.'}</p>
          <div className="guide-facts">{FACTS.map(f => <div key={f.label.en}><strong>{f.value}</strong><span>{x(f.label)}</span></div>)}</div>
          <p className="guide-note">{vi ? 'Ngày trong dữ liệu đã được dời để kết thúc vào 29/06/2025. Vì vậy "tháng này" nghĩa là tháng 6/2025. Hệ thống chỉ đọc dữ liệu, không bao giờ sửa.' : 'Dates in the data were shifted to end on 2025-06-29, so "this month" means June 2025. The system only reads the data and never changes it.'}</p>
        </section>

        <section id="guide-tables"><h2>{vi ? 'Các "sổ" dữ liệu, giải thích đời thường' : 'The data "ledgers" in everyday words'}</h2>
          <div className="guide-scroll"><table><thead><tr><th>{vi ? 'Cái gì' : 'What'}</th><th>{vi ? 'Chứa gì' : 'What it holds'}</th><th>{vi ? 'Ví dụ' : 'Example'}</th><th>{vi ? 'Tên kỹ thuật' : 'Technical name'}</th></tr></thead>
            <tbody>{TABLES.map(r => <tr key={r.tech}><td><strong>{x(r.name)}</strong></td><td>{x(r.holds)}</td><td>{x(r.example)}</td><td><small>{r.tech}</small></td></tr>)}</tbody></table></div>
          <h3>{vi ? 'Chúng liên quan với nhau thế nào' : 'How they connect'}</h3>
          <div className="guide-flow" role="img" aria-label={vi ? 'Sơ đồ liên kết các bảng' : 'Diagram of how tables connect'}>
            {flow.map((line, i) => <div className="guide-flow-line" key={i}><em>{i === 0 ? (vi ? 'Bán hàng' : 'Sales') : i === 1 ? (vi ? 'Khu vực' : 'Territory') : (vi ? 'Sản xuất' : 'Production')}</em>{line.map((n, j) => <React.Fragment key={j}><span>{x(n)}</span>{j < line.length - 1 && i !== 1 && <b>→</b>}</React.Fragment>)}</div>)}
          </div>
          <p className="guide-note">{vi ? 'Doanh thu đi theo nhánh Bán hàng. Sản lượng, phế phẩm và đúng hạn đi theo nhánh Sản xuất. Hai nhánh gặp nhau ở Sản phẩm.' : 'Revenue follows the Sales branch. Output, defects and on-time follow the Production branch. The two meet at Product.'}</p>
        </section>

        <section id="guide-metrics"><h2>{vi ? 'Năm chỉ số bạn có thể hỏi' : 'Five metrics you can ask about'}</h2>
          <div className="guide-cards">{METRICS.map(m => <div className="guide-card" key={m.name.en}><h3>{x(m.name)}</h3><p>{x(m.means)}</p><p><small><b>{vi ? 'Cách tính: ' : 'How: '}</b>{x(m.how)}</small></p><code>{x(m.ask)}</code>{m.note && <p className="guide-note">{x(m.note)}</p>}</div>)}</div>
        </section>

        <section id="guide-splits"><h2>{vi ? 'Chia nhỏ số liệu' : 'Breaking numbers down'}</h2>
          <p>{vi ? 'Thêm "theo …" vào câu hỏi để xem số liệu được chia thế nào.' : 'Add "by …" to a question to see how the figure divides.'}</p>
          <div className="guide-scroll"><table><thead><tr><th>{vi ? 'Chia' : 'Split'}</th><th>{vi ? 'Để làm gì' : 'What for'}</th><th>{vi ? 'Dùng được với' : 'Works with'}</th></tr></thead>
            <tbody>{SPLITS.map(s => <tr key={s.by.en}><td><strong>{x(s.by)}</strong></td><td>{x(s.what)}</td><td>{x(s.works)}</td></tr>)}</tbody></table></div>
        </section>

        <section id="guide-ask"><h2>{vi ? 'Cách hỏi để được câu trả lời tốt' : 'How to ask for a good answer'}</h2>
          <div className="guide-cards">{TIPS.map(tip => <div className="guide-card" key={tip.title.en}><h3>{x(tip.title)}</h3><code>{x(tip.good)}</code>{tip.bad && <p className="guide-note">✗ {x(tip.bad)}</p>}</div>)}</div>
        </section>

        <section id="guide-read"><h2>{vi ? 'Đọc một câu trả lời' : 'Reading an answer'}</h2>
          <ol className="guide-list">
            <li>{vi ? <><b>Câu tóm tắt</b>: kỳ đã dùng, con số chính, nhóm cao nhất và thấp nhất.</> : <><b>Summary line</b>: the period used, the main figure, the highest and lowest group.</>}</li>
            <li>{vi ? <><b>Biểu đồ</b>: thẻ số cho một giá trị, đường cho thời gian, tròn hoặc vành khuyên cho phần trăm của tổng, cột để so sánh, cột chồng cho hai chiều.</> : <><b>Chart</b>: a number card for one value, a line for time, pie or donut for shares of a whole, bars to compare, stacked bars for two splits.</>}</li>
            <li>{vi ? <><b>Bảng số</b>: mở "Xem bảng dữ liệu" để thấy từng dòng.</> : <><b>Table</b>: open "View data table" to see every row.</>}</li>
            <li>{vi ? <><b>Nguồn dữ liệu & cách tính</b>: câu lệnh đã chạy, khoảng ngày, phiên bản định nghĩa và giờ lấy dữ liệu.</> : <><b>Data source & method</b>: the query that ran, the date range, the definition version and retrieval time.</>}</li>
            <li>{vi ? <><b>Lịch sử</b>: mỗi kết quả được lưu ở thanh bên để mở lại.</> : <><b>History</b>: each result is saved in the sidebar so you can reopen it.</>}</li>
          </ol>
        </section>

        <section id="guide-roles"><h2>{vi ? 'Vai trò và quyền truy cập' : 'Roles and access'}</h2>
          <div className="guide-scroll"><table><thead><tr><th>{vi ? 'Tài khoản' : 'Account'}</th><th>{vi ? 'Thấy được' : 'Can see'}</th><th>{vi ? 'Không thấy' : 'Cannot see'}</th></tr></thead>
            <tbody>{ROLES.map(r => <tr key={r.who}><td><strong>{r.who}</strong></td><td>{x(r.sees)}</td><td>{x(r.cannot)}</td></tr>)}</tbody></table></div>
          <p className="guide-note">{vi ? 'Ở bản demo, các nút "Đăng nhập với …" trên trang đăng nhập cho bạn thử từng vai trò không cần mật khẩu.' : 'In the demo, the "Login as …" buttons on the sign-in page let you try each role without a password.'}</p>
        </section>

        <section id="guide-faq"><h2>{vi ? 'Hỏi nhanh' : 'FAQ'}</h2>
          {FAQS.map(f => <details key={f.q.en}><summary>{x(f.q)}</summary><p>{x(f.a)}</p></details>)}
        </section>
      </div>
    </article>
  );
}
