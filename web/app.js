'use strict';
const $ = selector => document.querySelector(selector);
const state = {token:'', view:'search', job:null, papers:[], favorites:[], selected:new Set(), limit:20, epoch:0, exportIds:[], busy:new Set()};
const journalState = {catalog:[], selected:new Set(), draft:new Set(), ready:false};
const SOURCE_NAMES = {openalex:'OpenAlex',crossref:'Crossref',arxiv:'arXiv',pubmed:'PubMed',semantic:'Semantic Scholar',europepmc:'Europe PMC'};
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeURL = value => { try {const u=new URL(value);return ['https:','http:'].includes(u.protocol)?u.href:'';}catch{return '';} };
const bookmark = '<svg viewBox="0 0 20 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M4 3h12v18l-6-4-6 4z" stroke-linejoin="round"/></svg>';
let toastTimer;
function toast(message, error=false) {const el=$('#toast');el.textContent=message;el.classList.toggle('error',error);el.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.hidden=true,error?6500:3300);}
async function api(path, payload) {
  const response = await fetch(path, payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-PaperDesk-Token':state.token},body:JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败，请稍后重试');
  return data;
}
function filteredPapers() {
  let items = state.papers.filter(p=>!$('#pdf-only').checked||p.pdf||p.localFile);
  if($('#sort').value==='recent')items.sort((a,b)=>(b.year||0)-(a.year||0)||(b.rank||0)-(a.rank||0));
  else if($('#sort').value==='cited')items.sort((a,b)=>(b.citations||0)-(a.citations||0));
  else if(state.view==='search')items.sort((a,b)=>(b.rank||0)-(a.rank||0));
  return items;
}
function card(p) {
  const isSelected=state.selected.has(p.id), saved=p.favorite;
  const paperURL=safeURL(p.url), title=escapeHTML(p.title);
  const authorNames=(p.authors||[]).slice(0,4).join(', ')+((p.authors||[]).length>4?' et al.':'');
  const canDownload=Boolean(p.pdf||p.localFile), busy=state.busy.has(p.id);
  return `<article class="paper-card${isSelected?' selected':''}" data-id="${escapeHTML(p.id)}">
    <div class="paper-top"><input class="paper-checkbox" type="checkbox" data-id="${p.id}" aria-label="选择 ${title}" ${isSelected?'checked':''}><span class="paper-year">${escapeHTML(p.year||'年份未收录')}</span>${(p.sources||[]).map(s=>`<span class="paper-source">${escapeHTML(s)}</span>`).join('')}${/review|综述/i.test(p.kind+' '+p.title)?'<span class="paper-kind">综述</span>':''}</div>
    <button class="bookmark-button${saved?' saved':''}" data-action="favorite" data-id="${p.id}" aria-label="${saved?'取消收藏':'收藏'} ${title}" aria-pressed="${Boolean(saved)}" title="${saved?'取消收藏':'收藏论文'}">${bookmark}</button>
    <h3 class="paper-title">${paperURL?`<a href="${escapeHTML(paperURL)}" target="_blank" rel="noopener noreferrer">${title}</a>`:title}</h3>
    <div class="paper-meta">${escapeHTML(authorNames||'作者未收录')}${p.venue?` <span> · </span> <span class="venue">${escapeHTML(p.venue)}</span>`:''}</div>
    <p class="paper-abstract collapsed${p.abstract?'':' empty'}">${escapeHTML(p.abstract||'此来源未提供摘要，可通过原文入口继续查看。')}</p>${p.abstract&&p.abstract.length>300?'<button class="abstract-toggle" data-action="abstract">展开摘要 ↓</button>':''}
    <div class="paper-footer"><div class="paper-signals"><span>${p.citations?`${Number(p.citations).toLocaleString()} 次引用`:'引用数据未收录'}</span>${p.doi?`<span title="${escapeHTML(p.doi)}">DOI</span>`:''}${p.pdf?'<span>开放 PDF</span>':''}</div><div class="paper-actions">${paperURL?`<a href="${escapeHTML(paperURL)}" target="_blank" rel="noopener noreferrer">原文入口 ↗</a>`:''}${canDownload?`<button class="download-button" data-action="download" data-id="${p.id}" ${busy?'disabled':''}>${busy?'正在下载…':'下载 PDF ↓'}</button>`:''}<button data-action="cite" data-id="${p.id}">引用</button></div></div>
  </article>`;
}
function renderPapers() {
  const items=filteredPapers();
  $('#result-count').textContent=String(items.length);
  const checked=items.filter(p=>state.selected.has(p.id)).length;
  $('#select-all').checked=items.length>0&&checked===items.length;
  $('#select-all').indeterminate=checked>0&&checked<items.length;
  $('#selection-count').textContent=state.selected.size?`已选 ${state.selected.size} 篇`:'选择论文';
  $('#load-more').hidden=items.length<=state.limit;
  if(items.length){$('#papers').innerHTML=items.slice(0,state.limit).map(card).join('');return;}
  if(state.view==='search'&&state.job?.status==='running'){
    $('#papers').innerHTML='<div class="loading-lines" aria-label="正在等待来源返回论文"><span></span><span></span><span></span><span></span></div><div class="loading-lines" aria-hidden="true"><span></span><span></span><span></span></div>';return;
  }
  const failed=state.view==='search'&&state.job?.status==='error';
  const title=state.view==='library'?'把有用的论文留在这里。':failed?'这次未能连接到数据源。':$('#pdf-only').checked?'当前结果没有开放 PDF 链接。':'暂时没有找到匹配的论文。';
  const help=state.view==='library'?'点击论文右上角的书签，即可加入收藏。':failed?'请检查网络或代理后重试。你也可以使用右侧的学术入口继续查找。':$('#pdf-only').checked?'取消“有开放 PDF”筛选后可查看全部题录，或从原文入口访问。':'试着缩短关键词、放宽年份，或换用英文主题词。';
  $('#papers').innerHTML=`<div class="results-empty"><h3>${title}</h3><p>${help}</p></div>`;
}
function showResults() {document.body.classList.add('has-results');$('#welcome').hidden=true;$('#results-panel').hidden=false;}
function updateJob(job) {
  state.job=job;state.papers=job.results||[];
  $('#result-eyebrow').textContent='DISCOVERY';
  $('#result-title').textContent=job.query;
  const activeSources=Object.values(job.sources).filter(s=>s.state!=='skipped');
  const done=activeSources.filter(s=>s.state!=='running').length;
  const total=activeSources.length;
  const success=Object.values(job.sources).filter(s=>s.state==='done').length;
  $('#result-subtitle').textContent=job.status==='running'?`正在检索 ${total} 个来源，已返回 ${done} 个。论文会陆续出现在这里。`:job.status==='interrupted'?'上次检索因服务关闭而中断，已返回的结果仍可使用；提交主题可重新检索。':`${success} 个来源已返回 · 按 DOI / 标题去重${job.elapsed?` · 用时 ${job.elapsed} 秒`:''}`;
  $('#source-progress').hidden=false;
  $('#source-progress').innerHTML=Object.entries(job.sources).map(([key,value])=>`<span class="source-pill ${escapeHTML(value.state)}" title="${escapeHTML(value.message||'')} 本来源返回 ${value.count} 篇"><i></i>${escapeHTML(SOURCE_NAMES[key]||key)}${value.state==='done'?` ${value.count}`:value.state==='error'?' 暂不可用':value.state==='skipped'?' 已跳过':' …'}</span>`).join('');
  const errors=Object.entries(job.sources).filter(([,v])=>v.state==='error');
  $('#source-errors').hidden=!errors.length;
  $('#source-error-list').innerHTML=errors.map(([key,v])=>`<p>${escapeHTML(SOURCE_NAMES[key])}：${escapeHTML(v.message)}</p>`).join('');
  const changed=job.searchQuery!==job.query;
  const journals=job.options.journals||[];
  $('#query-plan').hidden=!changed&&!job.options.yearFrom&&!job.options.review&&!journals.length;
  $('#query-plan').innerHTML=`<strong>本次检索词</strong>　${escapeHTML(job.searchQuery)}${job.options.yearFrom?`　·　${job.options.yearFrom} 年起`:''}${job.options.yearTo?`　至 ${job.options.yearTo} 年`:''}${changed?'　<span>按本地术语与规则转换，可直接修改搜索框中的词重新检索。</span>':''}${journals.length?`<div class="journal-applied"><strong>期刊范围（任意一本）</strong>　${journals.map(escapeHTML).join(' / ')}<br><span>已向支持期刊限制的来源提交条件，并按刊名 / ISSN 核验结果。arXiv 与 Semantic Scholar 本次跳过。</span></div>`:''}`;
  renderPapers();
}
async function poll(id, epoch) {
  let failures=0;
  while(epoch===state.epoch&&state.view==='search'){
    try {
      const job=await api('/api/search/'+encodeURIComponent(id));
      if(epoch!==state.epoch||state.view!=='search')return;
      failures=0;updateJob(job);
      if(job.status!=='running'){refreshHistory();return;}
    }catch(error){if(++failures>=3){toast(error.message+'。可在最近检索中重新打开任务。',true);return;}}
    await new Promise(resolve=>setTimeout(resolve,1100));
  }
}
async function search(event) {
  if(event)event.preventDefault();
  const query=$('#query').value.trim();if(!query){$('#query').focus();return;}
  const years=$('#years').value;
  const payload={query,review:$('#review').checked,journals:[...journalState.selected]};
  if(years==='custom'){
    if($('#year-from').value)payload.yearFrom=Number($('#year-from').value);
    if($('#year-to').value)payload.yearTo=Number($('#year-to').value);
  }else if(years!=='all')payload.yearFrom=new Date().getFullYear()-Number(years)+1;
  const button=$('#search-button');button.disabled=true;button.textContent='正在提交…';
  try{
    const job=await api('/api/search',payload);
    state.view='search';state.selected.clear();state.limit=20;state.epoch++;
    $('#pdf-only').checked=false;$('#nav-search').classList.add('active');$('#nav-library').classList.remove('active');
    $('#hero').hidden=false;showResults();updateJob(job);updateExternal(query);
    localStorage.setItem('paperdesk.lastRun',job.id);
    poll(job.id,state.epoch);refreshHistory();
  }catch(error){toast(error.message,true);}finally{button.disabled=false;button.innerHTML='开始检索 <span aria-hidden="true">↗</span>';}
}
async function refreshHistory() {
  try{
    const data=await api('/api/history');
    if(!data.items.length){$('#history-list').innerHTML='<p class="side-empty">从第一个研究问题开始。<br>检索记录会自动留在这里。</p>';return;}
    $('#history-list').innerHTML=data.items.slice(0,6).map(item=>{
      const d=new Date(item.createdAt);const date=`${d.getMonth()+1} 月 ${d.getDate()} 日`;
      return `<button class="history-item" data-run="${item.id}" title="${escapeHTML(item.query+(item.journals?.length?' · '+item.journals.join(' / '):''))}"><span class="history-icon">◷</span><div><strong>${escapeHTML(item.query)}</strong><small>${date} · ${item.status==='running'?'检索中':item.count+' 篇论文'}${item.journals?.length?` · ${item.journals.length} 本期刊`:''}</small></div><span>↗</span></button>`;
    }).join('');
  }catch(error){console.warn('History:',error.message);}
}
async function openRun(id) {
  try{
    const job=await api('/api/search/'+encodeURIComponent(id));
    state.view='search';state.epoch++;state.selected.clear();state.limit=20;
    $('#hero').hidden=false;$('#query').value=job.query;
    $('#nav-search').classList.add('active');$('#nav-library').classList.remove('active');$('#pdf-only').checked=false;
    $('#years').value=job.options.yearFrom||job.options.yearTo?'custom':'all';$('#custom-years').hidden=$('#years').value!=='custom';
    $('#year-from').value=job.options.yearFrom||'';$('#year-to').value=job.options.yearTo||'';$('#review').checked=Boolean(job.options.review);
    journalState.selected=new Set(job.options.journals||[]);renderJournalSelection();
    showResults();updateJob(job);updateExternal(job.query);localStorage.setItem('paperdesk.lastRun',id);
    if(job.status==='running')poll(id,state.epoch);
  }catch(error){toast(error.message,true);}
}
async function refreshCount() {try{const s=await api('/api/status');state.token=s.token;$('#favorite-count').textContent=s.favorites;}catch{}}
async function showLibrary() {
  try{
    const data=await api('/api/favorites');
    state.view='library';state.epoch++;state.papers=data.results;state.selected.clear();state.limit=20;
    $('#hero').hidden=true;showResults();$('#nav-library').classList.add('active');$('#nav-search').classList.remove('active');
    $('#result-eyebrow').textContent='YOUR READING COLLECTION';$('#result-title').textContent='我的收藏';
    $('#result-subtitle').textContent='值得阅读的论文，随时接着看。';
    $('#source-progress').hidden=true;$('#query-plan').hidden=true;$('#source-errors').hidden=true;$('#pdf-only').checked=false;renderPapers();
  }catch(error){toast(error.message,true);}
}
async function toggleFavorite(id) {
  const item=state.papers.find(p=>p.id===id);if(!item)return;
  try{
    const result=await api('/api/favorite',{id,favorite:!item.favorite});
    item.favorite=result.favorite;
    if(state.view==='library'&&!item.favorite){state.papers=state.papers.filter(p=>p.id!==id);state.selected.delete(id);}
    renderPapers();refreshCount();toast(result.favorite?'已加入收藏':'已取消收藏');
  }catch(error){toast(error.message,true);}
}
function saveFile(content, filename, type) {
  const link=document.createElement('a');const url=URL.createObjectURL(new Blob([content],{type}));
  link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1500);
}
function openExport(ids) {
  state.exportIds=ids;
  if(!ids.length){toast('先找到或收藏一些论文，再导出。');return;}
  $('#export-description').textContent=`将导出 ${ids.length} 篇论文。RIS 可直接导入 Zotero、EndNote。`;
  $('#export-dialog').showModal();
}
async function doExport(kind) {
  try{
    const data=await api('/api/export',{ids:state.exportIds,format:kind});
    saveFile(data.content,'豪哥论文-'+new Date().toISOString().slice(0,10)+'.'+(kind==='bibtex'?'bib':kind),'text/plain;charset=utf-8');
    $('#export-dialog').close();toast('文献题录已导出');
  }catch(error){toast(error.message,true);}
}
async function download(id) {
  state.busy.add(id);renderPapers();
  try{
    const data=await api('/api/download',{id});
    const item=state.papers.find(p=>p.id===id);if(item)item.localFile=data.file.split('/').pop();
    const response=await fetch(data.file);if(!response.ok)throw new Error('本地文件暂不可读，请重试');
    saveFile(await response.blob(),data.filename,'application/pdf');toast('PDF 已保存到资料库，并发送到浏览器下载');
  }catch(error){toast(error.message,true);}finally{state.busy.delete(id);renderPapers();}
}
function updateExternal(query='') {
  const term=query||$('#query').value.trim();const q=encodeURIComponent(term);
  const entries=[['Google Scholar',`https://scholar.google.com/scholar?q=${q}`],['百度学术',`https://xueshu.baidu.com/s?wd=${q}`],['知网','https://kns.cnki.net/kns8s/'],['万方',`https://s.wanfangdata.com.cn/paper?q=${q}`],['Web of Science','https://www.webofscience.com/wos/woscc/basic-search'],['Scopus','https://www.scopus.com/search/form.uri?display=basic'],['IEEE Xplore',`https://ieeexplore.ieee.org/search/searchresult.jsp?newsearch=true&queryText=${q}`],['ACM DL',`https://dl.acm.org/action/doSearch?AllField=${q}`]];
  $('#external-links').innerHTML=entries.map(([name,url])=>`<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${name}<span>↗</span></a>`).join('');
}

