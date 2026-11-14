import {jobs,reports} from '../api/entities.js';
import {download} from '../api/client.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
const bytes=n=>n>=1024*1024?(n/1024/1024).toFixed(1)+' MB':n>=1024?(n/1024).toFixed(1)+' KB':n+' B';
const integrity=d=>{
 if(d.ready)return '<span class="archive-state ok">✓ 明细与结论校验一致</span>';
 if(d.mode==='legacy')return '<span class="archive-state warn">旧版归档 · '+esc(d.integrity||'仅明细')+'</span>';
 return '<span class="archive-state bad">✕ '+esc(d.integrity||d.status)+'</span>';
};
const actions=d=>d.ready
 ? '<button class="text-button" data-download="'+d.id+'" data-format="csv">明细 CSV ↓</button><button class="text-button" data-download="'+d.id+'" data-format="json">结论 JSON ↓</button>'
 : d.mode==='legacy'
 ? '<button class="text-button" data-download="'+d.id+'" data-format="csv">旧明细 CSV ↓</button><span class="hint">旧版无结论文档，请新建计划</span>'
 : '<button class="text-button" data-repair="'+d.id+'">从当期快照修复 ↻</button>';
export async function reportPage(){
 const [list,deliveries,allJobs]=await Promise.all([reports.list(),reports.deliveries(),jobs.list()]);workspace.reports=list;
 const approved=allJobs.filter(j=>j.approved&&j.status==='completed');
 const ready=deliveries.filter(d=>d.ready).length;
 return heading('05 / DISTRIBUTION','定时分发与归档','每个计划的每个应发期次只公开一套明细 CSV 与完整结论 JSON，按期次快照冻结批准参数，并以 SHA256 指纹核对完整性。','<button class="button" id="refresh-reports">刷新收件箱 ↻</button>')+'<div class="report-grid"><section class="panel"><div class="section-title"><h2>创建分发计划</h2></div><form id="report-form"><label>简报名称<input name="name" required minlength="2" value="日间业务复核简报"></label><label>已批准分析<select name="job_id" required>'+approved.map(j=>'<option value="'+j.id+'">'+esc(j.name)+'</option>').join('')+'</select></label><label>业务接收组<input name="destination" value="业务复核组 · 本地收件箱" required></label><div class="parameter-grid"><label>分发周期<select name="interval_seconds"><option value="86400">每日</option><option value="604800">每周</option><option value="60">每分钟（验收）</option></select></label><label>首次执行<select name="delay_seconds"><option value="0">立即</option><option value="60">1 分钟后</option><option value="3600">1 小时后</option></select></label></div><button type="submit" class="primary" '+(session.user.role!=='admin'||!approved.length?'disabled':'')+'>保存分发计划</button></form><p class="hint">由后台 worker 按到期时间生成文件。仅负责人可创建或暂停；接收组为本地标签。同一期次只生成一套归档，中断后自动恢复。</p></section><section><div class="section-title"><h2>运行中的计划</h2><span>'+list.filter(r=>r.enabled).length+' 条已启用</span></div><div class="schedule-list">'+list.map(r=>'<article><div><strong>'+esc(r.name)+'</strong><p>'+esc(r.destination)+'</p><small>已发 '+(r.period||0)+' 期 · 下次 '+date(r.next_run)+' · 周期 '+r.interval_seconds+' 秒</small></div><button class="button small" data-toggle="'+r.id+'" '+(session.user.role!=='admin'?'disabled':'')+'>'+(r.enabled?'暂停':'启用')+'</button></article>').join('')+'</div><section class="panel inbox"><div class="section-title"><h2>本地收件箱</h2><span>'+ready+' / '+deliveries.length+' 份可下载</span></div>'+deliveries.map(d=>'<div class="delivery"><div><strong>'+esc(d.report_name||('计划 #'+d.report_id))+' · 第 '+(d.period??'—')+' 期</strong><p>'+esc(d.destination)+' · 归档于 '+date(d.completed_at||d.created_at)+' · 应发 '+date(d.due_at||d.created_at)+'</p>'+integrity(d)+(d.repair_count?'<span class="archive-state warn">已自动修复 '+d.repair_count+' 次'+(d.repaired_at?' · '+date(d.repaired_at):'')+'</span>':'')+'<small>明细 '+(d.csv_size?bytes(d.csv_size):'—')+' · 结论 '+(d.json_size?bytes(d.json_size):'—')+' · SHA256 '+esc((d.checksum||d.csv_checksum||'—').slice(0,24))+'…</small></div><div>'+actions(d)+'</div></div>').join('')+(!deliveries.length?'<p class="empty">计划执行后，文件会出现在此处。等待片刻后刷新。</p>':'')+'</section></section></div>';
}
export function bindReports(refresh){document.querySelector('#refresh-reports').onclick=refresh;submit(document.querySelector('#report-form'),async f=>{await reports.create({name:f.get('name'),job_id:Number(f.get('job_id')),destination:f.get('destination'),interval_seconds:Number(f.get('interval_seconds')),delay_seconds:Number(f.get('delay_seconds'))});notify('计划已保存，后台将按到期时间归档');await refresh();});document.querySelectorAll('[data-toggle]').forEach(b=>b.onclick=async()=>{try{await reports.toggle(b.dataset.toggle);await refresh();}catch(e){notify(e.message,true);}});document.querySelectorAll('[data-repair]').forEach(b=>b.onclick=async()=>{try{await reports.repair(b.dataset.repair);notify('已从当期批准快照重建归档');await refresh();}catch(e){notify(e.message,true);}});document.querySelectorAll('[data-download]').forEach(b=>b.onclick=()=>download(b.dataset.download,b.dataset.format).catch(e=>notify(e.message,true)));}
