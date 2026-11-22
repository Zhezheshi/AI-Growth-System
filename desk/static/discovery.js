import {mountDeskShell} from "./shell.js";

mountDeskShell("discovery");
const byId = id => document.getElementById(id);
const search = byId("search"), capture = byId("capture"), upload = byId("import");
const statusNote = text => { byId("status").textContent = text; };
const formObject = form => Object.fromEntries(new FormData(form));
let results = [], searchSequence = 0, currentLog = "";

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function link(label, url) {
  const a = node("a", label);
  a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
  return a;
}
async function api(path, method = "GET", body) {
  const res = await fetch(path, {method, headers: {"Content-Type":"application/json"}, body: body && JSON.stringify(body)});
  if (!res.ok) {
    let detail;
    try { detail = (await res.json()).detail; } catch { /* use status */ }
    throw new Error(typeof detail === "string" ? detail : "操作失败，请检查输入（" + res.status + "）");
  }
  return res.json();
}
function evidence(p) {
  const block = node("div", "", "proof");
  block.append(node("p", p.source_mode + " · 核查于 " + p.checked_at, "mini"));
  block.append(node("blockquote", p.evidence));
  if (p.summary) block.append(node("p", p.summary));
  if (p.products) block.append(node("p", "关联业务 · " + p.products));
  if (p.next_action) block.append(node("p", "建议首问 · " + p.next_action));
  const links = node("div", "", "source-links");
  links.append(link("查看证据原页 ↗", p.source_url));
  if (p.contact_url) links.append(link("公开联系入口 ↗", p.contact_url));
  else links.append(node("span","联系入口待查","mini"));
  block.append(links);
  return block;
}
function reviewForm(card, aiEnabled) {
  const form = node("form", "", "review");
  const select = node("select");
  for (const s of ["", "值得联系", "待核实", "不匹配"]) {
    const opt = node("option", s || "选择人工结论"); opt.value = s; select.append(opt);
  }
  select.required = true; select.value = card.decision;
  const roleLabel = node("label","人工判断"); roleLabel.append(select);
  const reason = node("input"); reason.required = true; reason.maxLength = 500;
  reason.value = card.reason; reason.placeholder = "核实了哪条证据，或还缺什么？";
  const notes = node("label","判定理由"); notes.append(reason);
  const save = node("button","保存判断","secondary");
  form.append(roleLabel, notes, save);
  form.onsubmit = async event => {
    event.preventDefault(); save.disabled = true;
    try {
      await api("/api/discovery/reviews/" + card.id, "PATCH", {decision:select.value, reason:reason.value});
      await load(); statusNote("已保存 " + card.company + " 的复核结论。");
    } catch (e) { statusNote(e.message); } finally { save.disabled = false; }
  };
  const section = node("div"); section.append(form);
  if (card.reviewed_at) section.append(node("p","最近复核 " + new Date(card.reviewed_at).toLocaleString(),"mini"));
  if (aiEnabled) {
    const button = node("button","用 AI 核查证据与建议首问","quiet");
    const answer = node("p","","ai-output"); section.append(button, answer);
    button.onclick = async () => {
      button.disabled = true; answer.textContent = "正在核查本卡证据…";
      try {
        const a = await api("/api/discovery/assist/" + card.id, "POST");
        const roles = {fleet:"车队",workshop:"维修企业",trade:"渠道商",unknown:"身份待核"};
        answer.textContent = a.supported
          ? ["AI 建议角色 · "+roles[a.role], a.summary, "原文 · " + a.evidence, "建议首问 · " + a.question].join("\n")
          : a.reason || "需要人工核实原文。";
      } catch (e) { answer.textContent = e.message; } finally { button.disabled = false; }
    };
  }
  return section;
}
const followupStatus = {new:"待跟进", following:"跟进中", done:"已完成"};
const beijingTime = value => new Date(value).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false});
function relatedInquiries(card) {
  const section = node("div");
  const last = card.last_followup;
  const count = card.inquiry_count || 0;
  if (!count) {
    section.append(node("p","尚未关联询盘","mini"));
    return section;
  }
  const summary = last ? [beijingTime(last.followed_at)+" 北京时间",last.owner || "待认领",
    followupStatus[last.status],last.next_action].filter(Boolean).join(" · ") : "暂无跟进记录";
  section.append(node("p","最近跟进 · "+summary));
  const details = node("details"), list = node("div");
  details.append(node("summary","相关询盘（"+count+"）"),list);
  details.ontoggle = async () => {
    if (!details.open) return;
    list.textContent = "正在读取…";
    try {
      const items = await api("/api/discovery/companies/"+card.id+"/inquiries");
      list.replaceChildren();
      for (const item of items) {
        list.append(node("p",[item.label,item.kind === "dm" ? "私信":"评论",item.sender,
          beijingTime(item.occurred_at)+" 北京时间"].join(" · "),"mini"),
          node("blockquote",item.body),node("p",[item.owner || "待认领",followupStatus[item.status],
            item.next_action,item.replied_at && "已回复 "+beijingTime(item.replied_at)].filter(Boolean).join(" · ")));
      }
      if (!items.length) list.textContent = "尚未关联询盘";
    } catch (error) { list.textContent = error.message; }
  };
  section.append(details);
  return section;
}
function draw(card, aiEnabled) {
  const article = node("article","","panel padded"), header = node("div","","row-head");
  header.append(node("h3", card.company), node("span", card.decision || "待人工复核", "tag cool"));
  article.append(header, node("p",[card.country,card.roles,card.history,card.match].join(" · ")));
  card.proofs.forEach(p=>article.append(evidence(p)));
  article.append(relatedInquiries(card));
  article.append(reviewForm(card, aiEnabled && card.proofs.length > 0)); return article;
}
async function load() {
  const seq = ++searchSequence;
  try {
    const data = await api("/api/discovery/search?" + new URLSearchParams(formObject(search)));
    if (seq !== searchSequence) return;
    results = data.items; currentLog = data.query;
    const cards = byId("cards"); cards.replaceChildren(...results.map(c=>draw(c,data.ai_enabled)));
    if (!results.length) cards.append(node("p", "本地资料暂未匹配。可点击「查找新买家」联网搜索。", "empty"));
    byId("count").textContent = "候选企业 · " + results.length;
    const top = results.filter(c=>!c.crm).slice(0,10);
    const checked = top.filter(c=>Boolean(c.decision)).length;
    const approved = top.filter(c=>c.decision==="值得联系").length;
    byId("review-count").textContent = top.length
      ? "当前筛选内前 "+top.length+" 家候选（剔除已知客户） · 已人工复核 "+checked+" 家 · 值得联系 "+approved+" 家。可导出清单固定本次送审范围。"
      : "";
    byId("web").href = data.search_url; byId("web").hidden = false;
    byId("search-log").textContent = "公开检索词 · " + data.query + "　检索于 " + new Date(data.searched_at).toLocaleString() +
       (data.ai_provider === "codex" ? "　AI 核查由本机 Codex CLI 调用。" : data.ai_enabled ? "" : "　当前按资料字段匹配；配置工作台 AI 后可逐条核查原文。");
  } catch (e) { statusNote(e.message); }
}
search.onsubmit = e=>{e.preventDefault();statusNote("");load();};
search.oninput = ()=>{byId("web").hidden = true;};
byId("open-capture").onclick = ()=>{
  const wrap=byId("capture-wrap"); wrap.open = true; wrap.scrollIntoView({block:"start"});
  capture.elements.country.value=search.elements.country.value; capture.elements.company.focus();
};
const localDate = new Date();
localDate.setMinutes(localDate.getMinutes()-localDate.getTimezoneOffset());
capture.elements.checked_at.value=localDate.toISOString().slice(0,10);
capture.onsubmit = async e=>{
  e.preventDefault(); const btn=capture.querySelector("button"); btn.disabled=true;
  try { await api("/api/discovery/capture","POST",formObject(capture)); await load(); statusNote("已保存证据。当前市场、品类和角色筛选会影响显示。"); }
  catch(err){statusNote(err.message);} finally{btn.disabled=false;}
};
upload.elements.kind.onchange = ()=>{byId("template").href = upload.elements.kind.value === "crm" ? "/examples/discovery-crm.csv" : "/examples/discovery.csv";};
upload.onsubmit = async e=>{
  e.preventDefault(); const file=upload.elements.file.files[0];
  if (!file || file.size>2000000){statusNote("请选择 2 MB 以内的 UTF-8 CSV。");return;}
  try{const r=await api("/api/discovery/import/"+upload.elements.kind.value,"POST",{content:await file.text()});await load();statusNote("已处理 "+r.processed+" 条名录记录。");}
  catch(err){statusNote(err.message);}
};
byId("export").onclick = ()=>{
  if (!results.length){statusNote("当前筛选没有可导出的企业。");return;}
  const fields=["本轮排序","检索词","企业","市场","角色","客户历史","产品线索","来源方式","证据原文","来源链接","联系入口","人工判断","复核原因","复核时间"];
  const lines=[fields,...results.map((c,i)=>[i+1,currentLog,c.company,c.country,c.roles,c.history,c.match,
    ...["source_mode","evidence","source_url","contact_url"].map(k=>c.proofs.map(p=>p[k]).filter(Boolean).join("\n")),
    c.decision,c.reason,c.reviewed_at])];
  const csv=lines.map(row=>row.map(value=>{
    let v=String(value); if (/^\s*[=+@\-]/.test(v)) v="'"+v;
    return '"'+v.replaceAll('"','""')+'"';
  }).join(",")).join("\r\n");
  const url=URL.createObjectURL(new Blob(["\ufeff"+csv],{type:"text/csv;charset=utf-8"}));
  const a=node("a");a.href=url;a.download="冠英买家复核表.csv";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
const initialFilters = new URLSearchParams(location.search);
search.elements.query.value = (initialFilters.get("query") || "").slice(0, 200);
if (initialFilters.get("country")) search.elements.country.value = initialFilters.get("country").slice(0,100);
if (initialFilters.get("product") === "disc") search.elements.product.value = "disc";
load();

const liveForm = byId("live");
let livePage=0, liveCity="";
async function searchLive(page) {
  const btn=liveForm.querySelector("button"), next=byId("live-next"), msg=byId("live-status");
  btn.disabled=true; next.disabled=true; msg.textContent="正在查询 Hino 官方名录…";
  const city=page===1 ? liveForm.elements.city.value.trim() : liveCity;
  try {
    const data=await api("/api/sources/hino?"+new URLSearchParams({city,page}));
    livePage=data.page; liveCity=city;
    const list=byId("live-results"); list.replaceChildren();
    for(const row of data.items) {
      const article=node("article","","result-card panel");
      article.append(node("h3",row.company),node("p",row.evidence),link("核对官方来源 ↗",row.source_url));
      const start=node("button","填入待核线索","quiet");
      start.onclick=()=>{
        capture.reset();
        for (const k of Object.keys(row)) {
          if (capture.elements.namedItem(k) && row[k]) capture.elements.namedItem(k).value=row[k];
        }
        capture.elements.checked_at.value=row.checked_at || data.checked_at;
        byId("capture-wrap").open=true; byId("capture-wrap").scrollIntoView({block:"start"});
        capture.elements.company.focus();
      };
      article.append(start); list.append(article);
    }
    msg.textContent=city+" · 第 "+data.page+" 页 · "+data.items.length+" 条 · 查询于 "+data.checked_at;
    next.hidden=!data.has_next;
  } catch(e) { msg.textContent=e.message; byId("live-results").replaceChildren(); next.hidden=true; }
  finally {btn.disabled=false;next.disabled=false;}
}
liveForm.onsubmit=e=>{e.preventDefault();searchLive(1);};
byId("live-next").onclick=()=>searchLive(livePage+1);

const findBuyers = byId("find-buyers"), historySelect = byId("research-history");
const researchStatus = byId("research-status"), researchResults = byId("research-results");
let cliReady = false, activeRun = false, launching = false, selectedRun = 0, poll;
const roleNames = {workshop:"维修企业", fleet:"车队", trade:"经销商或进口商", "":"全部角色"};
const categoryNames = ["新增", "重复", "待核实"];
const categoryStyles = {"新增":"tag ok", "重复":"tag cool", "待核实":"tag"};

function updateResearchButton() {
  historySelect.disabled = activeRun || launching;
  findBuyers.disabled = !cliReady || activeRun || launching;
  findBuyers.textContent = activeRun || launching ? "正在查找…" : "查找新买家";
}
function renderRun(run) {
  researchResults.replaceChildren();
  const terms = run.request;
  const scope = [terms.snapshot ? "随演示库载入的实测记录" : "", terms.country, terms.product === "drum" ? "鼓式刹车片" : "盘式刹车片",
    roleNames[terms.role], terms.query].filter(Boolean).join(" · ");
  if (run.status === "running") {
    researchStatus.textContent = scope + "。本机 Codex CLI 正在搜索公开网页并核对联系入口，离开页面后仍会继续。";
    return;
  }
  if (run.status === "failed") {
    researchStatus.textContent = scope + "。本轮未完成。"+run.error;
    return;
  }
  const counts = categoryNames.map(name => name+" "+run.results.filter(r=>r.category===name).length+" 家");
  researchStatus.textContent = scope+" ｜ "+counts.join(" · ")+" ｜ 联网操作 "+run.web_calls+" 次";
  if (!run.results.length) {
    researchResults.append(node("p","本轮没有找到匹配的公开线索，可调整市场或关键词再试。"));
    return;
  }
  const list = node("div","","run-list");
  for (const row of run.results) {
    const item = node("details"), title = node("summary");
    title.append(node("span",row.category,categoryStyles[row.category]), document.createTextNode(" "+row.company));
    item.append(title, node("p",row.summary || "推荐理由待补充"));
    if (row.evidence) item.append(node("blockquote",row.evidence));
    if (row.gap) item.append(node("p","待核实事项 · "+row.gap));
    if (row.next_action) item.append(node("p","建议首问 · "+row.next_action,"mini"));
    const links = node("div","","source-links");
    if (row.source_url) links.append(link("证据原页 ↗",row.source_url));
    if (row.contact_url) links.append(link("公开联系入口 ↗",row.contact_url));
    else links.append(node("span","联系入口待查","mini"));
    if (row.company_id) {
      const params = new URLSearchParams({country:terms.country, product:terms.product, query:row.card_name || row.company});
      const cardLink = node("a","查看企业卡");
      cardLink.href = "/static/discovery.html?"+params+"#cards"; links.append(cardLink);
    }
    item.append(links); list.append(item);
  }
  researchResults.append(list);
}
async function displayRun(id, refreshCards = false) {
  clearTimeout(poll);
  selectedRun = id;
  let retry = false;
  try {
    const run = await api("/api/discovery/runs/"+id);
    if (selectedRun !== id) return;
    renderRun(run);
    if (run.status === "running") {
      retry = true; activeRun = true;
    } else if (refreshCards) {
      activeRun = false;
      if (run.status === "done") await load();
    }
  } catch (e) {
    if (selectedRun !== id) return;
    retry = true; researchStatus.textContent = "暂时无法读取搜索进度，将自动重试。"+e.message;
  } finally {
    if (selectedRun === id) {
      updateResearchButton();
      if (retry) poll = setTimeout(()=>displayRun(id,true),3000);
    }
  }
}
async function researchHistory(preferred) {
  const data = await api("/api/discovery/runs"), runs = data.runs;
  cliReady = data.enabled;
  activeRun = runs.some(r=>r.status==="running");
  historySelect.replaceChildren(...runs.map(r=>{
    const option=node("option","#"+r.id+" · "+beijingTime(r.started)+" · "+r.request.country+" · "+roleNames[r.request.role]);
    option.value = r.id; return option;
  }));
  byId("run-picker").hidden = !runs.length;
  // Keep polling the active batch even if another tab started it.
  const id = runs.find(r=>r.status==="running")?.id || preferred || runs[0]?.id;
  if (id) {
    historySelect.value = id;
    await displayRun(id,activeRun);
  } else {
    researchStatus.textContent = cliReady ? "选择市场、产品和角色，点击「查找新买家」开始本机联网搜索。"
      : "本机联网搜索需要已登录的 Codex CLI。请使用 --codex 启动工作台。";
  }
  if (!cliReady) findBuyers.title = "请使用 --codex 启动工作台";
  updateResearchButton();
}
historySelect.onchange = ()=>displayRun(Number(historySelect.value));
findBuyers.onclick = async ()=>{
  if (launching || activeRun || !search.reportValidity()) return;
  launching = true; updateResearchButton();
  researchStatus.textContent = "正在启动本机 Codex CLI…";
  try {
    const {id} = await api("/api/discovery/runs","POST",formObject(search));
    await researchHistory(id);
  } catch (e) {
    try { await researchHistory(); } catch { /* show original error */ }
    if (!activeRun) researchStatus.textContent = e.message;
  } finally { launching=false; updateResearchButton(); }
};
researchHistory().catch(e=>{researchStatus.textContent=e.message;});
