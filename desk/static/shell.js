// Both the workbench and the stand-alone buyer search use the original workbench navigation.
const pages = [
  ["home","⌂","工作台首页"],
  ["discovery","◎","海外买家发现"],
  ["fit","⌕","型号与报价"],
  ["inbox","▤","询盘收件箱"],
  ["fair","◫","上海汽配展"],
  ["knowledge","▥","企业知识库"],
  ["accounts","◎","渠道与账号"]
];

export function mountDeskShell(current) {
  const standalone = current === "discovery";
  const links = pages.map(([view,icon,label]) => {
    const content = `<span aria-hidden="true">${icon}</span>${label}${view === "inbox" ? ' <em id="nav-count"></em>' : ""}`;
    const active = view === current ? ' aria-current="page"' : "";
    if (!standalone && view !== "discovery") return `<button data-view="${view}"${active}>${content}</button>`;
    const url = view === "discovery" ? "/static/discovery.html" : view === "home" ? "/" : "/?desk="+view;
    return `<a class="discovery-link" href="${url}"${active}>${content}</a>`;
  });
  document.querySelector(".sidebar").innerHTML = `
    <div class="brand"><span class="brand-mark" aria-hidden="true">K</span><div><strong>冠英外贸</strong><small>Kooying Sales Desk</small></div></div>
    <nav aria-label="工作台">${links.join("")}</nav>
    <div class="sidebar-footer"><span class="dot"></span> 销售工作台 <small id="model-status"></small></div>`;
  const alert = '<span class="dot"></span> <span id="top-alert">询盘收件箱</span>';
  document.querySelector(".topbar").innerHTML = '<span>浙江冠英汽车零部件有限公司</span>' +
    (standalone ? `<a class="quiet top-inbox" href="/?desk=inbox">${alert}</a>` :
      `<button class="quiet top-inbox" data-view="inbox">${alert}</button>`);
}
