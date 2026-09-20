import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Chat } from './Chat';
import { Admin } from './Admin';
import './style.css';

type Readiness = {data_as_of: string; proposed_metric_count: number; approved_metric_count: number; llm_enabled: boolean; advanced_analysis_enabled:boolean};
type Identity = {username: string; role: string; factories: number[] | 'all'};
type Metric = {metric_id: string; name: string; version: number};
const labels = {
  vi: {title:'Trợ lý phân tích doanh nghiệp', phase:'Giai đoạn 4 · Kết quả và lịch sử', anchor:'Ngày tham chiếu dữ liệu',
    subtitle:'Mọi kỳ tương đối sẽ dựa trên ngày này.', description:'Đặt câu hỏi về các chỉ số trong phạm vi của bạn.',
    proposed:'Chỉ số đề xuất', approved:'Chỉ số đã phê duyệt', error:'Không thể kiểm tra kết nối.', loading:'Đang kiểm tra kết nối…',
    login:'Đăng nhập', username:'Tên tài khoản', password:'Mật khẩu', logout:'Đăng xuất', denied:'Tên tài khoản hoặc mật khẩu không đúng.',
    available:'Chỉ số trong phạm vi của bạn', none:'Không có chỉ số nghiệp vụ trong phạm vi này.', scope:'Phạm vi nhà máy', allFactories:'Tất cả'},
  en: {title:'Conversational business intelligence', phase:'Phase 4 · Results and history', anchor:'Data reference date',
    subtitle:'All relative periods will use this date.', description:'Ask about approved metrics within your access scope.',
    proposed:'Proposed metrics', approved:'Approved metrics', error:'Unable to check connectivity.', loading:'Checking connectivity…',
    login:'Sign in', username:'Username', password:'Password', logout:'Sign out', denied:'Incorrect username or password.',
    available:'Metrics in your scope', none:'No business metrics in this scope.', scope:'Factory scope', allFactories:'All'}
};

function App() {
  const [language,setLanguage] = useState<'vi'|'en'>('vi');
  const [data,setData] = useState<Readiness|null>(null);
  const [failed,setFailed] = useState(false);
  const [access,setAccess] = useState('');
  const [identity,setIdentity] = useState<Identity|null>(null);
  const [metrics,setMetrics] = useState<Metric[]>([]);
  const [username,setUsername] = useState('');
  const [password,setPassword] = useState('');
  const [loginError,setLoginError] = useState(false);
  const t = labels[language];
  useEffect(() => { document.documentElement.lang=language; }, [language]);
  useEffect(() => {
    fetch('/api/bootstrap').then(r=>{if(!r.ok) throw new Error(); return r.json();}).then(setData).catch(()=>setFailed(true));
    fetch('/api/auth/refresh',{method:'POST'}).then(r=>r.ok?r.json():null).then(r=>{if(r) setAccess(r.access_token);}).catch(()=>{});
  },[]);
  useEffect(() => {
    if(!access) {setIdentity(null);setMetrics([]);return;}
    const headers={Authorization:`Bearer ${access}`};
    fetch('/api/auth/me',{headers}).then(r=>{if(!r.ok) throw new Error();return r.json();}).then(setIdentity).catch(()=>setAccess(''));
    fetch('/api/metadata',{headers}).then(r=>r.ok?r.json():null).then(r=>setMetrics(r?.metrics ?? [])).catch(()=>setMetrics([]));
  },[access]);
  async function signIn(event: React.FormEvent) {
    event.preventDefault();setLoginError(false);
    try {
      const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password})});
      if(!response.ok) throw new Error();
      setAccess((await response.json()).access_token);setPassword('');
    } catch {setLoginError(true);}
  }
  async function signOut() {
    await fetch('/api/auth/logout',{method:'POST'}).catch(()=>{});
    setAccess('');setUsername('');setPassword('');
  }
  return <main>
    <header><a className="brand" href="/">ACBI<span> / AdventureWorks</span></a>
      <button onClick={()=>setLanguage(language==='vi'?'en':'vi')} aria-label="Change language">{language==='vi'?'English':'Tiếng Việt'}</button></header>
    <section><p className="eyebrow">{t.phase}</p><h1>{t.title}</h1><p className="intro">{t.description}</p>
      {identity?<article className="access"><div className="access-head"><strong>{identity.username} <small>({identity.role})</small></strong><button onClick={signOut}>{t.logout}</button></div>
        <p>{t.scope}: {identity.factories==='all'?t.allFactories:identity.factories.join(', ')||'—'}</p><h2>{t.available}</h2>
        {metrics.length?<ul>{metrics.map(m=><li key={m.metric_id}>{m.name} · v{m.version}</li>)}</ul>:<p>{t.none}</p>}
      </article>:<form className="access" onSubmit={signIn}><h2>{t.login}</h2>
        <label>{t.username}<input required autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)}/></label>
        <label>{t.password}<input required type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)}/></label>
        {loginError&&<p role="alert">{t.denied}</p>}<button type="submit">{t.login}</button>
      </form>}
      {identity&&identity.role!=='it_admin'&&<Chat key={access} access={access} enabled={!!data?.llm_enabled} advancedEnabled={!!data?.advanced_analysis_enabled} anchor={data?.data_as_of||''} language={language}/>}
      {identity?.role==='it_admin'&&<Admin access={access} language={language}/>}
      {failed?<p role="alert">{t.error}</p>:!data?<p role="status">{t.loading}</p>:<>
        <article><p>{t.anchor}</p><strong className="date">{data.data_as_of}</strong><p>{t.subtitle}</p></article>
        <div className="metrics"><article><strong>{data.proposed_metric_count}</strong><p>{t.proposed}</p></article><article><strong>{data.approved_metric_count}</strong><p>{t.approved}</p></article></div>
      </>}
    </section><footer>ACBI · Phase 4</footer>
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
