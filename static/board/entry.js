// 数据录入 / 采集 —— 快速录入交互：AJAX 单条快录、批量粘贴、本次已录入清单。
(function () {
  'use strict';

  var slug = window.ENTRY_SLUG;
  var csrf = document.querySelector('[name=csrfmiddlewaretoken]');
  var CSRF = csrf ? csrf.value : '';
  var listEl = document.getElementById('entryList');
  var countEl = document.getElementById('sessionCount');
  var nextEl = document.getElementById('nextCode');
  var session = 0;

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': CSRF, 'X-Requested-With': 'XMLHttpRequest' },
      body: body,
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); });
  }

  function dropEmptyTip() {
    var tip = document.getElementById('emptyTip');
    if (tip) tip.remove();
  }

  function prepend(text) {
    dropEmptyTip();
    var li = document.createElement('li');
    li.className = 'fresh';
    li.innerHTML = '<span class="dot">▸</span>' + escapeHtml(text);
    listEl.insertBefore(li, listEl.firstChild);
    session += 1;
    countEl.textContent = session;
    // 动画结束后回落为历史样式
    setTimeout(function () { li.classList.remove('fresh'); li.classList.add('hist'); }, 950);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function clearErrors(form) {
    form.querySelectorAll('[data-err]').forEach(function (e) { e.textContent = ''; });
    form.querySelectorAll('.field.has-error').forEach(function (f) { f.classList.remove('has-error'); });
  }

  function showErrors(form, errors) {
    Object.keys(errors).forEach(function (name) {
      var wrap = form.querySelector('[data-field="' + name + '"]');
      if (wrap) {
        wrap.classList.add('has-error');
        var box = wrap.querySelector('[data-err]');
        if (box) box.textContent = errors[name].join('；');
      }
    });
  }

  function focusFirst(form) {
    var first = form.querySelector('input:not([type=hidden]), select, textarea');
    if (first) first.focus();
  }

  // ---- 单条快录 ----
  var singleForm = document.getElementById('singleForm');
  if (singleForm) {
    singleForm.addEventListener('submit', function (ev) {
      ev.preventDefault();
      clearErrors(singleForm);
      var btn = singleForm.querySelector('button[type=submit]');
      btn.disabled = true;
      post(singleForm.dataset.url, new FormData(singleForm)).then(function (res) {
        btn.disabled = false;
        if (res.data.ok) {
          prepend(res.data.text);
          if (nextEl && res.data.next_code) nextEl.textContent = res.data.next_code;
          // 只重置文本类输入，保留下拉选择（波次/订单/状态等，便于连续录同批）
          singleForm.querySelectorAll('input[type=text], input[type=number]').forEach(function (i) {
            i.value = '';
          });
          focusFirst(singleForm);
        } else if (res.data.errors) {
          showErrors(singleForm, res.data.errors);
        }
      }).catch(function () { btn.disabled = false; });
    });
  }

  // ---- 批量粘贴 ----
  var bulkForm = document.getElementById('bulkForm');
  if (bulkForm) {
    bulkForm.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var old = bulkForm.querySelector('.bulk-result');
      if (old) old.remove();
      var btn = bulkForm.querySelector('button[type=submit]');
      btn.disabled = true;
      post(bulkForm.dataset.url, new FormData(bulkForm)).then(function (res) {
        btn.disabled = false;
        var d = res.data;
        (d.created || []).forEach(function (c) { prepend(c.text); });
        if (nextEl && d.next_code) nextEl.textContent = d.next_code;

        var box = document.createElement('div');
        box.className = 'bulk-result';
        var okN = (d.created || []).length, errN = (d.errors || []).length;
        var summ = document.createElement('div');
        summ.className = 'msg ' + (errN ? 'error' : 'success');
        summ.textContent = '成功 ' + okN + ' 条' + (errN ? '，失败 ' + errN + ' 条：' : '');
        box.appendChild(summ);
        (d.errors || []).forEach(function (e) {
          var line = document.createElement('div');
          line.className = 'bulk-errline';
          line.textContent = '第 ' + e.line + ' 行「' + e.raw + '」：' + e.msg;
          box.appendChild(line);
        });
        bulkForm.appendChild(box);
        if (okN && !errN) bulkForm.querySelector('.bulk-area').value = '';
      }).catch(function () { btn.disabled = false; });
    });
  }

  // ---- 模式切换 ----
  document.querySelectorAll('.mode-btn').forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('.mode-btn').forEach(function (x) { x.classList.remove('active'); });
      b.classList.add('active');
      var single = b.dataset.mode === 'single';
      singleForm.classList.toggle('hidden', !single);
      bulkForm.classList.toggle('hidden', single);
      if (single) focusFirst(singleForm); else bulkForm.querySelector('.bulk-area').focus();
    });
  });
})();