async function loadJournals() {
  if(journalState.ready)return;
  const data=await api('/api/journals');
  journalState.catalog=data.items;journalState.ready=true;
  $('#journal-category').innerHTML='<option value="">全部学科</option>'+data.categories.map(c=>`<option value="${escapeHTML(c)}">${escapeHTML(c)}</option>`).join('');
  $('#journal-catalog-note').textContent=`预置 ${data.items.length} 本中外期刊，覆盖 ${data.categories.length} 个学科类别。支持多选、缩写搜索和补充刊名。`;
}
function journalChoices() {
  const category=$('#journal-category').value, q=$('#journal-search').value.trim().toLocaleLowerCase();
  const catalog=[...journalState.catalog];
  const known=new Set(catalog.map(j=>j.title));
  for(const title of journalState.draft)if(!known.has(title))catalog.push({title,category:'自定义期刊',aliases:[]});
  return catalog.filter(j=>(!category||j.category===category)&&(!q||[j.title,j.category,...j.aliases,...(j.issns||[])].join(' ').toLocaleLowerCase().includes(q)));
}
function renderJournalDraft() {
  const choices=journalChoices();
  $('#journal-visible-count').textContent=`显示 ${choices.length} 本`;
  $('#journal-draft-count').textContent=`已选 ${journalState.draft.size} 本${journalState.draft.size?' · 任意一本匹配':' · 不限期刊'}`;
  $('#journal-select-visible').disabled=!choices.length;
  let last='';
  $('#journal-list').innerHTML=choices.length?choices.map(j=>{
    const heading=j.category!==last?`<div class="journal-group">${escapeHTML(j.category)}</div>`:'';last=j.category;
    return `${heading}<label class="journal-option"><input type="checkbox" value="${escapeHTML(j.title)}" ${journalState.draft.has(j.title)?'checked':''}><span><strong>${escapeHTML(j.title)}</strong>${j.aliases.length?`<small>${escapeHTML(j.aliases.slice(0,3).join(' · '))}</small>`:''}</span></label>`;
  }).join(''):'<p class="journal-no-match">目录中没有匹配项，可在下方添加期刊官方全称。</p>';
}
function renderJournalSelection() {
  const names=[...journalState.selected];
  $('#journal-summary').textContent=names.length?`已选 ${names.length} 本`:'不限期刊';
  $('#journal-open').classList.toggle('has-selection',Boolean(names.length));
  $('#journal-reset').hidden=!names.length;
  $('#journal-chips').hidden=!names.length;
  $('#journal-chips').innerHTML=names.slice(0,6).map(title=>`<button type="button" data-journal-remove="${escapeHTML(title)}" aria-label="移除期刊 ${escapeHTML(title)}">${escapeHTML(title)} <span aria-hidden="true">×</span></button>`).join('')+(names.length>6?`<button type="button" data-journal-more>另 ${names.length-6} 本…</button>`:'');
}
async function openJournalDialog() {
  try{
    await loadJournals();journalState.draft=new Set(journalState.selected);
    $('#journal-search').value='';$('#journal-category').value='';$('#journal-custom-name').value='';
    renderJournalDraft();$('#journal-dialog').showModal();$('#journal-search').focus();
  }catch(error){toast('期刊目录读取失败：'+error.message,true);}
}
$('#journal-open').addEventListener('click',openJournalDialog);
$('#journal-search').addEventListener('input',renderJournalDraft);
$('#journal-category').addEventListener('change',renderJournalDraft);
$('#journal-list').addEventListener('change',event=>{
  if(!event.target.matches('input[type=checkbox]'))return;
  if(event.target.checked)journalState.draft.add(event.target.value);else journalState.draft.delete(event.target.value);
  $('#journal-draft-count').textContent=`已选 ${journalState.draft.size} 本${journalState.draft.size?' · 任意一本匹配':' · 不限期刊'}`;
});
$('#journal-select-visible').addEventListener('click',()=>{for(const j of journalChoices())journalState.draft.add(j.title);renderJournalDraft();});
$('#journal-clear').addEventListener('click',()=>{journalState.draft.clear();renderJournalDraft();});
$('#journal-add').addEventListener('click',()=>{
  const input=$('#journal-custom-name'), title=input.value.trim();if(!title){input.focus();return;}
  const known=journalState.catalog.find(j=>[j.title,...j.aliases].some(n=>n.toLocaleLowerCase()===title.toLocaleLowerCase()));
  journalState.draft.add(known?.title||title);input.value='';$('#journal-search').value='';$('#journal-category').value='';renderJournalDraft();toast('已加入待选期刊，点击应用筛选保存');
});
$('#journal-custom-name').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();$('#journal-add').click();}});
$('#journal-apply').addEventListener('click',()=>{
  journalState.selected=new Set(journalState.draft);renderJournalSelection();$('#journal-dialog').close();
  if(state.view==='search'&&state.job)toast('期刊范围已更新，点击“开始检索”按新条件查找');
});
for(const id of ['#journal-cancel','#journal-dismiss'])$(id).addEventListener('click',()=>$('#journal-dialog').close());
$('#journal-dialog').addEventListener('click',event=>{if(event.target===$('#journal-dialog')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}});
$('#journal-reset').addEventListener('click',()=>{journalState.selected.clear();renderJournalSelection();if(state.job)toast('已清空期刊范围，点击“开始检索”重新查找');});
$('#journal-chips').addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;if(button.hasAttribute('data-journal-more'))openJournalDialog();else{journalState.selected.delete(button.dataset.journalRemove);renderJournalSelection();}});
$('#search-form').addEventListener('submit',search);
$('#query').addEventListener('input',()=>updateExternal());
$('#copy-query').addEventListener('click',async()=>{const query=$('#query').value.trim()||state.job?.query||'';if(!query){toast('先输入一个研究主题');return;}try{await navigator.clipboard.writeText(query);toast('检索词已复制');}catch{toast('请从搜索框手动复制检索词');}});
$('#years').addEventListener('change',()=>$('#custom-years').hidden=$('#years').value!=='custom');
$('.suggestions').addEventListener('click',event=>{const button=event.target.closest('[data-query]');if(button){$('#query').value=button.dataset.query;search();}});
$('#pdf-only').addEventListener('change',()=>{state.limit=20;renderPapers();});
$('#sort').addEventListener('change',renderPapers);
$('#select-all').addEventListener('change',event=>{for(const p of filteredPapers()){if(event.target.checked)state.selected.add(p.id);else state.selected.delete(p.id);}renderPapers();});
$('#load-more').addEventListener('click',()=>{state.limit+=20;renderPapers();});
$('#papers').addEventListener('change',event=>{const id=event.target.dataset.id;if(event.target.matches('.paper-checkbox')){if(event.target.checked)state.selected.add(id);else state.selected.delete(id);renderPapers();}});
$('#papers').addEventListener('click',event=>{const button=event.target.closest('[data-action]');if(!button)return;const id=button.dataset.id;
  if(button.dataset.action==='favorite')toggleFavorite(id);
  if(button.dataset.action==='cite')openExport([id]);
  if(button.dataset.action==='download')download(id);
  if(button.dataset.action==='abstract'){const abstract=button.previousElementSibling;abstract.classList.toggle('collapsed');button.textContent=abstract.classList.contains('collapsed')?'展开摘要 ↓':'收起摘要 ↑';}
});
$('#history-list').addEventListener('click',event=>{const button=event.target.closest('[data-run]');if(button)openRun(button.dataset.run);});
$('#nav-library').addEventListener('click',showLibrary);
$('#nav-search').addEventListener('click',()=>{state.view='search';state.epoch++;$('#hero').hidden=false;$('#nav-search').classList.add('active');$('#nav-library').classList.remove('active');state.selected.clear();if(state.job){showResults();updateJob(state.job);if(state.job.status==='running')poll(state.job.id,state.epoch);}else{document.body.classList.remove('has-results');$('#welcome').hidden=false;$('#results-panel').hidden=true;}$('#query').focus();});
$('#export-button').addEventListener('click',()=>openExport(state.selected.size?[...state.selected]:filteredPapers().map(p=>p.id)));
$('.export-options').addEventListener('click',event=>{const button=event.target.closest('[data-export]');if(button)doExport(button.dataset.export);});
$('#export-dialog').addEventListener('click',event=>{if(event.target===$('#export-dialog'))$('#export-dialog').close();});
async function init(){
  updateExternal();
  try{const status=await api('/api/status');state.token=status.token;$('#favorite-count').textContent=status.favorites;
    if(status.deployment==='server'){
      $('.local-badge').innerHTML='<i></i> 服务器工作空间';$('#history-storage-label').textContent='服务器保存';
      $('.workspace-note strong').textContent='多端访问同一份文献。';
      $('.workspace-note p').textContent='收藏、历史和下载文件保存在服务器。检索时向公开学术来源发送关键词。';
    }
    await refreshHistory();}
  catch(error){toast('无法连接本地服务，请使用启动脚本重新打开。',true);return;}
  // History is available in the sidebar; a fresh launch intentionally starts clean.
}
init();
