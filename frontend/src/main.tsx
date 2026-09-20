import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Chat } from './Chat';
import { Admin } from './Admin';
import './style.css';

type Readiness = {data_as_of:string;llm_enabled:boolean;advanced_analysis_enabled:boolean};
type Identity = {username:string;role:string;factories:number[]|'all'};
function App() {
  const [language,setLanguage]=useState<'vi'|'en'>('vi');
  const [data,setData]=useState<Readiness|null>(null);
  const [access,setAccess]=useState('');
  const [identity,setIdentity]=useState<Identity|null>(null);
  const [username,setUsername]=useState('');
  const [password,setPassword]=useState('');
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const vi=language==='vi';
  useEffect(()=>{document.documentElement.lang=language;},[language]);
  useEffect(()=>{
    fetch('/api/bootstrap').then(r=>{if(!r.ok)throw Error();return r.json();}).then(setData).catch(()=>setError('Không thể kết nối máy chủ / Server unavailable'));
    fetch('/api/auth/refresh',{method:'POST'}).then(r=>r.ok?r.json():null).then(r=>{if(r)setAccess(r.access_token);}).catch(()=>{});
  },[]);
  useEffect(()=>{
    if(!access){setIdentity(null);return;}
    fetch('/api/auth/me',{headers:{Authorization:`Bearer ${access}`}}).then(r=>{if(!r.ok)throw Error();return r.json();}).then(setIdentity).catch(()=>setAccess(''));
    const timer=setInterval(()=>{
      fetch('/api/auth/refresh',{method:'POST'}).then(r=>{if(!r.ok)throw Error();return r.json();}).then(r=>setAccess(r.access_token)).catch(()=>{setAccess('');setError(vi?'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.':'Session expired. Please sign in again.');});
    },12*60*1000);
    return ()=>clearInterval(timer);
  },[access,vi]);
  async function signIn(event:React.FormEvent){
    event.preventDefault();setBusy(true);setError('');
    try{
      const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password})});
      if(!r.ok)throw Error();setAccess((await r.json()).access_token);setPassword('');
    }catch{setError(vi?'Không đăng nhập được. Kiểm tra tài khoản hoặc thử lại sau.':'Unable to sign in. Check your credentials or try later.');}
    finally{setBusy(false);}
  }
  async function signOut(){await fetch('/api/auth/logout',{method:'POST'}).catch(()=>{});setAccess('');setPassword('');}
  return <main className={identity?'app-shell':'login-shell'}>
    <header className="topbar"><a className="brand" href="/">ACBI<span>Workspace</span></a>
      <div className="topbar-actions">{identity&&<span className="user-badge">{identity.username}</span>}<button className="quiet" onClick={()=>setLanguage(vi?'en':'vi')}>{vi?'English':'Tiếng Việt'}</button>{identity&&<button className="quiet" onClick={signOut}>{vi?'Đăng xuất':'Sign out'}</button>}</div>
    </header>
    {!identity?<section className="login-view"><div className="login-intro"><span className="eyebrow">CONVERSATIONAL BUSINESS INTELLIGENCE</span><h1>{vi?'Dữ liệu của bạn.\nCâu trả lời rõ ràng.':'Your data.\nClear answers.'}</h1><p>{vi?'Khám phá doanh thu, sản xuất và chất lượng qua cuộc trò chuyện. Mỗi kết quả đều có nguồn để kiểm chứng.':'Explore revenue, production and quality through conversation. Every result has a source.'}</p><div className="feature-tags"><span>AdventureWorks</span><span>{vi?'Truy cập theo vai trò':'Role-based access'}</span><span>{vi?'Dữ liệu có kiểm chứng':'Traceable results'}</span></div></div>
      <form className="login-card" onSubmit={signIn}><div className="app-icon">A</div><h2>{vi?'Chào mừng trở lại':'Welcome back'}</h2><p>{vi?'Đăng nhập để bắt đầu phân tích.':'Sign in to start exploring.'}</p><label>{vi?'Tên tài khoản':'Username'}<input required autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)}/></label><label>{vi?'Mật khẩu':'Password'}<input required type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)}/></label>{error&&<p className="error" role="alert">{error}</p>}<button className="primary" disabled={busy}>{busy?(vi?'Đang đăng nhập…':'Signing in…'):(vi?'Đăng nhập':'Sign in')}</button></form></section>
      :identity.role==='it_admin'?<section className="admin-page"><h1>{vi?'Quản trị hệ thống':'Administration'}</h1><Admin access={access} language={language}/></section>
      :<Chat key={identity.username} access={access} enabled={!!data?.llm_enabled} advancedEnabled={!!data?.advanced_analysis_enabled} anchor={data?.data_as_of||''} language={language} role={identity.role}/>}
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
