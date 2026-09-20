import React, { useRef, useState } from 'react';
import { ResultChart, type VizConfig } from './ResultChart';

type Source = {data_as_of: string; sql: string; parameters: Record<string, unknown>; metric_versions: Record<string, number>; retrieved_at?:string; references?:string[]};
type Answer = {status: string; message: string; answer_text: string | null; table: Record<string, unknown>[]; viz_config?:VizConfig|null; chart_fallback?:boolean;
  sources: Source | null; conversation_id: string; saved?:boolean};
type Saved = {id:string;conversation_id:string;question:string;metric_id:string;created_at:string};

export function Chat({access, enabled, advancedEnabled, anchor, language}:{access:string;enabled:boolean;advancedEnabled:boolean;anchor:string;language:'vi'|'en'}) {
  const [question,setQuestion]=useState('');
  const [conversationId,setConversationId]=useState<string|null>(null);
  const [answer,setAnswer]=useState<Answer|null>(null);
  const [busy,setBusy]=useState(false);
  const [recording,setRecording]=useState(false);
  const [voiceError,setVoiceError]=useState('');
  const [history,setHistory]=useState<Saved[]|null>(null);
  const [historyError,setHistoryError]=useState(false);
  const recorder=useRef<MediaRecorder|null>(null);
  const vi=language==='vi';
  const headers={Authorization:`Bearer ${access}`};

  async function ask(event:React.FormEvent) {
    event.preventDefault();if(!question.trim()||busy)return;
    setBusy(true);setAnswer(null);
    try {
      const response=await fetch('/api/chat/ask',{method:'POST',headers:{...headers,'Content-Type':'application/json'},
        body:JSON.stringify({question:question.trim(),conversation_id:conversationId})});
      if(response.status===401)throw new Error('session');
      const result:Answer=await response.json();
      if(!result.status||!Array.isArray(result.table))throw new Error('response');
      setAnswer(result);setConversationId(result.conversation_id);setQuestion('');
      if(result.saved)setHistory(null);
    } catch {
      setAnswer({status:'technical_failure',message:vi?'Không thể xử lý câu hỏi.':'Question could not be processed.',
        answer_text:null,table:[],sources:null,conversation_id:conversationId||''});
    } finally {setBusy(false);}
  }

  async function startRecording() {
    setVoiceError('');
    try {
      if(!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder)throw new Error();
      const stream=await navigator.mediaDevices.getUserMedia({audio:true});
      const chunks:BlobPart[]=[];
      const capture=new MediaRecorder(stream);
      recorder.current=capture;
      capture.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
      capture.onstop=async()=>{
        stream.getTracks().forEach(track=>track.stop());setRecording(false);
        const type=capture.mimeType.split(';')[0]||'audio/webm';
        const body=new FormData();body.append('file',new Blob(chunks,{type}),'recording');body.append('language',language);
        setBusy(true);
        try {
          const response=await fetch('/api/chat/voice',{method:'POST',headers,body});
          if(!response.ok)throw new Error();
          const result:{transcript:string}=await response.json();
          if(!result.transcript)throw new Error();
          setQuestion(result.transcript);
        } catch {setVoiceError(vi?'Không thể chép lời. Hãy ghi lại hoặc nhập câu hỏi.':'Transcription failed. Record again or type your question.');}
        finally {setBusy(false);}
      };
      capture.start();setRecording(true);
      setTimeout(()=>{if(capture.state==='recording')capture.stop();},60000);
    } catch {setVoiceError(vi?'Không thể dùng micrô. Hãy kiểm tra quyền truy cập hoặc nhập câu hỏi.':'Microphone unavailable. Check permission or type your question.');}
  }

  async function loadHistory() {
    setHistoryError(false);
    try {
      const response=await fetch('/api/conversations',{headers});if(!response.ok)throw new Error();
      const result:{conversations:Saved[]}=await response.json();setHistory(result.conversations);
    } catch {setHistoryError(true);}
  }
  async function openResult(item:Saved) {
    try {
      const response=await fetch(`/api/results/${encodeURIComponent(item.id)}`,{headers});if(!response.ok)throw new Error();
      const result:Answer=await response.json();setAnswer(result);setConversationId(item.conversation_id);
    } catch {setHistoryError(true);}
  }

  return <article className="question"><h2>{vi?'Đặt câu hỏi':'Ask a question'}</h2>
    {!enabled&&<p role="status">{vi?'Cần cấu hình GROQ_API_KEY để hỏi đáp trực tiếp.':'Set GROQ_API_KEY to enable live questions.'}</p>}
    {enabled&&!advancedEnabled&&<p role="status">{vi?'Phân tích nâng cao và biểu đồ AI chưa được bật.':'Advanced analysis and AI charts are unavailable.'}</p>}
    <form onSubmit={ask}><label htmlFor="question">{vi?'Câu hỏi của bạn':'Your question'}</label>
      <textarea id="question" required maxLength={1000} value={question} onChange={e=>setQuestion(e.target.value)}/>
      <div className="question-actions"><button type="submit" disabled={busy||!enabled}>{busy?(vi?'Đang xử lý…':'Working…'):(vi?'Gửi':'Send')}</button>
        {conversationId&&<button type="button" onClick={()=>{setConversationId(null);setAnswer(null);}}>{vi?'Câu hỏi mới':'New question'}</button>}</div></form>
    <div className="voice-actions"><button type="button" disabled={busy||!enabled} onClick={()=>recording?recorder.current?.stop():startRecording()}>
      {recording?(vi?'Dừng và chép lời':'Stop and transcribe'):(vi?'Ghi âm câu hỏi':'Record question')}</button>
      <small>{vi?'Âm thanh được gửi tới Groq để chép lời. Hãy kiểm tra và sửa văn bản trước khi gửi.':'Audio goes to Groq for transcription. Review and edit text before asking.'}</small></div>
    {voiceError&&<p role="alert">{voiceError}</p>}
    {answer&&<div className="answer" role="status"><p>{answer.message}</p>{answer.answer_text&&<p>{answer.answer_text}</p>}
      <ResultChart config={answer.viz_config||null} rows={answer.table} language={language}/>
      {answer.chart_fallback&&<p>{vi?'Không thể tạo biểu đồ phù hợp. Kết quả vẫn có trong bảng.':'A suitable chart could not be created. Results remain available in the table.'}</p>}
      {answer.table.length>0&&<div className="table-scroll"><table><thead><tr>{Object.keys(answer.table[0]).map(k=><th key={k}>{k}</th>)}</tr></thead>
        <tbody>{answer.table.map((row,i)=><tr key={i}>{Object.values(row).map((v,j)=><td key={j}>{String(v??'—')}</td>)}</tr>)}</tbody></table></div>}
      {answer.saved&&<p>{vi?'Kết quả đã lưu.':'Result saved.'}</p>}
      {answer.sources&&<details><summary>{vi?'Nguồn và định nghĩa':'Source and definition'}</summary>
        <p>{vi?'Ngày tham chiếu':'Reference date'}: {answer.sources.data_as_of||anchor}</p>
        <p>Metric versions: {JSON.stringify(answer.sources.metric_versions)}</p>
        {answer.sources.retrieved_at&&<p>{vi?'Thời gian truy xuất':'Retrieved at'}: {answer.sources.retrieved_at}</p>}
        {answer.sources.references?.length?<p>References: {answer.sources.references.join(', ')}</p>:null}
        <pre>{answer.sources.sql}</pre><pre>{JSON.stringify(answer.sources.parameters,null,2)}</pre></details>}</div>}
    <div className="history"><button type="button" onClick={loadHistory}>{vi?'Lịch sử kết quả':'Saved results'}</button>
      {historyError&&<p role="alert">{vi?'Không thể mở lịch sử.':'Could not load history.'}</p>}
      {history&&<ul>{history.length?history.map(item=><li key={item.id}><button type="button" onClick={()=>openResult(item)}>{item.question}</button><small>{new Date(item.created_at).toLocaleString(language)}</small></li>):<li>{vi?'Chưa có kết quả đã lưu.':'No saved results yet.'}</li>}</ul>}</div>
  </article>;
}
