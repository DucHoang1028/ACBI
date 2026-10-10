import React, { useCallback, useEffect, useRef, useState } from 'react';
type Provider={id:string;label:string;keys:number;ready:number;state:'ready'|'resting';back_in_seconds:number|null};
export const AI_KEY='acbi_ai';
export function storedAi(){try{return localStorage.getItem(AI_KEY)||'auto';}catch{return 'auto';}}
export function AiPicker({access,vi,value,onChange}:{access:string;vi:boolean;value:string;onChange:(id:string)=>void}){
  const [list,setList]=useState<Provider[]>([]);
  const [at,setAt]=useState(Date.now());
  const [now,setNow]=useState(Date.now());
  const [checking,setChecking]=useState(false);
  const [note,setNote]=useState('');
  const known=useRef<Record<string,string>>({});
  const headers={Authorization:`Bearer ${access}`};
  const apply=useCallback((next:Provider[])=>{
    // A provider that was resting and answers again is shown the moment it does.
    const back=next.filter(p=>known.current[p.id]==='resting'&&p.state==='ready');
    if(back.length)setNote((vi?'Đã hồi: ':'Back: ')+back.map(p=>p.label).join(', '));
    known.current=Object.fromEntries(next.map(p=>[p.id,p.state]));
    setList(next);setAt(Date.now());
  },[vi]);
  const load=useCallback(async()=>{
    try{const r=await fetch('/api/llm/status',{headers});if(r.ok)apply((await r.json()).providers);}catch{/* keep the last list */}
  },[access,apply]);
  useEffect(()=>{void load();const t=setInterval(()=>{if(!document.hidden)void load();},15000);return ()=>clearInterval(t);},[load]);
  useEffect(()=>{const t=setInterval(()=>setNow(Date.now()),5000);return ()=>clearInterval(t);},[]);
  async function recheck(){
    setChecking(true);setNote('');
    try{
      const targets=list.filter(p=>p.state==='resting'&&(value==='auto'||p.id===value));
      for(const p of targets){
        const r=await fetch('/api/llm/check',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({provider:p.id})});
        if(r.ok)apply((await r.json()).providers);
        else if(r.status===429)setNote(vi?'Vừa kiểm tra xong, thử lại sau ít giây.':'Just checked; try again in a few seconds.');
      }
      if(!targets.length)await load();
    }catch{setNote(vi?'Không kiểm tra được.':'Could not check.');}
    finally{setChecking(false);}
  }
  const wait=(p:Provider)=>Math.max(0,(p.back_in_seconds??0)-Math.round((now-at)/1000));
  const text=(p:Provider)=>p.state==='ready'?(vi?'sẵn sàng':'ready'):(wait(p)>60?(vi?`nghỉ ~${Math.ceil(wait(p)/60)} phút`:`resting ~${Math.ceil(wait(p)/60)} min`):(vi?'sắp hồi':'back soon'));
  const chosen=list.find(p=>p.id===value);
  const shown=chosen?value:'auto';
  const resting=list.filter(p=>p.state==='resting');
  return <div className="ai-picker">
    <label>{vi?'AI trả lời':'AI'}
      <select value={shown} onChange={e=>{try{localStorage.setItem(AI_KEY,e.target.value);}catch{/* ignore */}setNote('');onChange(e.target.value);}}>
        <option value="auto">{vi?'Tự động (thử lần lượt)':'Automatic (try in turn)'}</option>
        {list.map(p=><option key={p.id} value={p.id} disabled={p.state==='resting'&&p.id!==value}>{p.label} · {text(p)}</option>)}
      </select>
    </label>
    {resting.length>0&&<button type="button" className="quiet" onClick={recheck} disabled={checking}>{checking?(vi?'Đang kiểm tra…':'Checking…'):(vi?'Kiểm tra lại':'Check again')}</button>}
    {chosen&&chosen.state==='resting'&&<small className="ai-warn" role="status">{vi?`${chosen.label} đang nghỉ, câu cần AI sẽ báo bận. Chọn Tự động hoặc kiểm tra lại.`:`${chosen.label} is resting; questions that need AI will report busy. Pick Automatic or check again.`}</small>}
    {note&&<small className="ai-back" role="status">{note}</small>}
  </div>;
}
