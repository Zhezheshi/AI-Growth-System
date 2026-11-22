import {mountDeskShell} from "./shell.js";

mountDeskShell("home");
const $ = (selector, parent = document) => parent.querySelector(selector);
const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
const escape = value => String(value ?? "").replace(/[&<>"']/g, c => (
  {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]
));
const formData = form => Object.fromEntries(new FormData(form));
const date = value => value ? new Intl.DateTimeFormat("zh-CN", {
  month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false,timeZone:"Asia/Shanghai"
}).format(new Date(value)) : "";
const roleNames = {unknown:"待核实",end_user:"终端采购",trade:"贸易渠道"};
const statusNames = {new:"待跟进",following:"跟进中",done:"已完成"};
const sourceModes = {platform:"实接", social:"行业评论", relay:"上游转接", legacy:"导入", import:"导入", manual:"手工", demo:"演示"};
const buyerStages = {new:"待接触",invited:"已邀约",meeting:"已确认面谈",visited:"已面谈 / 参观",followup:"展后跟进",closed:"完成"};
const missingLabels = {products:"产品",fit:"适配复核",reports:"试验报告",prices:"价表",cases:"销售经验"};
const tag = (label, style = "") => `<span class="tag ${style}">${escape(label)}</span>`;
const options = (values, choice) => Object.entries(values).map(([key, value]) =>
  `<option value="${escape(key)}"${key === choice ? " selected" : ""}>${escape(value)}</option>`).join("");
const examples = {
  wva:{query:"WVA 19032",code:"19032",namespace:"WVA"},
  fmsi:{query:"FMSI 4707",code:"4707",namespace:"FMSI"},
  jh6:{query:"JH6 后桥换过，旧片编号 QT435SH10-3502572FW",code:"QT435SH10-3502572FW",position:"rear"},
  axle:{query:"当前车桥是联合440桥",axle:"联合440桥"},
  quote:{query:"DEMO-D01",code:"DEMO-D01",axle:"DEMO-AXLE",position:"rear",material:"DEMO-M1",
    qty:200,market:"Indonesia",currency:"USD",incoterm:"FOB"}
};
let info = {platforms:{}, sources:[]};
let accounts = [];
let inboxOffset = 0;
let lastDmCount = null;
let inboxVersion = 0;
let searchVersion = 0;
let exampleFields = {};
const inquiryAssessments = new Map();
let buyerVersion = 0;
let noticeTimer;

async function api(url, method = "GET", data) {
  const r = await fetch(url, {method, ...(data === undefined ? {} :
    {headers:{"Content-Type":"application/json"},body:JSON.stringify(data)})});
  let result;
  try { result = await r.json(); } catch { throw new Error("工作台暂时无法读取服务响应"); }
  if (!r.ok) throw new Error(typeof result.detail === "string" ? result.detail :
    result.detail?.map(item => `${item.loc.at(-1)} ${item.msg}`).join(" / ") || "操作未完成");
  return result;
}

function notify(message) {
  const el = $("#notice");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { el.hidden = true; }, 4500);
}

function run(work) {
  return Promise.resolve().then(work).catch(error => notify(error.message));
}

function respond(form, work) {
  form.addEventListener("submit", event => {
    event.preventDefault();
    const submit = event.submitter;
    if (submit) submit.disabled = true;
    run(() => work(formData(form))).finally(() => { if (submit) submit.disabled = false; });
  });
}

