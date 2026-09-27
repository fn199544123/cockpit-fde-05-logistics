/* 大屏数据可视化 —— 全部手写 SVG，零依赖、不连任何 CDN。
 * 暴露 window.renderCharts(m)：用一份指标对象重绘 趋势曲线 / 占比环图 / 对比柱状，
 * 供首屏（内联 JSON）与轮询刷新共用。 */
(function () {
  'use strict';

  // 与 dashboard.css 中 tone-* 语义配色保持一致
  var TONE = {
    good: '#37e6c8', info: '#4aa8ff', accent: '#b18cff',
    warn: '#ffb547', crit: '#ff5470', muted: '#7f92b8',
  };
  var color = function (tone) { return TONE[tone] || TONE.muted; };

  var SVG_NS = 'http://www.w3.org/2000/svg';
  function paint(id, markup) {
    var box = document.getElementById(id);
    if (!box) return;
    // 用 SVG 命名空间解析，保证 innerHTML 注入后节点为真正的 SVG 元素
    var doc = new DOMParser().parseFromString(
      '<svg xmlns="' + SVG_NS + '">' + markup + '</svg>', 'image/svg+xml');
    box.innerHTML = '';
    var svg = doc.documentElement;
    // 迁移子节点到目标容器内的新 svg（保留 viewBox 等属性）
    box.appendChild(svg);
  }
  function html(id, markup) { var e = document.getElementById(id); if (e) e.innerHTML = markup; }

  // 把最大值向上取整到「好看」的刻度，保证 Y 轴分度整洁
  function niceMax(v) {
    if (v <= 0) return 5;
    var pow = Math.pow(10, Math.floor(Math.log10(v)));
    var f = v / pow;
    var step = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
    return step * pow;
  }

  // Catmull-Rom → 三次贝塞尔，得到平滑曲线路径
  function smooth(pts) {
    if (pts.length < 2) return pts.length ? 'M' + pts[0][0] + ',' + pts[0][1] : '';
    var d = 'M' + pts[0][0] + ',' + pts[0][1];
    for (var i = 0; i < pts.length - 1; i++) {
      var p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
      var c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6;
      var c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
      d += 'C' + c1x + ',' + c1y + ' ' + c2x + ',' + c2y + ' ' + p2[0] + ',' + p2[1];
    }
    return d;
  }

  // ————————————————— 趋势曲线（多序列平滑折线 + 面积）—————————————————
  function renderTrend(trend) {
    if (!trend || !trend.series) return;
    var W = 680, H = 250, padL = 42, padR = 16, padT = 16, padB = 28;
    var iw = W - padL - padR, ih = H - padT - padB;
    var labels = trend.labels, n = labels.length;
    var max = 0;
    trend.series.forEach(function (s) { s.points.forEach(function (v) { if (v > max) max = v; }); });
    max = niceMax(max);

    var xAt = function (i) { return padL + (n <= 1 ? iw / 2 : iw * i / (n - 1)); };
    var yAt = function (v) { return padT + ih - (max ? ih * v / max : 0); };

    var g = '';
    // Y 轴网格 + 刻度（4 段）
    for (var k = 0; k <= 4; k++) {
      var gv = max * k / 4, gy = yAt(gv);
      g += '<line x1="' + padL + '" y1="' + gy + '" x2="' + (W - padR) + '" y2="' + gy +
        '" stroke="#1e2b48" stroke-width="1"/>';
      g += '<text x="' + (padL - 8) + '" y="' + (gy + 4) + '" fill="#7f92b8" font-size="11" ' +
        'text-anchor="end">' + Math.round(gv) + '</text>';
    }
    // X 轴标签
    for (var i = 0; i < n; i++) {
      g += '<text x="' + xAt(i) + '" y="' + (H - 8) + '" fill="#7f92b8" font-size="11" ' +
        'text-anchor="middle">' + labels[i] + '</text>';
    }
    // 各序列：渐变面积 + 平滑线 + 数据点
    var defs = '';
    trend.series.forEach(function (s, si) {
      var c = color(s.tone), gid = 'trGrad' + si;
      defs += '<linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0%" stop-color="' + c + '" stop-opacity="0.34"/>' +
        '<stop offset="100%" stop-color="' + c + '" stop-opacity="0"/></linearGradient>';
      var pts = s.points.map(function (v, i) { return [xAt(i), yAt(v)]; });
      var line = smooth(pts);
      var area = line + ' L' + xAt(n - 1) + ',' + (padT + ih) + ' L' + xAt(0) + ',' + (padT + ih) + ' Z';
      g += '<path d="' + area + '" fill="url(#' + gid + ')"/>';
      g += '<path d="' + line + '" fill="none" stroke="' + c + '" stroke-width="2.5" ' +
        'stroke-linejoin="round" stroke-linecap="round"/>';
      pts.forEach(function (p) {
        g += '<circle cx="' + p[0] + '" cy="' + p[1] + '" r="3.2" fill="#0a0f1c" ' +
          'stroke="' + c + '" stroke-width="2"/>';
      });
    });

    paint('trendChart', '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet" ' +
      'width="100%" height="100%"><defs>' + defs + '</defs>' + g + '</svg>');
    html('trendLegend', trend.series.map(function (s) {
      return '<span class="lg"><i class="dot" style="background:' + color(s.tone) + '"></i>' +
        s.name + '</span>';
    }).join(''));
  }

  // ————————————————— 结构占比环图（stroke-dasharray 拼环）—————————————————
  function renderDonut(rows) {
    if (!rows) return;
    var segs = rows.filter(function (r) { return r.count > 0; });
    var total = rows.reduce(function (a, r) { return a + r.count; }, 0);
    var size = 190, cx = size / 2, cy = size / 2, r = 66, sw = 24;
    var C = 2 * Math.PI * r;

    var g = '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" ' +
      'stroke="#1e2b48" stroke-width="' + sw + '"/>';
    var off = 0;
    segs.forEach(function (s) {
      var len = C * s.count / (total || 1);
      var c = color(s.tone);
      g += '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="' + c +
        '" stroke-width="' + sw + '" stroke-dasharray="' + len + ' ' + (C - len) + '" ' +
        'stroke-dashoffset="' + (-off) + '" transform="rotate(-90 ' + cx + ' ' + cy + ')">' +
        '<title>' + s.label + ' ' + s.count + '（' + s.pct + '%）</title></circle>';
      off += len;
    });
    // 圆心：总量
    g += '<text x="' + cx + '" y="' + (cy - 4) + '" fill="#dbe6ff" font-size="34" ' +
      'font-weight="700" text-anchor="middle">' + total + '</text>';
    g += '<text x="' + cx + '" y="' + (cy + 18) + '" fill="#7f92b8" font-size="12" ' +
      'text-anchor="middle">订单总数</text>';

    paint('donutChart', '<svg viewBox="0 0 ' + size + ' ' + size + '" preserveAspectRatio="xMidYMid meet" ' +
      'width="100%" height="100%">' + g + '</svg>');
    html('donutLegend', rows.map(function (r) {
      return '<span class="lg"><i class="dot" style="background:' + color(r.tone) + '"></i>' +
        r.label + ' <b>' + r.count + '</b> · ' + r.pct + '%</span>';
    }).join(''));
  }

  // ————————————————— 对比柱状（各库区 订单数 vs 件数 分组柱）—————————————————
  function renderBars(zones) {
    if (!zones) return;
    var W = 680, H = 250, padL = 42, padR = 16, padT = 16, padB = 28;
    var iw = W - padL - padR, ih = H - padT - padB;
    var series = [
      { name: '订单数', tone: 'info', key: 'orders' },
      { name: '件数', tone: 'good', key: 'items' },
    ];
    var max = 0;
    zones.forEach(function (z) { series.forEach(function (s) { if (z[s.key] > max) max = z[s.key]; }); });
    max = niceMax(max);
    var n = zones.length || 1;
    var yAt = function (v) { return padT + ih - (max ? ih * v / max : 0); };

    var g = '';
    for (var k = 0; k <= 4; k++) {
      var gv = max * k / 4, gy = yAt(gv);
      g += '<line x1="' + padL + '" y1="' + gy + '" x2="' + (W - padR) + '" y2="' + gy +
        '" stroke="#1e2b48" stroke-width="1"/>';
      g += '<text x="' + (padL - 8) + '" y="' + (gy + 4) + '" fill="#7f92b8" font-size="11" ' +
        'text-anchor="end">' + Math.round(gv) + '</text>';
    }
    var groupW = iw / n, barW = Math.min(30, groupW * 0.26), gap = 8;
    zones.forEach(function (z, gi) {
      var gx = padL + groupW * gi + groupW / 2;
      var x0 = gx - barW - gap / 2;
      series.forEach(function (s, si) {
        var v = z[s.key] || 0, bx = x0 + si * (barW + gap), by = yAt(v), bh = padT + ih - by;
        var c = color(s.tone);
        g += '<rect x="' + bx + '" y="' + by + '" width="' + barW + '" height="' + bh +
          '" rx="3" fill="' + c + '" opacity="0.9"><title>' + z.zone + '区 ' + s.name + ' ' + v +
          '</title></rect>';
        g += '<text x="' + (bx + barW / 2) + '" y="' + (by - 5) + '" fill="' + c + '" ' +
          'font-size="11" font-weight="700" text-anchor="middle">' + v + '</text>';
      });
      g += '<text x="' + gx + '" y="' + (H - 8) + '" fill="#dbe6ff" font-size="12" ' +
        'font-weight="700" text-anchor="middle">' + z.zone + ' 区</text>';
    });

    paint('barsChart', '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet" ' +
      'width="100%" height="100%">' + g + '</svg>');
    html('barsLegend', series.map(function (s) {
      return '<span class="lg"><i class="dot" style="background:' + color(s.tone) + '"></i>' +
        s.name + '</span>';
    }).join(''));
  }

  window.renderCharts = function (m) {
    if (!m) return;
    renderTrend(m.trend);
    renderDonut(m.order_rows);
    renderBars(m.zones);
  };

  // 首屏：用内联 JSON 立即渲染（无需等首次轮询）
  document.addEventListener('DOMContentLoaded', function () {
    var el = document.getElementById('dashData');
    if (el) {
      try { window.renderCharts(JSON.parse(el.textContent)); } catch (e) { /* ignore */ }
    }
  });
})();
