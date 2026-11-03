/* ═══════════════════════════════════════════════════════════
   曲线查看器 · 渲染与交互
   ───────────────────────────────────────────────────────────
   设计系统阶段只承诺两件事：
     1. 能按数据画出可读的图（刻度、网格、峰值、准线）
     2. 状态切换不改尺寸（loading/error/empty 都保留坐标框架）
   缩放、拖动、降采样留给阶段 4 的真实实现，这里给视觉规格与数据接口。

   用法：
     CurveViewer.render(el, {
       title: '2026-01 · 96 点',
       unit: 'kW',
       times: ['00:00', '00:15', ...],
       series: [
         { name: '日曲线', data: [...], kind: 'line' },
         { name: '平均负荷', data: [...], kind: 'avg' },
         { name: '容量线 500', value: 500, kind: 'ref' }
       ],
       yMax: 1200, yStep: 200
     })
   ═══════════════════════════════════════════════════════════ */
(function (global) {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const VB_W = 820, VB_H = 284;
  const PAD = { l: 52, r: 20, t: 16, b: 34 };

  function el(tag, attrs, children) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    (children || []).forEach(c => n.appendChild(c));
    return n;
  }
  function text(x, y, s, cls, anchor) {
    const t = document.createElementNS(NS, 'text');
    t.setAttribute('x', x); t.setAttribute('y', y);
    t.setAttribute('class', cls || 'cv-tick-label');
    if (anchor) t.setAttribute('text-anchor', anchor);
    t.textContent = s;
    return t;
  }

  function niceStep(max, targetTicks) {
    const raw = max / targetTicks;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 4 ? 4 : 5;
    return step * mag;
  }

  function build(data) {
    const times = data.times || [];
    const n = times.length;
    const series = data.series || [];
    const vals = series.filter(s => Array.isArray(s.data)).flatMap(s => s.data)
      .filter(v => typeof v === 'number' && isFinite(v));
    const refVals = series.filter(s => typeof s.value === 'number').map(s => s.value);
    const dataMax = Math.max(0, ...vals, ...refVals);
    const dataMin = Math.min(0, ...vals);

    const yStep = data.yStep || niceStep(data.yMax || dataMax || 100, 6);
    const yMax = data.yMax || Math.ceil((dataMax || yStep) / yStep) * yStep;
    const yMin = dataMin < 0 ? Math.floor(dataMin / yStep) * yStep : 0;

    const x0 = PAD.l, x1 = VB_W - PAD.r;
    const y0 = VB_H - PAD.b, y1 = PAD.t;
    const sx = i => x0 + (n <= 1 ? 0 : (i * (x1 - x0)) / (n - 1));
    const sy = v => y0 - ((v - yMin) / (yMax - yMin)) * (y0 - y1);

    const g = el('g');

    // 网格与 Y 轴刻度
    const yTicks = [];
    for (let v = yMin; v <= yMax + 1e-9; v += yStep) yTicks.push(v);
    yTicks.forEach(v => {
      const y = sy(v);
      g.appendChild(el('line', { x1: x0, y1: y, x2: x1, y2: y, class: 'cv-grid' }));
      g.appendChild(text(x0 - 6, y + 3, String(Math.round(v)), 'cv-tick-label', 'end'));
    });
    // 轴线
    g.appendChild(el('line', { x1: x0, y1: y1, x2: x0, y2: y0, class: 'cv-axis' }));
    g.appendChild(el('line', { x1: x0, y1: y0, x2: x1, y2: y0, class: 'cv-axis' }));
    g.appendChild(text(8, 12, data.unit || '', 'cv-axis-name'));

    // X 轴刻度：抽 5 个
    const xTickCount = 5;
    for (let k = 0; k < xTickCount; k++) {
      const idx = Math.round((k * (n - 1)) / (xTickCount - 1));
      if (idx < 0 || idx >= n) continue;
      g.appendChild(text(sx(idx), y0 + 16, times[idx], 'cv-tick-label', 'middle'));
    }

    // 曲线
    series.forEach(s => {
      if (typeof s.value === 'number') {
        g.appendChild(el('line', {
          x1: x0, y1: sy(s.value), x2: x1, y2: sy(s.value), class: 'cv-line--ref'
        }));
        return;
      }
      if (!Array.isArray(s.data) || !s.data.length) return;
      let d = '';
      s.data.forEach((v, i) => {
        if (typeof v !== 'number' || !isFinite(v)) return;
        d += (d ? ' L' : 'M') + sx(i).toFixed(2) + ',' + sy(v).toFixed(2);
      });
      if (!d) return;
      const cls = s.kind === 'avg' ? 'cv-line cv-line--avg'
        : s.kind === 'compare' ? 'cv-line cv-line--compare'
          : 'cv-line';
      g.appendChild(el('path', { d, class: cls }));
    });

    // 峰值标注：找主曲线的最大值
    const main = series.find(s => s.kind !== 'avg' && s.kind !== 'ref' && Array.isArray(s.data));
    if (main) {
      let pi = -1, pv = -Infinity;
      main.data.forEach((v, i) => { if (typeof v === 'number' && v > pv) { pv = v; pi = i; } });
      if (pi >= 0) {
        g.appendChild(el('circle', { cx: sx(pi), cy: sy(pv), r: 2.5, class: 'cv-peak-dot' }));
        const label = Math.round(pv) + ' ' + (data.unit || '') + ' @ ' + times[pi];
        g.appendChild(text(sx(pi) + 6, sy(pv) - 6, label, 'cv-peak-label'));
      }
    }

    // 准线（hover 时由 CSS 显隐）
    const cross = el('line', {
      x1: x0, y1: y1, x2: x0, y2: y0, class: 'cv-crosshair', id: 'cv-cross'
    });
    g.appendChild(cross);

    return { node: g, sx, sy, x0, x1, y0, y1, n, times, series, PAD, xStepPx: (x1 - x0) / Math.max(1, n - 1) };
  }

  function render(root, data) {
    root.classList.add('curve-viewer');
    root.setAttribute('tabindex', '0');
    root.setAttribute('role', 'img');
    root.setAttribute('aria-label', (data.title || '负荷曲线') + '，' + (data.unit || ''));

    const stats = data.stats || [];
    root.innerHTML = '';

    const head = document.createElement('div');
    head.className = 'curve-viewer__head';
    head.innerHTML =
      '<span class="curve-viewer__title">' + (data.title || '') + '</span>' +
      '<span class="curve-viewer__stats">' +
      stats.map(s => '<span>' + s.label + '<b>' + s.value + '</b></span>').join('') +
      '</span>';
    root.appendChild(head);

    const plot = document.createElement('div');
    plot.className = 'curve-viewer__plot';
    const svg = el('svg', { viewBox: '0 0 ' + VB_W + ' ' + VB_H, preserveAspectRatio: 'xMidYMid meet' });
    const ctx = build(data);
    svg.appendChild(ctx.node);
    plot.appendChild(svg);
    root.appendChild(plot);

    // hover 气泡
    const tip = document.createElement('div');
    tip.className = 'cv-tooltip';
    plot.appendChild(tip);

    // 图例
    const legend = document.createElement('div');
    legend.className = 'curve-viewer__legend';
    legend.innerHTML = (data.series || [])
      .filter(s => s.name)
      .map(s => {
        const cls = s.kind === 'avg' ? ' cv-legend-swatch--avg'
          : s.kind === 'ref' ? ' cv-legend-swatch--ref'
            : s.kind === 'compare' ? ' cv-legend-swatch--compare' : '';
        return '<span class="cv-legend-item"><i class="cv-legend-swatch' + cls + '"></i>' +
          s.name + '</span>';
      }).join('');
    root.appendChild(legend);

    // 底部说明
    if (data.foot) {
      const foot = document.createElement('div');
      foot.className = 'curve-viewer__foot';
      foot.textContent = data.foot;
      root.appendChild(foot);
    }

    // 骨架 / 错误 / 空
    const sk = document.createElement('div');
    sk.className = 'curve-viewer__skeleton';
    sk.innerHTML = '<span></span>';
    root.appendChild(sk);

    const err = document.createElement('div');
    err.className = 'curve-viewer__error';
    err.innerHTML = '<div class="curve-viewer__error-title">' + (data.errorTitle || '数据不可用') + '</div>' +
      '<div class="curve-viewer__error-desc">' + (data.errorDesc || '') + '</div>';
    root.appendChild(err);

    const emp = document.createElement('div');
    emp.className = 'curve-viewer__empty empty';
    emp.innerHTML = '<div class="empty__title">没有数据</div>' +
      '<div class="empty__desc">' + (data.emptyDesc || '调整日期范围或重新导入文件') + '</div>';
    root.appendChild(emp);

    // hover：准线 + 气泡
    const cross = svg.querySelector('.cv-crosshair');
    plot.addEventListener('mousemove', ev => {
      if (root.dataset.state && root.dataset.state !== 'default') return;
      const host = plot.getBoundingClientRect();
      if (!host.width) return;
      const vbX = ((ev.clientX - host.left) / host.width) * VB_W;
      const i = Math.max(0, Math.min(ctx.n - 1, Math.round((vbX - ctx.x0) / ctx.xStepPx)));
      const px = ctx.sx(i);
      cross.setAttribute('x1', px);
      cross.setAttribute('x2', px);
      const main = ctx.series.find(s => s.kind !== 'avg' && s.kind !== 'ref' && Array.isArray(s.data));
      const v = main ? main.data[i] : null;
      if (typeof v === 'number' && isFinite(v)) {
        tip.innerHTML = '<span class="cv-tooltip__time">' + ctx.times[i] + '</span>' +
          '<b>' + Math.round(v) + '</b> ' + (data.unit || '');
        tip.dataset.visible = 'true';
        const leftPct = ((px - ctx.PAD.l) / (VB_W - ctx.PAD.l - ctx.PAD.r)) * 100;
        tip.style.left = Math.max(0, Math.min(100, leftPct)) + '%';
        tip.style.top = (((ctx.sy(v)) / VB_H) * 100) + '%';
      }
    });
    plot.addEventListener('mouseleave', () => { tip.dataset.visible = 'false'; });

    return root;
  }

  function setState(root, state) {
    root.dataset.state = state || 'default';
  }

  global.CurveViewer = { render, setState };
})(window);
