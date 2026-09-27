// 智能调度中心 —— 执行「自动组波 / 智能规划」并回显结果，成功后刷新方案。
(function () {
  'use strict';

  var csrf = document.querySelector('[name=csrfmiddlewaretoken]');
  var CSRF = csrf ? csrf.value : '';
  var toast = document.getElementById('toast');
  var timer = null;

  function showToast(msg, isErr) {
    toast.textContent = msg;
    toast.className = 'toast show' + (isErr ? ' err' : '');
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () { toast.className = 'toast'; }, 3200);
  }

  function run(action, btn) {
    var url = window.DISPATCH_RUN_URL.replace('ACTION', action);
    btn.classList.add('busy');
    btn.disabled = true;
    fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': CSRF, 'X-Requested-With': 'XMLHttpRequest' },
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, data: j }; });
    }).then(function (res) {
      if (res.ok && res.data.ok) {
        showToast('✔ ' + res.data.msg, false);
        // 让用户看清 toast 后刷新，方案表随之更新
        setTimeout(function () { window.location.reload(); }, 1100);
      } else {
        showToast('✖ 执行失败，请重试', true);
        btn.classList.remove('busy');
        btn.disabled = false;
      }
    }).catch(function () {
      showToast('✖ 网络异常，请重试', true);
      btn.classList.remove('busy');
      btn.disabled = false;
    });
  }

  document.querySelectorAll('.run-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (btn.disabled) return;
      run(btn.getAttribute('data-action'), btn);
    });
  });
})();
