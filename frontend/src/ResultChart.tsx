import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell, ScatterChart, Scatter,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import './charts.css';

export type VizConfig = {type:'bar'|'line'|'pie'|'donut'|'stacked_bar'|'scatter'|'table'|'kpi_card'; x:string|null; y:string[]; series:string|null};
const colors=['#176c80','#bd6a30','#7662a1','#3c8463','#a54765','#607a91','#947b2f','#525d78'];
const names:Record<string,[string,string]>={
  revenue:['Doanh thu','Revenue'], production_output:['Sản lượng','Production output'],
  defect_rate:['Tỷ lệ phế phẩm','Defect rate'], sales_growth:['Tăng trưởng doanh thu','Sales growth'],
  current_revenue:['Doanh thu kỳ này','Current revenue'],previous_revenue:['Doanh thu kỳ trước','Previous revenue'],
};

export function ResultChart({config,rows,language}:{config:VizConfig|null;rows:Record<string,unknown>[];language:'vi'|'en'}) {
  if(!config||config.type==='table'||!rows.length)return null;
  const label=(key:string)=>names[key]?.[language==='vi'?0:1]||key;
  const format=(value:number,key:string)=>new Intl.NumberFormat(language==='vi'?'vi-VN':'en-US',
    {style:['defect_rate','sales_growth'].includes(key)?'percent':'decimal',maximumFractionDigits:2}).format(value);
  const metric=config.y[0];
  if(config.type==='kpi_card')return <figure className="kpi-card"><figcaption>{label(metric)}</figcaption>
    <strong>{format(Number(rows[0][metric]),metric)}</strong>
    {metric==='revenue'&&<p>{language==='vi'?'Đơn vị tiền tệ của nguồn':'Source currency'}</p>}</figure>;
  if(!config.x)return null;
  const x=config.x;
  let data=rows.map(row=>{
    const value={...row};
    for(const key of config.y)value[key]=Number(row[key]);
    if(config.type==='scatter')value[x]=Number(row[x]);
    return value;
  });
  let series=config.y.map(key=>({key,name:label(key)}));
  if(config.type==='stacked_bar'&&config.series){
    const seriesColumn=config.series;
    const categories=[...new Set(rows.map(r=>String(r[seriesColumn])))];
    const grouped=new Map<string,Record<string,unknown>>();
    rows.forEach(row=>{
      const category=String(row[x]);
      const result=grouped.get(category)||{[x]:row[x]};
      result[`series_${categories.indexOf(String(row[seriesColumn]))}`]=Number(row[metric]);
      grouped.set(category,result);
    });
    data=[...grouped.values()];
    series=categories.map((name,i)=>({key:`series_${i}`,name}));
  }
  const axes=<><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey={x} type={config.type==='scatter'?'number':'category'} tick={{fontSize:12}}/>
    <YAxis dataKey={config.type==='scatter'?metric:undefined} type="number" tick={{fontSize:12}} width={76}/><Tooltip/><Legend/></>;
  return <figure className="result-chart" aria-label={label(metric)}><figcaption>{label(metric)}</figcaption>
    <ResponsiveContainer width="100%" height={320}>
      {config.type==='pie'||config.type==='donut'?<PieChart><Pie data={data} dataKey={metric} nameKey={x}
        innerRadius={config.type==='donut'?65:0} outerRadius={110} isAnimationActive={false}>
          {data.map((_,i)=><Cell key={i} fill={colors[i%colors.length]}/>)}</Pie><Tooltip/><Legend/></PieChart>
      :config.type==='line'?<LineChart data={data}>{axes}{series.map((s,i)=><Line key={s.key} type="linear" dataKey={s.key} name={s.name} stroke={colors[i%colors.length]} strokeWidth={2} isAnimationActive={false}/>)}</LineChart>
      :config.type==='scatter'?<ScatterChart>{axes}<Scatter data={data} fill={colors[0]} name={label(metric)} dataKey={metric} isAnimationActive={false}/></ScatterChart>
      :<BarChart data={data}>{axes}{series.map((s,i)=><Bar key={s.key} dataKey={s.key} name={s.name} fill={colors[i%colors.length]} stackId={config.type==='stacked_bar'?'values':undefined} isAnimationActive={false}/>)}</BarChart>}
    </ResponsiveContainer>
  </figure>;
}
