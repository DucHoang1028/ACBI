import React, { useEffect, useRef, useState } from 'react';
import { ResultChart, type VizConfig } from './ResultChart';
type Source={data_as_of:string;sql:string;parameters:Record<string,unknown>;metric_versions:Record<string,number>;retrieved_at?:string};
type Answer={status:string;message:string;answer_text:string|null;table:Record<string,unknown>[];viz_config?:VizConfig|null;chart_fallback?:boolean;sources:Source|null;conversation_id:string;saved?:boolean};
type Saved={id:string;conversation_id:string;question:string;created_at:string};
type Turn={question:string;answer:Answer|null};
const labels:Record<string,string>={revenue:'Doanh thu',sales_growth:'Tăng trưởng doanh thu',production_output:'Sản lượng',defect_rate:'Tỷ lệ phế phẩm',territory:'Khu vực',sales_territory:'Khu vực',month:'Tháng',product:'Sản phẩm',factory:'Nhà máy',year:'Năm',orders:'Số đơn hàng',sample_count:'Số bản ghi',current_revenue:'Doanh thu kỳ này',previous_revenue:'Doanh thu kỳ trước'};
export function Chat({access,enabled,advancedEnabled,anchor,language,role}:{access:string;enabled:boolean;advancedEnabled:boolean;anchor:string;language:'vi'|'en';role:string}){
  const vi=language==='vi';
  const [question,setQuestion]=useState('');
  const [conversationId,setConversationId]=useState<string|null>(null);
  const [turns,setTurns]=useState<Turn[]>([]);
  const [busy,setBusy]=useState(false);
  const [recording,setRecording]=useState(false);
  const [notice,setNotice]=useState('');
  const [history,setHistory]=useState<Saved[]>([]);
  const [historyError,setHistoryError]=useState(false);
  const recorder=useRef<MediaRecorder|null>(null);
  const bottom=useRef<HTMLDivElement|null>(null);
  const input=useRef<HTMLTextAreaElement|null>(null);
  const mounted=useRef(true);
  const headers={Authorization:`Bearer ${access}`};
  useEffect(()=>{mounted.current=true;return ()=>{mounted.current=false;if(recorder.current?.state==='recording')recorder.current.stop();};},[]);
  useEffect(()=>{bottom.current?.scrollIntoView({behavior:'smooth',block:'end'});},[turns,busy]);
  useEffect(()=>{void loadHistory();},[access]);
  async function loadHistory(){
    try{const r=await fetch('/api/conversations',{headers});if(!r.ok)throw Error();setHistory((await r.json()).conversations);setHistoryError(false);}catch{setHistoryError(true);}
  }
  function newChat(){if(busy||recording)return;setTurns([]);setConversationId(null);setQuestion('');setNotice('');input.current?.focus();}
  async function ask(event:React.FormEvent){
    event.preventDefault();const text=question.trim();if(!text||busy||recording)return;
    setTurns(previous=>[...previous,{question:text,answer:null}]);setQuestion('');setBusy(true);setNotice('');
    let result:Answer;
    try{
      const r=await fetch('/api/chat/ask',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({question:text,conversation_id:conversationId,language})});
      if(r.status===401)throw Error('session');
      result=await r.json();if(!result.status||!Array.isArray(result.table))throw Error();
      if(result.status!=='technical_failure')setConversationId(result.conversation_id);
      if(result.saved)void loadHistory();
    }catch(error){result={status:'technical_failure',message:error instanceof Error&&error.message==='session'?(vi?'Phiên đăng nhập hết hạn. Tải lại trang để tiếp tục.':'Session expired. Reload to continue.'):(vi?'Không thể xử lý lúc này. Hãy thử lại sau ít phút.':'Unable to process now. Please try again shortly.'),answer_text:null,table:[],sources:null,conversation_id:conversationId||''};}
    setTurns(previous=>previous.map((turn,i)=>i===previous.length-1?{...turn,answer:result}:turn));setBusy(false);input.current?.focus();
  }
  async function openResult(item:Saved){
    if(busy||recording)return;
    try{const r=await fetch(`/api/results/${encodeURIComponent(item.id)}`,{headers});if(!r.ok)throw Error();const answer:Answer=await r.json();setTurns([{question:item.question,answer}]);setConversationId(item.conversation_id);setQuestion('');setNotice(vi?'Đã mở kết quả đã lưu. Bạn có thể hỏi tiếp trong cuộc trò chuyện này.':'Saved result opened. You can continue this conversation.');}catch{setHistoryError(true);}
  }
  async function startRecording(){
    setNotice('');
    try{
      const stream=await navigator.mediaDevices.getUserMedia({audio:true});const chunks:BlobPart[]=[];const capture=new MediaRecorder(stream);recorder.current=capture;
      capture.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
      capture.onstop=async()=>{
        stream.getTracks().forEach(track=>track.stop());if(!mounted.current)return;setRecording(false);setBusy(true);
        const body=new FormData();body.append('file',new Blob(chunks,{type:capture.mimeType.split(';')[0]||'audio/webm'}),'recording');body.append('language',language);
        try{const r=await fetch('/api/chat/voice',{method:'POST',headers,body});if(!r.ok)throw Error();setQuestion((await r.json()).transcript);setNotice(vi?'Đã chép lời. Kiểm tra và sửa trước khi gửi.':'Transcript ready. Review before sending.');}catch{setNotice(vi?'Không thể chép lời. Hãy nhập câu hỏi hoặc ghi âm lại.':'Transcription failed. Type or record again.');}finally{setBusy(false);}
      };
      capture.start();setRecording(true);setTimeout(()=>{if(capture.state==='recording')capture.stop();},60000);
    }catch{setNotice(vi?'Không mở được micrô. Kiểm tra quyền truy cập.':'Microphone unavailable. Check permission.');}
  }
  const suggestions=role==='production'?(vi?['Sản lượng tháng này','Tỷ lệ phế phẩm quý trước']:['Production output this month','Defect rate last quarter']):(vi?['Doanh thu tháng này là bao nhiêu?','Doanh thu quý trước','Top 3 khu vực theo doanh thu tháng này']:['What is revenue this month?','Revenue last quarter','Top 3 territories by revenue this month']);
  function message(a:Answer){
    if(!vi)return a.message;
    const translated:Record<string,string>={ok:'',no_data:'Không có dữ liệu trong kỳ đã chọn.',denied:'Yêu cầu nằm ngoài quyền truy cập của bạn.',partial:'Đã có kết quả nhưng chưa lưu được.',technical_failure:'Không thể xử lý lúc này. Hãy thử lại sau ít phút.'};
    return a.status==='needs_clarification'?a.message:translated[a.status]??a.message;
  }
  function cell(value:unknown,key:string){
    if(value===null||value===undefined)return '—';
    if(['revenue','sales_growth','production_output','defect_rate','current_revenue','previous_revenue'].includes(key)&&Number.isFinite(Number(value)))return new Intl.NumberFormat(vi?'vi-VN':'en-US',{style:['defect_rate','sales_growth'].includes(key)?'percent':'decimal',maximumFractionDigits:2}).format(Number(value));
    return String(value);
  }
  return <div className="workspace"><aside className="sidebar"><button className="new-chat" onClick={newChat} disabled={busy||recording}><span>＋</span>{vi?'Cuộc trò chuyện mới':'New conversation'}</button><div className="sidebar-title">{vi?'KẾT QUẢ ĐÃ LƯU':'SAVED RESULTS'}<button className="icon-button" onClick={loadHistory} aria-label={vi?'Tải lại lịch sử':'Refresh history'}>↻</button></div>{historyError&&<p className="muted" role="alert">{vi?'Chưa tải được lịch sử.':'History unavailable.'}</p>}<nav className="history-list" aria-label={vi?'Lịch sử':'History'}>{history.map(item=><button key={item.id} className={conversationId===item.conversation_id?'selected':''} disabled={busy||recording} onClick={()=>openResult(item)} title={item.question}>{item.question}</button>)}{!history.length&&<p className="muted">{vi?'Kết quả phân tích của bạn sẽ xuất hiện ở đây.':'Your saved analyses will appear here.'}</p>}</nav><div className="sidebar-foot"><span className="status-dot"/>{vi?'AdventureWorks · Chỉ đọc':'AdventureWorks · Read only'}<small>{role==='production'?'Factory A':role==='sales'?(vi?'Phạm vi: bán hàng':'Scope: sales'):(vi?'Tất cả chỉ số được phê duyệt':'All approved metrics')}</small></div></aside>
    <section className="chat-stage"><div className="chat-toolbar"><strong>{vi?'Trợ lý phân tích':'Analytics assistant'}</strong><span>{vi?'Ngày dữ liệu':'Data as of'} <b>{anchor||'…'}</b></span></div>
      <div className="conversation" aria-live="polite">{!turns.length&&<div className="welcome"><div className="app-icon">A</div><h1>{vi?'Bạn muốn tìm hiểu điều gì?':'What would you like to explore?'}</h1><p>{vi?'Hỏi bằng ngôn ngữ tự nhiên. Nhận kết quả từ dữ liệu thực, kèm nguồn và cách tính.':'Ask naturally. Get results from actual data, with sources and calculations.'}</p><div className="suggestions">{suggestions.map(text=><button key={text} onClick={()=>{setQuestion(text);input.current?.focus();}}>{text}<span>↗</span></button>)}</div><p className="anchor-note">{vi?`“Tháng này” và “quý trước” được tính theo ngày dữ liệu ${anchor}, không phải ngày hiện tại.`:`Relative dates use ${anchor}, not today's date.`}</p></div>}
      {turns.map((turn,index)=><div className="turn" key={index}><div className="user-message">{turn.question}</div><div className="assistant-message"><div className="assistant-avatar">A</div><div className="answer-content">{!turn.answer?<p className="thinking">{vi?'Đang phân tích dữ liệu…':'Analyzing data…'}</p>:<>{message(turn.answer)&&<p className={turn.answer.status==='denied'?'error':''}>{message(turn.answer)}</p>}{turn.answer.answer_text&&<p className="answer-summary">{turn.answer.answer_text}</p>}<ResultChart config={turn.answer.viz_config||null} rows={turn.answer.table} language={language}/>{turn.answer.table.length>0&&<details className="result-details" open={turn.answer.table.length>1}><summary>{vi?'Xem bảng dữ liệu':'View data table'} · {turn.answer.table.length}</summary><div className="table-scroll"><table><thead><tr>{Object.keys(turn.answer.table[0]).map(k=><th key={k}>{vi?labels[k]||k:k}</th>)}</tr></thead><tbody>{turn.answer.table.map((row,i)=><tr key={i}>{Object.entries(row).map(([k,v])=><td key={k}>{cell(v,k)}</td>)}</tr>)}</tbody></table></div></details>}{turn.answer.sources&&<details className="source-details"><summary>{vi?'Nguồn dữ liệu & cách tính':'Sources & calculation'}</summary><p>{vi?'Ngày tham chiếu':'Data as of'}: {turn.answer.sources.data_as_of}</p><p>{vi?'Khoảng truy vấn (ngày kết thúc không bao gồm)':'Query range (end exclusive)'}: {String(turn.answer.sources.parameters.start)} — {String(turn.answer.sources.parameters.end)}</p><pre>{turn.answer.sources.sql}</pre><pre>{JSON.stringify(turn.answer.sources.parameters,null,2)}</pre></details>}{turn.answer.saved&&<small className="saved-label">✓ {vi?'Đã lưu kết quả':'Result saved'}</small>}</>}</div></div></div>)}<div ref={bottom}/></div>
      <div className="composer-wrap">{notice&&<p className="composer-notice" role="status">{notice}</p>}{!enabled&&<p role="status">{vi?'Dịch vụ hỏi đáp chưa sẵn sàng.':'Question service unavailable.'}</p>}<form className="composer" onSubmit={ask}><label className="sr-only" htmlFor="question">{vi?'Câu hỏi của bạn':'Your question'}</label><textarea ref={input} id="question" rows={2} maxLength={1000} value={question} onChange={e=>setQuestion(e.target.value)} placeholder={vi?'Hỏi về dữ liệu của bạn…':'Ask about your data…'} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();if(question.trim()&&!busy&&!recording&&enabled)e.currentTarget.form?.requestSubmit();}}}/><div className="composer-actions"><button type="button" className={recording?'recording':'quiet'} disabled={busy||!enabled} onClick={()=>recording?recorder.current?.stop():startRecording()}>{recording?(vi?'■ Dừng & chép lời':'■ Stop & transcribe'):(vi?'◉ Ghi âm':'◉ Record')}</button><button className="send-button" aria-label={vi?'Gửi câu hỏi':'Send question'} disabled={busy||!enabled||recording||!question.trim()} type="submit">↑</button></div></form><p className="composer-hint">{vi?'Enter để gửi · Shift + Enter xuống dòng. Âm thanh gửi tới Groq khi dừng ghi âm.':'Enter to send · Shift + Enter for a new line. Audio goes to Groq after recording.'}{!advancedEnabled&&' · '+(vi?'Chế độ phân tích cơ bản':'Basic analysis')}</p></div>
    </section></div>;
}
