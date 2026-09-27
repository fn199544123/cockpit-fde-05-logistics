// 基础台账 —— 删除前二次确认（原生，无依赖）
document.addEventListener('submit', function (e) {
  var form = e.target;
  var msg = form.getAttribute('data-confirm');
  if (msg && !window.confirm(msg)) {
    e.preventDefault();
  }
});