function openView(view, initial = false) {
  if (!initial) history.replaceState(null,"",view === "home" ? "/" : "/?desk=" + encodeURIComponent(view));
  $$(".view").forEach(section => { section.hidden = section.id !== "view-" + view; });
  $$("nav button").forEach(button => {
    if (button.dataset.view === view) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  window.scrollTo(0,0);
  if (view === "inbox" || view === "home") return run(refreshInbox);
  if (view === "fair") return run(refreshBuyers);
}

function source(name, page) {
  const label = escape(name) + (page ? ` · PDF 第 ${page} 页` : "");
  const document = info.sources.find(item => item.name === name);
  return document?.available ? `<a target="_blank" rel="noopener" href="/source/${document.id}${page ? "#page=" + page : ""}">${label}</a>` : label;
}

function resultCard(row) {
  const state = row.conflicts.length ? tag("存在冲突","bad") : row.fit ? tag("有适配复核记录","ok") :
    row.code_match ? tag("编码匹配","cool") : tag("目录候选","cool");
  const quote = row.quote;
  const draft = quote.ready ? [
    row.demo ? "DEMO ONLY" : "Subject to final sales confirmation",
    row.code, quote.price.market, quote.price.incoterm, `${quote.qty} sets`,
    `${quote.price.currency} ${quote.price.unit_price} per set`,
    `Subtotal ${quote.price.currency} ${quote.subtotal}`
  ].join(" · ") : "";
  return `<article class="panel result-card"><div class="row-head"><h3>${escape(row.name)}</h3>
    <div class="tags">${row.demo ? tag("演示数据") : ""}${state}</div></div>
    <dl class="specs"><div><dt>产品类别</dt><dd>${{drum:"鼓式刹车片",disc:"盘式刹车片",wind:"风电刹车片",shoe:"刹车蹄",clutch:"离合器面片"}[row.kind]}</dd></div>
    <div><dt>目录尺寸${row.dimensions.endsWith(" in") ? "" : " mm"}</dt><dd>${escape(row.dimensions || "待补充")}</dd></div>
    <div><dt>孔数</dt><dd>${row.holes ?? "—"}</dd></div>
    <div><dt>安装位置</dt><dd>${{front:"前",rear:"后",trailer:"挂车"}[row.position] || "待核对"}</dd></div></dl>
    <div class="tags">${row.refs.slice(0,10).map(ref => tag(ref.namespace + " " + ref.label +(ref.type === "family" ? " 系列" : ""),"cool")).join("")}</div>
    <p class="provenance">来源 ${source(row.source,row.page)}</p>
    ${row.hits.filter(ref => ref.source !== row.source || ref.page !== row.page).map(ref =>
      `<p class="provenance">${escape(ref.label)} 依据 ${source(ref.source,ref.page)}</p>`).join("")}
    ${row.conflicts.length ? `<p>${escape(row.conflicts.join("；"))}</p>` : ""}
    <details class="quote-details"${quote.ready ? " open" : ""}><summary>${quote.ready ? (row.demo ? "演示价及依据" : "历史参考价及依据") : "查看报价依据和缺项"}</summary>
      ${row.fit ? `<p>适配 ${escape(row.fit.record_id)} · ${source(row.fit.source,row.fit.page)} · ${escape(row.fit.evidence)}</p>` : ""}
      ${quote.report ? `<p>试验 ${escape(quote.report.record_id)} · ${source(quote.report.source,quote.report.page)} · ${escape(quote.report.evidence)}</p>` : ""}
      ${quote.ready ? `<p><strong>${escape(quote.price.currency)} ${escape(quote.price.unit_price)} / 套 × ${quote.qty} 套 = ${escape(quote.subtotal)}</strong></p>
        <p>价表 ${escape(quote.price.record_id)} · ${source(quote.price.source)} · ${escape(quote.price.incoterm)}
        · 价表有效期至 ${escape(quote.price.valid_to)}</p>
        <h4>${row.demo ? "演示报价草稿" : "客户报价草稿 · 待业务确认"}</h4>
        <p lang="en">${escape(draft)}</p>
        <button type="button" class="quiet" data-copy="${escape(draft)}">复制英文草稿</button>`
      : `<ul>${quote.actions.map(action =>
          `<li><strong>${escape(action.owner)}</strong> · ${escape(action.detail)}</li>`).join("")}</ul>`}
    </details>
    <div class="actions"><button type="button" class="quiet inspect" data-key="${escape(row.key)}">查看关联记录</button>
    ${row.conflicts.length ? "" : `<button type="button" class="quiet price-from-part" data-code="${escape(row.code)}">按订单核算</button>`}</div>
    <div class="evidence"></div></article>`;
}

function customerAnswer(answer) {
  const choices = answer.options.map(value =>
    `<button type="button" class="pill" data-measured="${escape(value)}" data-field="${escape(answer.field)}">${escape(value)}${answer.field === "holes" ? " 孔" : ""}</button>`
  ).join("");
  const fieldLabels = {axle:"车桥",position:"安装位置",material:"材料",holes:"孔数",dimensions:"尺寸"};
  const edit = fieldLabels[answer.field] ? `<button type="button" class="quiet" data-answer-field="${escape(answer.field)}">补填${fieldLabels[answer.field]}</button>` : "";
  return `<section class="panel result-card"><h3>现场答复</h3>
    <p>${escape(answer.zh)}</p><p lang="en">${escape(answer.en)}</p>
    <div class="actions"><button type="button" class="quiet" data-copy="${escape(answer.en)}">复制英文答复</button>${edit}</div>
    ${choices ? `<p class="mini">选客户实测值继续查询</p><div class="tags">${choices}</div>` : ""}
  </section>`;
}

async function search() {
  const version = ++searchVersion;
  const data = formData($("#search"));
  for (const key of ["qty","holes"]) data[key] = data[key] === "" ? null : Number(data[key]);
  $("#results").innerHTML = '<p class="empty">正在查找目录与企业记录…</p>';
  const result = await api("/api/search","POST",data);
  if (version !== searchVersion) return;
  const messages = {
    verified:"型号、当前车桥和材料已有匹配的复核记录。报价前核对业务条款。",
    candidate:"找到目录候选。请核对当前车桥、位置与样件，补齐材料和适配依据。",
    ambiguous:"有多条候选。请补充当前车桥铭牌、形状尺寸和孔数，再核对 A/B 位置或变体。",
    conflict:"输入与现有记录有冲突。请重新核对铭牌、旧片编码和测量尺寸。",
    missing:"暂未找到目录记录。请补充车桥铭牌、旧片编码或尺寸，记录后由技术同事复核。"
  };
  $("#results").innerHTML = `<div class="result-heading"><h2>查询结果</h2><span class="mini">${result.count} 条候选${result.total > 30 ? "，显示前 30 条记录" : ""}</span></div>
    <p class="result-info ${result.state === "verified" ? "good" : ""}">${messages[result.state]}</p>
    ${customerAnswer(result.answer)}
    ${result.items.map(resultCard).join("")}`;
}

async function openEvidence(button) {
  const slot = $(".evidence",button.closest("article"));
  if (slot.innerHTML) { slot.innerHTML = ""; return; }
  const details = await api("/api/knowledge/" + encodeURIComponent(button.dataset.key));
  const rows = Object.entries(details).flatMap(([kind, values]) => values.map(r =>
    `<p class="provenance">${escape(missingLabels[kind])} · ${escape(r.record_id)} · ${source(r.source,r.page)} ·
    ${r.verified ? "已复核" : "待复核"}${r.material ? " · " + escape(r.material) : ""}${r.evidence ? " · " + escape(r.evidence) : ""}</p>`));
  slot.innerHTML = rows.join("") || '<p class="mini">当前只有目录记录。</p>';
}

function useExample(name) {
  const form = $("#search");
  form.reset();
  exampleFields = {...examples[name]};
  Object.entries(examples[name]).forEach(([key,value]) => { form.elements[key].value = value; });
  $("#search-fields").open = name === "quote";
  return search();
}

async function refreshInfo() {
  info = await api("/api/info");
  $("#model-status").textContent = info.ai_provider === "codex" ? "本机 Codex CLI" : info.ai ? "AI 辅助可用" : "本地检索";
  $("#catalog-count").textContent = `${info.products} 条目录记录`;
  $("#quote-example").hidden = !info.demo;
  $("#model-extract").hidden = !info.ai;
  $("#fair-xlsx").hidden = !info.buyer_workbook;
  $("#sync-status").textContent = info.sync_sources.map(source => {
    const success = source.last_success ? date(source.last_success) + " 北京时间" : "暂无成功记录";
    return source.label + " · 最近成功读取 " + success +
      (source.error ? " · " + date(source.last_attempt) + " " + source.error : source.more ? " · 历史分页待续读" : "");
  }).join(" / ") || (info.legacy ? "已配置原获客来源，等待同步。" : "尚未记录主动同步。");
  $("#source-list").innerHTML = info.sources.map(doc => `<div class="source-row"><strong>${source(doc.name)}</strong>
    ${tag(doc.available ? "可查看原件" : "本机原件待绑定",doc.available ? "ok" : "")}</div>`).join("");
  $("#knowledge-stats").textContent = [`目录 ${info.products}`,...Object.entries(missingLabels).filter(([key])=>key!=="products")
    .map(([key,title])=>`${title} ${info.records[key] || 0}`)].join("  /  ");
}

const accountPlatform = $("#add-account [name=platform]");
const supportsCommentSearch = platform => ["xhs","douyin"].includes(platform);
function updateAccountHelp() {
  $("#ig-setup").hidden = accountPlatform.value !== "instagram";
  $("#social-setup").hidden = !supportsCommentSearch(accountPlatform.value);
}
accountPlatform.addEventListener("change", updateAccountHelp);

async function refreshAccounts() {
  accounts = await api("/api/accounts");
  const existing = $("#filter-platform").value;
  $("#filter-platform").innerHTML = options({"":"全部平台",...info.platforms},existing);
  const accountFilter = $("#filter-account");
  accountFilter.innerHTML = options({"":"全部账号",...Object.fromEntries(accounts.filter(a=> !existing || a.platform === existing)
    .map(a=>[a.id,a.label]))},accountFilter.value);
  $("#manual [name=account]").innerHTML = options(Object.fromEntries(accounts.map(a=>[a.id,a.label])));
  $("#import-inbox [name=account]").innerHTML = $("#manual [name=account]").innerHTML;
  const socialAccount = $("#industry-search [name=account]");
  socialAccount.innerHTML = options(Object.fromEntries(accounts.filter(a=>supportsCommentSearch(a.platform))
    .map(a=>[a.id,info.platforms[a.platform]+" · "+a.label])),socialAccount.value);
  accountPlatform.innerHTML = options(info.platforms, accountPlatform.value);
  updateAccountHelp();
  $("#channels").innerHTML = Object.entries(info.platforms).map(([id, name]) => {
    const matching = accounts.filter(a => a.platform === id);
    const state = {xhs:"行业评论 / 私信导入",douyin:"行业评论",x:"授权私信读取",tiktok:"事件转接 / 手工",
      youtube:"授权视频评论读取",instagram:"签名 Webhook",made_in_china:"人工登记 / 授权 CSV 导入"}[id];
    const entries = supportsCommentSearch(id) ? matching.map(a =>
      `<p class="actions"><span>${escape(a.label)}</span><button type="button" class="quiet" data-search-account="${escape(a.id)}">搜索评论</button></p>`).join("") :
      `<p>${matching.map(a=>escape(a.label)).join("、")}</p>`;
    return `<div class="channel-row"><div><strong>${escape(name)}</strong>
      ${matching.length ? entries : "<p>待添加账号</p>"}</div>${tag(state,"cool")}</div>`;
  }).join("");
}

function inquiryCard(item, companies) {
  const waiting = item.status !== "done" && !item.replied_at;
  const minutes = Math.max(0, Math.floor((Date.now() - Date.parse(item.occurred_at)) / 60000));
  const overdue = waiting && minutes >= 8*60;
  return `<article class="inquiry" data-id="${item.id}"><div class="row-head"><div><h3>${escape(item.sender)}</h3>
    <p class="mini">${escape(info.platforms[item.platform])} · ${escape(item.label)} · ${item.mode === "social" ? "采集于 " : ""}${date(item.occurred_at)} 北京时间</p></div>
    <div class="tags">${tag(item.kind === "dm" ? "私信":"评论","cool")}${overdue ? tag("超过 8 小时","bad") : ""}
    ${tag(sourceModes[item.mode] || item.mode)}</div></div>
    <p class="body">${escape(item.body)}</p>
    <p class="mini">系统建议 ${roleNames[item.suggested_role]}${item.intent === "buy" ? " · 有询价线索" : ""}
    ${item.evidence ? " · " + escape(item.evidence) : ""}</p>
    <div class="actions"><button class="secondary lookup">查型号</button>
      ${item.url ? `<a class="quiet" target="_blank" rel="noopener" href="${escape(item.url)}">打开来源</a>` : ""}
      ${info.ai ? '<button class="quiet assess">AI 辅助核查</button>' : ""}</div><div class="assessment" aria-live="polite">${escape(inquiryAssessments.get(item.id) || "")}</div>
    <p class="mini">${escape(item.owner || "待认领")} · ${escape(statusNames[item.status])} ·
      ${item.replied_at ? "实际回复 " + date(item.replied_at) + " 北京时间 · " + escape(item.replied_by) :
        waiting ? "已等待 " + Math.floor(minutes / 60) + " 小时 " + minutes % 60 + " 分钟" : "无实际回复登记"}</p>
    <form class="actions inquiry-form">
      <label>关联已有企业<select name="company_id">
        <option value="">未关联企业</option>
        ${companies.map(c=>`<option value="${c.id}"${c.id === item.company_id ? " selected" : ""}>${escape([c.company,c.country,c.domain].filter(Boolean).join(" · "))}</option>`).join("")}
      </select></label>
      <select name="role" aria-label="人工确认身份">${options(roleNames,item.role)}</select>
      <select name="status" aria-label="处理状态">${options(statusNames,item.status)}</select>
      <input name="owner" aria-label="负责人" placeholder="负责人" maxlength="80" value="${escape(item.owner)}">
      <input type="hidden" name="revision" value="${item.revision}">
      <input name="next_action" aria-label="下一步" placeholder="下一步跟进事项" maxlength="300" value="${escape(item.next_action)}">
      ${item.replied_at ? "" : '<label>负责人实际回复时间（设备本地时间）<input name="replied_at" type="datetime-local" step="1"></label>'}
      <button class="quiet">保存跟进</button>
    </form></article>`;
}

async function refreshInbox() {
  const version = ++inboxVersion;
  const params = new URLSearchParams({offset:inboxOffset});
  for (const key of ["platform","status","role","account","queue"]) params.set(key,$("#filter-" + key).value);
  const onHome = !$("#view-home").hidden;
  const result = await api(onHome ? "/api/inbox?queue=unanswered" : "/api/inbox?" + params);
  if (version !== inboxVersion) return;
  const counts = result.counts;
  $("#nav-count").textContent = counts.dm || "";
  $("#top-alert").textContent = counts.dm ? `${counts.dm} 条私信待处理` : "询盘提醒";
  $("#rail-pending").textContent = counts.pending;
  $("#rail-dm").textContent = counts.dm;
  $("#rail-lead").textContent = counts.direct ? `有 ${counts.direct} 条疑似或已确认终端采购线索待跟进。` : "已记录的私信和评论按账号归档。";
  if (lastDmCount !== null && counts.dm > lastDmCount) notify(`收到 ${counts.dm-lastDmCount} 条待处理私信`);
  lastDmCount = counts.dm;
  const stats = [["全部询盘",counts.total],["待认领",counts.unassigned],["待回复",counts.unanswered],
    ["终端采购线索",counts.direct]].map(([label,value])=>`<div><strong>${value}</strong><span>${label}</span></div>`).join("");
  $("#inbox-stats").innerHTML = stats;
  if (onHome) {
    $("#home-stats").innerHTML = stats;
    $("#home-inquiries").innerHTML = result.items.slice(0,3).map(item =>
      `<article class="inquiry"><h3><a href="/?desk=inbox&amp;queue=unanswered&amp;inquiry=${item.id}">${escape(item.sender)}</a></h3>
        <p class="body">${escape(item.body)}</p><p class="mini">${escape(info.platforms[item.platform])} · ${escape(item.owner || "待认领")}</p></article>`
    ).join("") || '<p class="inquiry">当前没有待回复的询盘。</p>';
    return;
  }
  // Leave the operator's input intact when a background refresh occurs.
  if ($("#inquiries").contains(document.activeElement) && /INPUT|SELECT|BUTTON/.test(document.activeElement.tagName)) return;
  const list = $("#inquiries");
  const editing = new Map($$("article[data-editing]",list).map(card=>[card.dataset.id,card]));
  const content = document.createDocumentFragment();
  for (const item of result.items) {
    const draft = editing.get(String(item.id));
    if (draft) {
      content.append(draft);
      continue;
    }
    const template = document.createElement("template");
    template.innerHTML = inquiryCard(item,result.companies);
    const card = template.content.firstElementChild;
    const form = $(".inquiry-form",card);
    form.oninput = form.onchange = () => { card.dataset.editing = "true"; };
    respond(form, async data => {
      data.company_id = data.company_id ? Number(data.company_id) : null;
      if (data.replied_at) data.replied_at = new Date(data.replied_at).toISOString();
      await api("/api/inbox/" + card.dataset.id,"PATCH",data);
      delete card.dataset.editing;
      document.activeElement?.blur();
      notify("已保存跟进");
      await refreshInbox();
    });
    content.append(card);
  }
  list.replaceChildren(content);
  if (!result.items.length) list.innerHTML = '<p class="empty">当前筛选没有询盘。</p>';
  $("#prev-inbox").hidden = !inboxOffset;
  $("#next-inbox").hidden = !result.more;
}

async function refreshBuyers() {
  const version = ++buyerVersion;
  const query = new URLSearchParams({query:$("#buyer-filter").value,grade:$("#buyer-grade").value});
  const list = await api("/api/buyers?" + query);
  if (version !== buyerVersion) return;
  $("#fair-file-note").textContent = list.length ? `当前筛选 ${list.length} 家。展开条目查看证据和跟进。` : "当前筛选没有买家，可导入公开线索或授权的海关数据。";
  $("#buyers").innerHTML = list.map(row => `<details class="buyer-detail"><summary>${tag(row.grade,"cool")} ${escape(row.company)} · ${escape(row.country)}</summary>
    <p>${escape(row.products)} · ${escape(row.attendance || "到场待核实")}</p><p>${escape(row.match)}</p>
    <p class="mini">${escape([row.business_contact,row.email,row.phone,row.whatsapp,row.telegram,row.wechat,row.next_action].filter(Boolean).join(" · "))}</p>
    <div class="actions">${[["业务",row.product_source],["联系",row.contact_source],["到场",row.attendance_source]].filter(([,href])=>href).map(([label,href])=>
      `<a class="quiet" href="${escape(href)}" target="_blank" rel="noopener">${label}来源</a>`).join("")}
      <button type="button" class="quiet prepare-invite" data-buyer="${escape(row.company)}" data-email="${escape(row.email)}">生成邀约</button>
    </div><form class="fields buyer-form" data-id="${escape(row.id)}">
      <label>跟进阶段<select name="stage">${options(buyerStages,row.stage)}</select></label>
      <label>联系人 / 下次行动<input name="notes" maxlength="3000" value="${escape(row.notes)}"></label><button class="quiet">保存跟进</button>
    </form></details>`).join("");
  $$(".buyer-form").forEach(form => respond(form,async data => {
    await api("/api/buyers/" + encodeURIComponent(form.dataset.id),"PATCH",data);
    notify("买家跟进已保存");
  }));
}

function invitation(row) {
  const subjects = {pre:"Meet Kooying at Automechanika Shanghai | Booth 1.1N59",
    show:"Confirming our meeting at Automechanika Shanghai | Booth 1.1N59",
    after:"Following up on heavy-duty braking parts | Kooying"};
  const opening = {
    pre:`Will your team be in Shanghai for Automechanika on 2–5 December 2026? We'd like to invite you to visit Kooying at booth 1.1N59 to discuss ${row.product}.`,
    show:`If you are at Automechanika Shanghai this week, could we confirm a time at booth 1.1N59 to discuss ${row.product}?`,
    after:`I'm following up after Automechanika Shanghai to discuss ${row.product}. If you're still in the area, we can also discuss a visit to our Huzhou factory.`
  };
  const body = `Hello ${row.buyer},\n\n${opening[row.stage]}\n\nIf useful, please share the reference numbers or axle applications your team buys. We'd be happy to discuss your requirements and arrange a factory tour in Huzhou, with pickup by appointment during the show. What time and business contact channel work best for you?\n\nBest regards,\nKooying Export Team\nwww.kooying.com`;
  const subject = subjects[row.stage];
  $("#draft").innerHTML = `<pre>${escape(subject)}\n\n${escape(body)}</pre><div class="actions">
    <a class="secondary" href="mailto:${encodeURIComponent(row.email)}?subject=${encodeURIComponent(subject)}&amp;body=${encodeURIComponent(body)}">在邮件客户端打开</a>
    <button class="quiet copy-quote" data-copy="${escape(subject+"\n\n"+body)}">复制邮件草稿</button></div>`;
}

const quoteForm = $("#custom-quote-form");
const attachmentSection = $("#attachment-section");
const attachmentLines = $("#attachment-lines");
let quoteRevision = 0;

function clearCustomQuote() {
  quoteRevision += 1;
  $("#custom-quote-result").replaceChildren();
}

function addAttachment() {
  if (attachmentLines.children.length >= 30) {
    notify("附件最多填 30 项");
    return;
  }
  attachmentLines.insertAdjacentHTML("beforeend", `<fieldset class="attachment fields">
    <legend>附件</legend>
    <label>名称<input name="name" placeholder="例：铆钉" required maxlength="80"></label>
    <label>材质<input name="material" placeholder="例：铜 / 铁 / 铝" required maxlength="80"></label>
    <label>单套件数<input name="units_per_set" type="number" min="1" max="10000" required></label>
    <label>每件成本<input name="cost_per_piece" type="number" min="0" step="0.0001" required></label>
    <button type="button" class="quiet remove-attachment">移除该附件</button>
  </fieldset>`);
  clearCustomQuote();
}

function toggleAttachments() {
  const enabled = quoteForm.elements.includes_attachments.value === "yes";
  attachmentSection.hidden = !enabled;
  if (enabled && !attachmentLines.children.length) addAttachment();
  $$(".attachment",attachmentLines).forEach(fieldset => { fieldset.disabled = !enabled; });
}

function fillCustomQuote(code) {
  quoteForm.reset();
  attachmentLines.replaceChildren();
  toggleAttachments();
  const lookup = formData($("#search"));
  for (const key of ["axle","position","material","qty","market","currency","incoterm"]) {
    if (lookup[key]) quoteForm.elements.namedItem(key).value = lookup[key];
  }
  quoteForm.elements.namedItem("product").value = code;
  clearCustomQuote();
  $("#custom-quote").open = true;
  $("#custom-quote").scrollIntoView({block:"start"});
  quoteForm.elements.namedItem("product").focus({preventScroll:true});
}

async function calculateCustomQuote(data) {
  const revision = ++quoteRevision;
  // The browser submits input decimals as strings to retain the operator's precision.
  const fields = ["product","axle","position","material","thickness","market","standard","currency","incoterm",
    "delivery_point","qty","kg_per_set","material_cost_per_kg","other_cost_per_set","trade_cost_total","source"];
  const payload = Object.fromEntries(fields.map(key => [key,quoteForm.elements.namedItem(key).value.trim()]));
  payload.currency = payload.currency.toUpperCase();
  payload.proposed_per_set = data.proposed_per_set || null;
  payload.includes_attachments = data.includes_attachments === "yes";
  payload.attachments = payload.includes_attachments ? $$(".attachment",attachmentLines).map(fieldset =>
    Object.fromEntries($$("input",fieldset).map(input => [input.name,input.value]))) : [];
  const result = await api("/api/quotes/calculate","POST",payload);
  if (revision !== quoteRevision) return;
  const money = value => `${escape(result.currency)} ${escape(value)}`;
  $("#custom-quote-result").innerHTML = `<div class="result-info"><div class="tags">${tag("内部核算稿")}
    ${result.product.demo ? tag("演示数据") : ""}</div>
    <p><strong>${escape(result.product.code)} · ${escape(result.terms)} · ${result.qty} 套</strong></p>
    <p>配方材料 ${money(result.material_per_set)} / 套。附件 ${money(result.accessories_per_set)} / 套。
    其他 ${money(result.other_per_set)} / 套。整单贸易费用 ${money(result.trade_cost_total)}。</p>
    ${result.attachments.map(a=>`<p class="mini">${escape(a.name)}（${escape(a.material)}）× ${a.units_per_set} · ${money(a.cost_per_set)} / 套</p>`).join("")}
    <p><strong>整单核算成本 ${money(result.total_cost)}</strong></p>
    ${result.proposed_total === null ? "" : `<p>拟议整单金额 ${money(result.proposed_total)}。与核算成本差额 ${money(result.difference)}。</p>`}
    <p class="mini">成本来源 ${escape(result.source)}</p>
    ${result.fit ? `<p class="mini">适配历史 ${source(result.fit.source,result.fit.page)} · ${escape(result.fit.record_id)}</p>` : ""}
    ${result.report ? `<p class="mini">试验历史 ${source(result.report.source,result.report.page)} · ${escape(result.report.record_id)}</p>` : ""}
    <ul>${result.reviews.map(item=>`<li>${escape(item)}</li>`).join("")}</ul></div>
    <div class="result-card"><h3>客户答复草稿</h3><p lang="en">${escape(result.customer_reply)}</p>
    <button type="button" class="quiet" data-copy="${escape(result.customer_reply)}">复制英文答复</button></div>`;
}

document.addEventListener("click", event => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.view) openView(button.dataset.view);
  if (button.dataset.searchAccount) run(async () => {
    const account = button.dataset.searchAccount;
    if (!accounts.some(a=>a.id === account)) await refreshAccounts();
    openView("inbox");
    industryForm.elements.account.value = account;
    industryOffset = 0;
    $("#industry-comments").open = true;
    industryForm.elements.keyword.focus();
    $("#industry-comments").scrollIntoView({block:"start"});
    await refreshIndustry();
  });
  if (button.dataset.example) run(() => useExample(button.dataset.example));
  if (button.dataset.answerField) {
    $("#search-fields").open = true;
    $("#search").elements.namedItem(button.dataset.answerField).focus();
  }
  if (button.hasAttribute("data-measured")) {
    const field = $("#search").elements.namedItem(button.dataset.field);
    field.value = button.dataset.measured;
    $("#search-fields").open = true;
    run(search);
  }
  if (button.classList.contains("inspect")) run(() => openEvidence(button));
  if (button.classList.contains("price-from-part")) fillCustomQuote(button.dataset.code);
  if (button.classList.contains("remove-attachment")) {
    button.closest("fieldset").remove();
    clearCustomQuote();
  }
  if (button.dataset.copy) run(async () => {await navigator.clipboard.writeText(button.dataset.copy);notify("已复制");});
  if (button.classList.contains("lookup")) run(async () => {
    exampleFields = {};
    $("#search").reset();
    $("#query").value = $(".body",button.closest("article")).textContent;
    openView("fit");
    await search();
  });
  if (button.classList.contains("assess")) run(async () => {
    const article = button.closest("article");
    const id = Number(article.dataset.id);
    const display = text => {
      inquiryAssessments.set(id,text);
      const active = document.querySelector('#inquiries article[data-id="' + id + '"] .assessment');
      if (active) active.textContent = text;
    };
    if (inquiryAssessments.get(id) === "正在分析客户原文…") return;
    display("正在分析客户原文…");
    try {
      const result = await api("/api/assist/inquiry","POST",{body:$(".body",article).textContent});
      display(`AI 建议：${roleNames[result.role]} · ${result.intent === "buy" ? "采购询问" : "需核实意图"} · ${result.evidence || "缺少原文依据"}`);
    } catch (err) { display(err.message); }

  });
  if (button.classList.contains("prepare-invite")) {
    const form = $("#invitation");
    form.elements.buyer.value = button.dataset.buyer;
    form.elements.email.value = button.dataset.email;
    form.elements.buyer.focus();
  }
});

