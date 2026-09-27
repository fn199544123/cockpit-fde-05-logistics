// 顶栏时钟
function tick() {
  const el = document.getElementById('clock');
  if (!el) return;
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  el.textContent = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
setInterval(tick, 1000);
tick();

// 超时 / 异常预警：大屏横幅无刷新轮询（每 15 秒）
function renderAlerts(data) {
  const c = data.counts;
  const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
  set('mCrit', c.critical); set('mWarn', c.warning); set('mTotal', c.total);
  set('navBadge', c.total);

  const nav = document.querySelector('.alert-nav');
  if (nav) {
    nav.classList.remove('has-crit', 'has-warn');
    if (c.critical) nav.classList.add('has-crit');
    else if (c.total) nav.classList.add('has-warn');
  }
  const banner = document.getElementById('alertBanner');
  if (banner) {
    banner.classList.remove('crit', 'warn', 'ok');
    banner.classList.add(c.critical ? 'crit' : (c.total ? 'warn' : 'ok'));
  }
  const list = document.getElementById('alertList');
  if (!list) return;
  const top = data.items.slice(0, 8);
  if (!top.length) {
    list.innerHTML = '<div class="ab-empty">✅ 当前无超时 / 异常，作业均在时限内</div>';
    return;
  }
  list.innerHTML = top.map((a) =>
    `<div class="ab-row ${a.level}">` +
    `<span class="ab-tag ${a.level}">${a.level_label}</span>` +
    `<span class="ab-cat">${a.icon}</span>` +
    `<span class="ab-code">${a.code}</span>` +
    `<span class="ab-subj">${a.subject}</span>` +
    `<span class="ab-reason">${a.reason}</span></div>`
  ).join('');
}
function pollAlerts() {
  fetch('/alerts/data/').then((r) => r.json()).then(renderAlerts).catch(() => {});
}
if (document.getElementById('alertBanner')) setInterval(pollAlerts, 15000);

// ————————————————— 大屏指标无刷新轮询（每 10 秒）—————————————————
function setNum(id, v) { const e = document.getElementById(id); if (e && e.textContent != v) e.textContent = v; }

// 状态分布：重建堆叠条 + 图例（保留标题节点）
function renderDist(id, rows) {
  const box = document.getElementById(id);
  if (!box) return;
  const segs = rows.filter((r) => r.count)
    .map((r) => `<span class="seg tone-${r.tone}" style="width:${r.pct}%" title="${r.label} ${r.count}"></span>`).join('');
  const legend = rows
    .map((r) => `<span class="lg"><i class="dot tone-${r.tone}"></i>${r.label} <b>${r.count}</b></span>`).join('');
  const barEl = box.querySelector('.dist-bar');
  const lgEl = box.querySelector('.dist-legend');
  if (barEl) barEl.innerHTML = segs;
  if (lgEl) lgEl.innerHTML = legend;
}

function renderDash(d) {
  const s = d.stats, r = d.rates;
  setNum('kOrders', s.orders); setNum('kItems', s.items); setNum('kWaves', s.waves);
  setNum('kTasks', s.tasks); setNum('kVehicles', s.vehicles); setNum('kRoutes', s.routes);

  // 三环健康度
  const gp = document.getElementById('gPick'); if (gp) gp.style.setProperty('--p', r.pick);
  const gt = document.getElementById('gTask'); if (gt) gt.style.setProperty('--p', r.task);
  const gv = document.getElementById('gVeh'); if (gv) gv.style.setProperty('--p', r.veh_pct);
  setNum('gPickV', r.pick); setNum('gTaskV', r.task); setNum('gVehV', r.veh_ready);

  // 订单流水线
  const stages = document.querySelectorAll('#flow .stage');
  d.pipeline.forEach((p, i) => {
    const st = stages[i]; if (!st) return;
    const num = st.querySelector('.st-num'); if (num) num.textContent = p.count;
    const bar = st.querySelector('.st-bar i'); if (bar) bar.style.width = p.pct + '%';
  });

  // 状态分布
  renderDist('distOrder', d.order_rows); renderDist('distWave', d.wave_rows);
  renderDist('distTask', d.task_rows); renderDist('distVehicle', d.veh_rows);
  renderDist('distRoute', d.route_rows);

  // 手写 SVG 图表（趋势曲线 / 占比环图 / 对比柱状）随轮询刷新
  if (window.renderCharts) window.renderCharts(d);

  // 库区产能
  const zbox = document.getElementById('zones');
  if (zbox && d.zones.length) {
    zbox.innerHTML = d.zones.map((z) =>
      `<div class="zrow"><span class="zname">${z.zone} 区</span>` +
      `<div class="zbar"><i style="width:${z.pct}%"></i></div>` +
      `<span class="zval"><b>${z.items}</b> 件 · ${z.orders} 单</span></div>`
    ).join('');
  }
}
function pollDash() {
  fetch('/dashboard/data/').then((r) => r.json()).then(renderDash).catch(() => {});
}
if (document.getElementById('flow')) setInterval(pollDash, 10000);
