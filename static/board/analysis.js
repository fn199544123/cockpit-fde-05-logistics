/* 效率分析中心 —— 每 10 秒无刷新轮询 /analysis/data/，
 * 平滑更新分拣效率与车辆装载率的全部数字、仪表环、占比条与明细表。
 * 顶栏时钟复用 dashboard.js。零依赖、不连 CDN。 */
(function () {
  'use strict';

  var TONE = { good: 'good', info: 'info', accent: 'accent', warn: 'warn', crit: 'crit', muted: 'muted' };

  function setNum(id, v) {
    var e = document.getElementById(id);
    if (e && e.textContent != v) e.textContent = v;
  }
  function setGauge(id, valId, pct) {
    var g = document.getElementById(id);
    if (g) g.style.setProperty('--p', pct);
    setNum(valId, pct);
  }
  // 依据 tone 切换元素的 tone-* 类（仪表环随完成率/装载率变色）
  function setTone(el, tone) {
    if (!el) return;
    Object.keys(TONE).forEach(function (t) { el.classList.remove('tone-' + t); });
    el.classList.add('tone-' + (TONE[tone] || 'muted'));
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  var zClass = function (z) { return (z === 'A' || z === 'B' || z === 'C') ? ' z-' + z : ''; };
  var mini = function (rate, tone) {
    var w = rate > 100 ? 100 : rate;
    return '<div class="minibar"><i class="tone-' + tone + '" style="width:' + w + '%"></i></div>' +
           '<span class="tone-' + tone + '">' + rate + '%</span>';
  };

  // —— 分拣效率 ——
  function renderSorting(s) {
    setGauge('gDone', 'gDoneV', s.done_rate);
    setTone(document.getElementById('gDone'), s.done_tone);
    setNum('sDone', s.done); setNum('sTasks', s.total_tasks);
    setNum('sPicked', s.picked_items); setNum('sTph', s.tph);
    setNum('sPickers', s.active_pickers); setNum('sDoing', s.doing); setNum('sTodo', s.todo);

    var zb = document.getElementById('zoneBars');
    if (zb) {
      zb.innerHTML = s.zones.length ? s.zones.map(function (z) {
        return '<div class="brow"><span class="bname">' + esc(z.zone) + ' 区</span>' +
          '<div class="bbar"><i class="tone-' + z.tone + '" style="width:' + z.rate + '%"></i></div>' +
          '<span class="bval"><b class="tone-' + z.tone + '">' + z.rate + '%</b> · ' +
          z.done + '/' + z.tasks + ' · ' + z.items + ' 件</span></div>';
      }).join('') : '<div class="empty">暂无拣货任务</div>';
    }

    var pt = document.querySelector('#pickerTable tbody');
    if (pt) {
      pt.innerHTML = s.pickers.length ? s.pickers.map(function (p) {
        return '<tr><td>' + esc(p.name) + '</td>' +
          '<td class="r mono">' + p.done + '/' + p.tasks + '</td>' +
          '<td class="r mono">' + p.items + '</td>' +
          '<td>' + mini(p.rate, p.tone) + '</td></tr>';
      }).join('') : '<tr><td colspan="4" class="empty">暂无拣货员数据</td></tr>';
    }

    var wt = document.querySelector('#waveTable tbody');
    if (wt) {
      wt.innerHTML = s.waves.length ? s.waves.map(function (w) {
        return '<tr><td class="mono">' + esc(w.code) + '</td>' +
          '<td><span class="zbadge' + zClass(w.zone) + '">' + esc(w.zone) + '</span></td>' +
          '<td><span class="pill s-' + w.status_slug + '">' + esc(w.status) + '</span></td>' +
          '<td class="r mono">' + w.done + '/' + w.tasks + '</td>' +
          '<td class="r mono">' + w.items + '</td>' +
          '<td class="r mono">' + w.hours + '</td>' +
          '<td class="r mono">' + w.throughput + '</td>' +
          '<td>' + mini(w.rate, w.tone) + '</td></tr>';
      }).join('') : '<tr><td colspan="8" class="empty">暂无波次数据</td></tr>';
    }
  }

  // —— 车辆装载率 ——
  function renderLoading(l) {
    setGauge('gLoad', 'gLoadV', l.avg_load);
    setTone(document.getElementById('gLoad'), l.avg_tone);
    setNum('lRoutes', l.routes_total); setNum('lFull', l.full);
    setNum('lLow', l.low); setNum('lActive', l.active_vehicles); setNum('lIdle', l.idle_count);

    var vb = document.getElementById('vehBars');
    if (vb) {
      vb.innerHTML = l.vehicles.length ? l.vehicles.map(function (v) {
        return '<div class="brow"><span class="bname">' + esc(v.name) +
          '<small>' + esc(v.driver) + '</small></span>' +
          '<div class="bbar"><i class="tone-' + v.tone + '" style="width:' +
          (v.rate > 100 ? 100 : v.rate) + '%"></i></div>' +
          '<span class="bval"><b class="tone-' + v.tone + '">' + v.rate + '%</b> · ' +
          v.items + '/' + v.capacity + ' 件 · ' + v.routes + ' 趟</span></div>';
      }).join('') : '<div class="empty">暂无在运车辆</div>';
    }

    var rt = document.querySelector('#routeTable tbody');
    if (rt) {
      rt.innerHTML = l.routes.length ? l.routes.map(function (r) {
        return '<tr><td class="mono">' + esc(r.code) + '</td>' +
          '<td><span class="zbadge' + zClass(r.zone) + '">' + esc(r.zone) + '</span></td>' +
          '<td>' + esc(r.vehicle) + '</td>' +
          '<td class="r mono">' + r.items + '/' + r.capacity + '</td>' +
          '<td class="r mono">' + r.stops + '</td>' +
          '<td>' + mini(r.rate, r.tone) + '</td></tr>';
      }).join('') : '<tr><td colspan="6" class="empty">暂无配送路线</td></tr>';
    }
  }

  function poll() {
    fetch('/analysis/data/').then(function (r) { return r.json(); }).then(function (a) {
      renderSorting(a.sorting);
      renderLoading(a.loading);
    }).catch(function () {});
  }
  if (document.querySelector('.analysis')) setInterval(poll, 10000);
})();