respond($("#search"), search);
function invalidateSearch() {
  searchVersion++;
  $("#results").replaceChildren();
}
$("#search").addEventListener("input", event => {
  if (event.target.name === "query") {
    const form = event.currentTarget;
    for (const [key, value] of Object.entries(exampleFields)) {
      if (key !== "query" && form.elements[key].value === String(value)) form.elements[key].value = "";
    }
    exampleFields = {};
  } else {
    delete exampleFields[event.target.name];
  }
  invalidateSearch();
});
respond(quoteForm, calculateCustomQuote);
$("#add-attachment").addEventListener("click",addAttachment);
quoteForm.elements.includes_attachments.addEventListener("change",toggleAttachments);
quoteForm.addEventListener("input",clearCustomQuote);
respond($("#invitation"), invitation);
respond($("#capture-buyer"), async data => {
  await api("/api/buyers","POST",data);
  $("#capture-buyer").reset();
  $("#buyer-filter").value = data.company;
  $("#buyer-grade").value = "";
  await refreshBuyers();
  notify("已登记买家与长期联系方式");
});
const industryForm = $("#industry-search");
let industryOffset = 0, industryVersion = 0, industryTimer;
async function refreshIndustry() {
  const version = ++industryVersion;
  clearTimeout(industryTimer);
  if (!$("#industry-comments").open) return;
  const account = industryForm.elements.account.value;
  const page = await api("/api/social-comments?" + new URLSearchParams({account,offset:industryOffset}));
  if (version !== industryVersion) return;
  $("#industry-status").textContent = page.task.message || (account
    ? "选择关键词开始搜索，登录时请在本机浏览器内完成。" : "请先在「渠道与账号」添加小红书或抖音账号。");
  industryForm.querySelector(".primary").disabled = page.task.running || !account;
  $("#industry-stop").hidden = !page.task.running;
  $("#industry-items").innerHTML = page.items.map(row=>`<article class="inquiry">
    <p class="mini">${escape(info.platforms[row.platform])} · ${escape(row.label)} · ${escape(row.keyword)} · 采集于 ${date(row.captured_at)} 北京时间</p>
    <strong>${escape(row.sender)}</strong><p class="body">${escape(row.body)}</p>
    <a href="${escape(row.url)}" target="_blank" rel="noopener">${escape(row.title || "查看作品原页")}</a>
    <button class="quiet" data-social-comment="${row.id}" ${row.inquiry_id ? "disabled" : ""}>${row.inquiry_id ? "已加入收件箱" : "加入收件箱"}</button>
  </article>`).join("") || '<p class="empty">当前账号暂无候选评论。</p>';
  $("#industry-prev").hidden = industryOffset === 0;
  $("#industry-next").hidden = !page.more;
  if (page.task.running) industryTimer = setTimeout(()=>run(refreshIndustry),2000);
}
$("#industry-comments").ontoggle = ()=>run(refreshIndustry);
industryForm.elements.account.onchange = ()=>{industryOffset=0;run(refreshIndustry);};
industryForm.onsubmit = event => {
  event.preventDefault();
  const data = formData(industryForm);
  run(async()=>{
    await api("/api/social-comments/search","POST",{...data,limit:Number(data.limit)});
    industryOffset=0;
    await refreshIndustry();
  });
};
$("#industry-stop").onclick = ()=>run(async()=>{await api("/api/social-comments/stop","POST");await refreshIndustry();});
$("#industry-items").onclick = e=>{const button=e.target.closest("button[data-social-comment]");if(button) run(async()=>{
  const result=await api("/api/social-comments/"+button.dataset.socialComment+"/accept","POST");
  notify(result.added ? "已加入询盘收件箱" : "此评论已登记");
  await refreshIndustry();await refreshInbox();
});};
$("#industry-prev").onclick=()=>{industryOffset=Math.max(0,industryOffset-30);run(refreshIndustry);};
$("#industry-next").onclick=()=>{industryOffset+=30;run(refreshIndustry);};

