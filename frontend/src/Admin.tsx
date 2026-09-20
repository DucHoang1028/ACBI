import React, { useState } from 'react';

type User = {id:number;username:string;role:string;enabled:boolean};
type Scope = {user_id:number;username:string;factories:number[]|'all'};
type Audit = {request_id:string;username:string;outcome:string;metric_id:string|null;created_at:string};
const roles=['manager','sales','production','it_admin'];

export function Admin({access,language}:{access:string;language:'vi'|'en'}) {
  const vi=language==='vi';
  const [users,setUsers]=useState<User[]>([]);
  const [scopes,setScopes]=useState<Scope[]>([]);
  const [audit,setAudit]=useState<Audit[]>([]);
  const [name,setName]=useState('');
  const [role,setRole]=useState('sales');
  const [password,setPassword]=useState('');
  const [error,setError]=useState('');
  const headers={Authorization:`Bearer ${access}`};
  async function fetchAll() {
    setError('');
    try {
      const [u,s,a]=await Promise.all(['users','scopes','audit'].map(path=>fetch(`/api/admin/${path}`,{headers}).then(r=>{if(!r.ok)throw new Error();return r.json();})));
      setUsers(u.users);setScopes(s.scopes);setAudit(a.audit);
    } catch {setError(vi?'Không thể tải trang quản trị.':'Could not load administration.');}
  }
  async function create(event:React.FormEvent) {
    event.preventDefault();setError('');setPassword('');
    try {
      const response=await fetch('/api/admin/users',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({username:name,role})});
      if(!response.ok)throw new Error();
      const result:{password:string}=await response.json();setPassword(result.password);setName('');await fetchAll();
    } catch {setError(vi?'Không thể tạo tài khoản. Kiểm tra tên tài khoản.':'Could not create account. Check username.');}
  }
  async function update(user:User,change:{role?:string;enabled?:boolean}) {
    setError('');
    try {
      const response=await fetch(`/api/admin/users/${user.id}`,{method:'PATCH',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(change)});
      if(!response.ok)throw new Error();await fetchAll();
    } catch {setError(vi?'Không thể cập nhật tài khoản.':'Could not update account.');}
  }
  return <article className="admin"><h2>{vi?'Quản trị':'Administration'}</h2>
    <button type="button" onClick={fetchAll}>{vi?'Tải người dùng và nhật ký':'Load users and audit'}</button>
    {error&&<p role="alert">{error}</p>}
    <form onSubmit={create}><h3>{vi?'Tạo tài khoản':'Create account'}</h3>
      <label>{vi?'Tên tài khoản':'Username'}<input required minLength={3} pattern="[a-z][a-z0-9_]*" value={name} onChange={e=>setName(e.target.value)}/></label>
      <label>{vi?'Vai trò':'Role'}<select value={role} onChange={e=>setRole(e.target.value)}>{roles.map(r=><option key={r}>{r}</option>)}</select></label>
      <button type="submit">{vi?'Tạo':'Create'}</button></form>
    {password&&<p role="status">{vi?'Mật khẩu ban đầu (chỉ hiển thị một lần)':'Initial password (shown once)'}: <code>{password}</code></p>}
    {users.length>0&&<div className="table-scroll"><h3>{vi?'Người dùng':'Users'}</h3><table><thead><tr><th>{vi?'Tên':'Username'}</th><th>{vi?'Vai trò':'Role'}</th><th>{vi?'Trạng thái':'Status'}</th></tr></thead>
      <tbody>{users.map(user=><tr key={user.id}><td>{user.username}</td><td><select aria-label={`${user.username} role`} value={user.role} onChange={e=>update(user,{role:e.target.value})}>{roles.map(r=><option key={r}>{r}</option>)}</select></td>
        <td><button type="button" onClick={()=>update(user,{enabled:!user.enabled})}>{user.enabled?(vi?'Tắt':'Disable'):(vi?'Bật':'Enable')}</button></td></tr>)}</tbody></table></div>}
    {scopes.length>0&&<details><summary>{vi?'Phạm vi':'Scopes'}</summary><ul>{scopes.map(s=><li key={s.user_id}>{s.username}: {s.factories==='all'?'all':s.factories.join(', ')||'—'}</li>)}</ul></details>}
    {audit.length>0&&<details><summary>{vi?'Nhật ký từ chối':'Denial audit'}</summary><ul>{audit.map(a=><li key={a.request_id}>{a.created_at} · {a.username} · {a.outcome} · {a.metric_id||'—'}</li>)}</ul></details>}
  </article>;
}