respond($("#manual"), async data => {
  const field = $("#manual [name=event_id]");
  if (!field.value.trim()) field.value = crypto.randomUUID();
  data.event_id = field.value;
  if (data.occurred_at) data.occurred_at = new Date(data.occurred_at).toISOString();
  const result = await api("/api/inbox","POST",data);
  $("#manual").reset();
  notify(result.added ? "已登记询盘" : "此账号已登记相同编号");
  await refreshInbox();
});
respond($("#import-inbox"), async data => {
  if (data.file.size > 10000000) throw new Error("请选择小于 10 MB 的 CSV");
  const result = await api("/api/inbox/import","POST",{account:data.account,content:await data.file.text()});
  notify(`新增 ${result.added} 条，重复 ${result.duplicates} 条`);
  await refreshInbox();
});
respond($("#add-account"), async data => {
  const result = await api("/api/accounts","POST",data);
  let next = "可在「询盘收件箱」选择此账号，手动登记或导入询盘。";
  let action = "";
  if (supportsCommentSearch(data.platform)) {
    next = "现在可以用这个账号搜索行业评论。";
    action = `<button class="primary" type="button" data-search-account="${escape(result.id)}">用此账号搜索评论</button>`;
  }
  if (data.platform === "instagram" && data.external_id.trim()) {
    next = "完成 Meta 授权和回调订阅后，用另一个测试账号发一条文字私信，再到「询盘收件箱」按此账号查看带“实接”标记的消息。";
  }
  $("#new-account").innerHTML = `<p role="status">已在本工作台登记「${escape(data.label)}」。${next}</p>${action}
    <details><summary>上游转接凭据（仅此次可查看）</summary>
    <pre>POST /ingest/${escape(result.id)}\nAuthorization: Bearer ${escape(result.secret)}</pre></details>`;
  $("#add-account").reset();
  await refreshAccounts();
});
respond($("#case"), async data => {
  await api("/api/cases","POST",data);
  $("#case").reset();
  await refreshInfo();
  notify("经验已保存，待技术复核");
});
respond($("#import"),async data => {
  const result = await api("/api/import/" + data.kind,"POST",{content:await data.file.text()});
  notify(`已导入 ${result.imported} 条`);
  await refreshInfo();
});
$("#import [name=kind]").addEventListener("change",event => {
  $("#template").href = `/examples/${event.target.value}.csv`;
});
$("#buyer-file").addEventListener("change",event => run(async () => {
  if (!event.target.files.length) return;
  const res = await api("/api/buyers/import","POST",{content:await event.target.files[0].text()});
  notify(`已导入 ${res.imported} 家买家`);
  await refreshBuyers();
  event.target.value = "";
}));
["buyer-filter","buyer-grade"].forEach(id => $("#"+id).addEventListener("input",() => run(refreshBuyers)));
$("#copy-ad").addEventListener("click",() => run(async () => {
  await navigator.clipboard.writeText("Sourcing heavy-duty brake linings or pads? Meet Kooying at Automechanika Shanghai, booth 1.1N59, 2–5 December 2026. Request a short booth meeting or a Huzhou factory visit with pickup by appointment.");
  notify("已复制英文文案");
}));
$$(".filters select").filter(select=> select.id.startsWith("filter-")).forEach(select => {
  select.addEventListener("change",() => run(async () => {
    inboxOffset = 0;
    if (select.id === "filter-platform") await refreshAccounts();
    await refreshInbox();
  }));
});
$("#next-inbox").addEventListener("click",() => { inboxOffset += 100;run(refreshInbox); });
$("#prev-inbox").addEventListener("click",() => { inboxOffset = Math.max(0,inboxOffset-100);run(refreshInbox); });
$("#sync").addEventListener("click",() => run(async () => {
  try {
    const res = await api("/api/sync","POST");
    notify(`同步完成，新增 ${res.added} 条`);
  } finally {
    await refreshInfo();
    await refreshAccounts();
    await refreshInbox();
  }
}));
$("#model-extract").addEventListener("click",() => run(async () => {
  const form = $("#search");
  const body = form.elements.query.value.trim();
  if (!body) { notify("请先输入客户原话"); return; }
  const version = searchVersion;
  const button = $("#model-extract");
  button.disabled = true;
  button.textContent = "正在提取…";
  try {
    const result = await api("/api/assist/query","POST",{body});
    if (version !== searchVersion) return;
    for (const [key,value] of Object.entries(result)) {
      if (!form.elements[key].value) {
        form.elements[key].value = value;
        exampleFields[key] = value;
      }
    }
    $("#search-fields").open = true;
    invalidateSearch();
    notify(Object.keys(result).length ? "请核对 AI 提取的字段，再查询" : "原文中缺少可提取的型号字段");
  } finally {
    button.disabled = false;
    button.textContent = "AI 提取字段";
  }
}));

run(async () => {
  await refreshInfo();
  await refreshAccounts();
  const entry = new URLSearchParams(location.search);
  const view = entry.get("desk");
  const destinations = ["inbox","industry","fit","fair","knowledge","accounts"];
  if (destinations.includes(view)) {
    const queue = entry.get("queue");
    if (["unassigned","unanswered","overdue"].includes(queue)) $("#filter-queue").value = queue;
    await openView(view === "industry" ? "inbox" : view, true);
    if (view === "industry") {
      $("#industry-comments").open = true;
      industryForm.elements.keyword.focus();
      $("#industry-comments").scrollIntoView();
    } else if (view === "inbox" && /^\d+$/.test(entry.get("inquiry") || "")) {
      const card = document.querySelector('#inquiries article[data-id="' + entry.get("inquiry") + '"]');
      if (card) {
        card.tabIndex = -1;
        card.focus();
        card.scrollIntoView({block:"start"});
      } else notify("这条询盘的状态已变化，请在收件箱切换筛选查看。");
    } else if (view === "fit") {
      $("#query").value = (entry.get("query") || "").slice(0,1600);
      if ($("#query").value.trim()) await search();
      else $("#query").focus();
    }
    if (!["industry","inbox"].includes(view)) await refreshInbox();
  } else {
    await Promise.all([useExample("wva"),refreshInbox()]);
  }
  setInterval(() => run(refreshInbox), 15000);
});
