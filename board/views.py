import re

from django.contrib import messages
from django.db import transaction
from django.db.models import Max
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Order, Wave, PickTask, Vehicle, Route
from . import forms
from . import scheduler
from . import alerts as alerts_engine
from . import analytics as analytics_engine
from .scheduler import next_code, _max_num  # noqa: F401 —— 编号生成唯一来源


# ---------------------------------------------------------------------------
# 大屏看板首页：核心指标 + 状态分布 + 区域产能 + 车辆运力 + 超时预警
# 所有数字全部来自真实数据库（board.models）；供页面首屏渲染 + JSON 轮询共用。
# ---------------------------------------------------------------------------

# 各状态在图表中的配色 slug（映射到 CSS 变量），保证颜色语义一致。
_ORDER_TONE = {'pending': 'warn', 'picking': 'info', 'picked': 'good', 'shipped': 'accent'}
_WAVE_TONE = {'planned': 'info', 'picking': 'accent', 'done': 'good', 'paused': 'crit'}
_TASK_TONE = {'todo': 'warn', 'doing': 'info', 'done': 'good'}
_VEH_TONE = {'idle': 'muted', 'loading': 'warn', 'delivering': 'accent', 'maintenance': 'crit'}
_ROUTE_TONE = {'planned': 'info', 'running': 'accent', 'done': 'good'}


def _breakdown(model, tone_map):
    """按模型 STATUS_CHOICES 顺序统计各状态数量，返回带配色的分布列表。"""
    from collections import Counter
    counts = Counter(model.objects.values_list('status', flat=True))
    total = sum(counts.values())
    rows = []
    for slug, label in model.STATUS_CHOICES:
        n = counts.get(slug, 0)
        rows.append({
            'slug': slug, 'label': label, 'count': n,
            'pct': round(n * 100 / total) if total else 0,
            'tone': tone_map.get(slug, 'muted'),
        })
    return rows, total


def _trend(days=7):
    """近 N 日作业趋势（真实 created_at 归集）：每日新增订单数 / 入库件数。

    两条序列都以订单创建为口径（每单必有件数），保证曲线逐日连续、可读；
    返回可 JSON 序列化结构，供手写 SVG 折线图渲染 + 轮询刷新共用。
    """
    from datetime import timedelta

    today = timezone.localdate()
    span = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    idx = {d: i for i, d in enumerate(span)}

    orders = [0] * days
    items = [0] * days
    for dt, cnt in Order.objects.values_list('created_at', 'item_count'):
        i = idx.get(timezone.localtime(dt).date())
        if i is not None:
            orders[i] += 1
            items[i] += cnt

    return {
        'labels': [f'{d.month}/{d.day}' for d in span],
        'series': [
            {'name': '新增订单', 'tone': 'info', 'points': orders},
            {'name': '入库件数', 'tone': 'good', 'points': items},
        ],
    }


def _dashboard_metrics():
    """汇总大屏所需的全部真实指标（可 JSON 序列化，供页面与轮询接口共用）。"""
    from django.db.models import Sum

    orders = Order.objects.all()
    order_rows, order_total = _breakdown(Order, _ORDER_TONE)
    wave_rows, _ = _breakdown(Wave, _WAVE_TONE)
    task_rows, task_total = _breakdown(PickTask, _TASK_TONE)
    veh_rows, veh_total = _breakdown(Vehicle, _VEH_TONE)
    route_rows, _ = _breakdown(Route, _ROUTE_TONE)

    total_items = orders.aggregate(n=Sum('item_count'))['n'] or 0

    # 订单流水线：待分拣 → 分拣中 → 已拣完 → 已发运（漏斗式推进）
    by_status = {r['slug']: r['count'] for r in order_rows}
    pipeline = [
        {'slug': s, 'label': dict(Order.STATUS_CHOICES)[s], 'count': by_status.get(s, 0),
         'tone': _ORDER_TONE[s]}
        for s in ('pending', 'picking', 'picked', 'shipped')
    ]
    pipe_max = max((p['count'] for p in pipeline), default=0)
    for p in pipeline:
        p['pct'] = round(p['count'] * 100 / pipe_max) if pipe_max else 0

    # 分拣完成率：已拣完+已发运 占全部订单
    done_orders = by_status.get('picked', 0) + by_status.get('shipped', 0)
    pick_rate = round(done_orders * 100 / order_total) if order_total else 0
    task_done_rate = next((r['pct'] for r in task_rows if r['slug'] == 'done'), 0)

    # 区域产能：A/B/C 各区订单数、件数（按 Order.zone 推断）
    zbucket = {}
    for o in orders:
        b = zbucket.setdefault(o.zone, {'zone': o.zone, 'orders': 0, 'items': 0})
        b['orders'] += 1
        b['items'] += o.item_count
    zones = [zbucket[z] for z in sorted(zbucket)]
    zmax = max((z['items'] for z in zones), default=0)
    for z in zones:
        z['pct'] = round(z['items'] * 100 / zmax) if zmax else 0

    # 运力：可用车（非维保）/ 全部车
    veh_ready = Vehicle.objects.exclude(status='maintenance').count()
    veh_pct = round(veh_ready * 100 / veh_total) if veh_total else 0

    return {
        'stats': {
            'orders': order_total, 'waves': Wave.objects.count(),
            'tasks': task_total, 'vehicles': veh_total,
            'routes': Route.objects.count(), 'items': total_items,
        },
        'rates': {
            'pick': pick_rate, 'task': task_done_rate,
            'veh_ready': veh_ready, 'veh_total': veh_total, 'veh_pct': veh_pct,
        },
        'pipeline': pipeline,
        'order_rows': order_rows,
        'wave_rows': wave_rows,
        'task_rows': task_rows,
        'veh_rows': veh_rows,
        'route_rows': route_rows,
        'zones': zones,
        'trend': _trend(),
    }


def dashboard(request):
    """大屏看板入口页：核心指标 + 状态分布 + 区域产能 + 超时/异常预警。"""
    alerts = alerts_engine.compute_alerts()
    ctx = {
        'm': _dashboard_metrics(),
        'waves': Wave.objects.all()[:8],
        'routes': Route.objects.select_related('vehicle')[:8],
        'alert_counts': alerts['counts'],
        'alert_top': alerts['items'][:6],   # 大屏只置顶最紧急若干条，全量见预警中心
    }
    return render(request, 'board/dashboard.html', ctx)


def dashboard_data(request):
    """大屏指标 JSON：供首页无刷新轮询，保证投屏/录屏时数字持续鲜活。"""
    return JsonResponse(_dashboard_metrics())


# ---------------------------------------------------------------------------
# 超时 / 异常预警：预警中心页 + JSON 轮询接口
# ---------------------------------------------------------------------------

def alerts_page(request):
    """预警中心：分拣/配送超时与异常的完整预警列表 + 分级计数。"""
    alerts = alerts_engine.compute_alerts()
    ctx = {
        'counts': alerts['counts'],
        'items': alerts['items'],
        'sla': {
            'order': alerts_engine.ORDER_PICK_SLA_MIN,
            'pick': alerts_engine.PICK_TASK_SLA_MIN,
            'route': alerts_engine.ROUTE_RUN_MAX_MIN,
            'soon': alerts_engine.SOON_MIN,
        },
    }
    return render(request, 'board/alerts.html', ctx)


def alerts_data(request):
    """预警数据 JSON：供大屏 / 预警中心无刷新轮询。"""
    return JsonResponse(alerts_engine.compute_alerts())


# ---------------------------------------------------------------------------
# 分拣效率与车辆装载率分析：分析页 + JSON 轮询接口
# ---------------------------------------------------------------------------

def analysis_page(request):
    """效率分析中心：分拣效率（拣货员/库区/波次）+ 车辆装载率（单车/单线路）。"""
    a = analytics_engine.compute_analytics()
    ctx = {
        'a': a,
        'thr': {
            'load_full': analytics_engine.LOAD_FULL_PCT,
            'load_low': analytics_engine.LOAD_LOW_PCT,
            'eff_good': analytics_engine.EFF_GOOD_PCT,
            'eff_low': analytics_engine.EFF_LOW_PCT,
        },
    }
    return render(request, 'board/analysis.html', ctx)


def analysis_data(request):
    """分析指标 JSON：供分析页无刷新轮询，投屏/录屏时数字持续鲜活。"""
    return JsonResponse(analytics_engine.compute_analytics())


# ---------------------------------------------------------------------------
# 基础台账（主数据）管理：配置驱动的列表 / 新增 / 编辑
# ---------------------------------------------------------------------------

def _col(header, render_fn, width=None):
    return {'header': header, 'render': render_fn, 'width': width}


def _status(obj):
    """状态标签：文案 + 供 CSS 上色的 slug。"""
    return {'text': obj.get_status_display(), 'slug': obj.status}


# 每个台账：slug -> 元数据。render 均返回字符串或 {'text','slug'}（状态徽标）。
LEDGERS = {
    'vehicle': {
        'title': '配送车', 'icon': '🚚', 'model': Vehicle, 'form': forms.VehicleForm,
        'queryset': lambda: Vehicle.objects.all(),
        'search': ('code', 'name', 'driver_alias'),
        'columns': [
            _col('车辆编号', lambda o: o.code),
            _col('名称', lambda o: o.name),
            _col('司机化名', lambda o: o.driver_alias or '—'),
            _col('额定载件', lambda o: o.capacity),
            _col('状态', _status),
        ],
    },
    'wave': {
        'title': '分拣波次', 'icon': '🌊', 'model': Wave, 'form': forms.WaveForm,
        'queryset': lambda: Wave.objects.all(),
        'search': ('code', 'name', 'zone'),
        'columns': [
            _col('波次编号', lambda o: o.code),
            _col('名称', lambda o: o.name or '—'),
            _col('库区', lambda o: o.zone or '—'),
            _col('订单', lambda o: o.total_orders),
            _col('任务', lambda o: o.total_tasks),
            _col('状态', _status),
        ],
    },
    'order': {
        'title': '订单', 'icon': '📦', 'model': Order, 'form': forms.OrderForm,
        'queryset': lambda: Order.objects.select_related('wave'),
        'search': ('code', 'receiver', 'address'),
        'columns': [
            _col('订单编号', lambda o: o.code),
            _col('收货点', lambda o: o.receiver),
            _col('件数', lambda o: o.item_count),
            _col('所属波次', lambda o: o.wave.code if o.wave else '—'),
            _col('状态', _status),
        ],
    },
    'picktask': {
        'title': '拣货任务', 'icon': '🧾', 'model': PickTask, 'form': forms.PickTaskForm,
        'queryset': lambda: PickTask.objects.select_related('wave', 'order'),
        'search': ('code', 'location', 'picker_alias'),
        'columns': [
            _col('任务编号', lambda o: o.code),
            _col('库位', lambda o: o.location),
            _col('波次', lambda o: o.wave.code),
            _col('订单', lambda o: o.order.code),
            _col('拣货员', lambda o: o.picker_alias or '—'),
            _col('件数', lambda o: o.qty),
            _col('状态', _status),
        ],
    },
    'route': {
        'title': '配送路线', 'icon': '🗺️', 'model': Route, 'form': forms.RouteForm,
        'queryset': lambda: Route.objects.select_related('vehicle'),
        'search': ('code', 'name'),
        'columns': [
            _col('路线编号', lambda o: o.code),
            _col('名称', lambda o: o.name or '—'),
            _col('配送车', lambda o: o.vehicle.name if o.vehicle else '—'),
            _col('订单数', lambda o: o.orders.count()),
            _col('站点', lambda o: o.stops),
            _col('状态', _status),
        ],
    },
}

# 台账在导航中的展示顺序
LEDGER_ORDER = ['vehicle', 'wave', 'order', 'picktask', 'route']


def _nav():
    """台账侧栏/顶部导航项。"""
    return [
        {'slug': s, 'title': LEDGERS[s]['title'], 'icon': LEDGERS[s]['icon'],
         'count': LEDGERS[s]['model'].objects.count()}
        for s in LEDGER_ORDER
    ]


def _cfg(slug):
    cfg = LEDGERS.get(slug)
    if not cfg:
        raise Http404('未知台账')
    return cfg


def ledger_index(request):
    """台账总览：各主数据入口卡片。"""
    return render(request, 'board/ledger_index.html', {'nav': _nav()})


def ledger_list(request, slug):
    """某主数据的列表页，支持关键字搜索。"""
    cfg = _cfg(slug)
    qs = cfg['queryset']()
    q = (request.GET.get('q') or '').strip()
    if q:
        from django.db.models import Q
        cond = Q()
        for f in cfg['search']:
            cond |= Q(**{f'{f}__icontains': q})
        qs = qs.filter(cond)

    rows = []
    for obj in qs:
        rows.append({'pk': obj.pk, 'cells': [c['render'](obj) for c in cfg['columns']]})

    ctx = {
        'nav': _nav(), 'slug': slug, 'cfg': cfg, 'q': q,
        'columns': cfg['columns'], 'rows': rows,
        'total': len(rows),
    }
    return render(request, 'board/ledger_list.html', ctx)


def ledger_edit(request, slug, pk=None):
    """新增（pk 为空）/ 编辑某主数据记录。"""
    cfg = _cfg(slug)
    model, FormCls = cfg['model'], cfg['form']
    obj = get_object_or_404(model, pk=pk) if pk else None

    if request.method == 'POST':
        form = FormCls(request.POST, instance=obj)
        if form.is_valid():
            saved = form.save()
            messages.success(
                request, f'已{"更新" if pk else "新增"}{cfg["title"]}：{saved}')
            return redirect(reverse('ledger_list', args=[slug]))
    else:
        form = FormCls(instance=obj)

    ctx = {
        'nav': _nav(), 'slug': slug, 'cfg': cfg,
        'form': form, 'obj': obj,
        'is_new': obj is None,
    }
    return render(request, 'board/ledger_form.html', ctx)


def ledger_delete(request, slug, pk):
    """删除某主数据记录（仅 POST）。"""
    cfg = _cfg(slug)
    obj = get_object_or_404(cfg['model'], pk=pk)
    if request.method == 'POST':
        label = str(obj)
        obj.delete()
        messages.success(request, f'已删除{cfg["title"]}：{label}')
    return redirect(reverse('ledger_list', args=[slug]))


# ---------------------------------------------------------------------------
# 数据录入 / 采集：面向一线的「快速录入」。编号自动生成、录入即留、
# 连续快录（AJAX 提交后表单不跳转，实时追加「本次已录入」清单），
# 另配「批量粘贴」一次落库多条。
# ---------------------------------------------------------------------------

# 编号生成 next_code / _max_num 已移至 board/scheduler.py，此处顶部导入复用。


# 每类快速录入：slug -> 元数据。form 为精简的 Quick*Form（不含 code）。
ENTRY = {
    'order': {
        'title': '订单', 'icon': '📦', 'model': Order, 'form': forms.QuickOrderForm,
        'summary': lambda o: f'{o.code}　{o.receiver}　{o.item_count} 件'
                             + (f'　→{o.wave.code}' if o.wave else ''),
        'bulk_help': '每行一条：收货点, 件数[, 波次编号]　例：某企业A区收货点, 12, W-20260923-01',
    },
    'picktask': {
        'title': '拣货任务', 'icon': '🧾', 'model': PickTask, 'form': forms.QuickPickTaskForm,
        'summary': lambda o: f'{o.code}　@{o.location}　{o.wave.code}/{o.order.code}　{o.qty} 件',
        'bulk_help': '每行一条：波次编号, 订单编号, 库位, 件数[, 拣货员]　'
                     '例：W-20260923-01, SO-100001, A-03-12, 6, 李师傅',
    },
    'wave': {
        'title': '分拣波次', 'icon': '🌊', 'model': Wave, 'form': forms.QuickWaveForm,
        'summary': lambda o: f'{o.code}　{o.name or "—"}　{o.zone or "—"} 区',
        'bulk_help': '每行一条：名称[, 库区]　例：晚班C区波次, C',
    },
    'vehicle': {
        'title': '配送车', 'icon': '🚚', 'model': Vehicle, 'form': forms.QuickVehicleForm,
        'summary': lambda o: f'{o.code}　{o.name}　{o.driver_alias or "—"}',
        'bulk_help': '每行一条：名称[, 司机化名[, 额定载件]]　例：四号配送车, 赵师傅, 220',
    },
    'route': {
        'title': '配送路线', 'icon': '🗺️', 'model': Route, 'form': forms.QuickRouteForm,
        'summary': lambda o: f'{o.code}　{o.name or "—"}'
                             + (f'　{o.vehicle.name}' if o.vehicle else ''),
        'bulk_help': '每行一条：名称[, 站点数]　例：A区东线, 5',
    },
}
ENTRY_ORDER = ['order', 'picktask', 'wave', 'vehicle', 'route']


def _entry_nav():
    return [
        {'slug': s, 'title': ENTRY[s]['title'], 'icon': ENTRY[s]['icon'],
         'count': ENTRY[s]['model'].objects.count()}
        for s in ENTRY_ORDER
    ]


def _ecfg(slug):
    cfg = ENTRY.get(slug)
    if not cfg:
        raise Http404('未知录入类型')
    return cfg


def entry_page(request, slug='order'):
    """快速录入页：精简单条表单 + 批量粘贴，右侧实时「本次已录入」。"""
    cfg = _ecfg(slug)
    form = cfg['form']()
    recent = cfg['model'].objects.order_by('-created_at')[:8]
    ctx = {
        'entry_nav': _entry_nav(), 'slug': slug, 'cfg': cfg, 'form': form,
        'next_code': next_code(slug),
        'recent': [{'pk': o.pk, 'text': cfg['summary'](o)} for o in recent],
    }
    return render(request, 'board/entry.html', ctx)


@require_POST
def entry_create(request, slug):
    """AJAX：落库单条，返回新编号与摘要，供前端追加清单、不跳转。"""
    cfg = _ecfg(slug)
    form = cfg['form'](request.POST)
    if not form.is_valid():
        errs = {f: [str(e) for e in es] for f, es in form.errors.items()}
        return JsonResponse({'ok': False, 'errors': errs}, status=400)
    obj = form.save(commit=False)
    obj.code = next_code(slug)
    obj.save()
    form.save_m2m()
    return JsonResponse({
        'ok': True, 'pk': obj.pk, 'code': obj.code,
        'text': cfg['summary'](obj), 'next_code': next_code(slug),
    })


def _bulk_row(slug, parts):
    """把一行拆好的字段构造成对应模型的待存实例；数据非法则抛 ValueError。"""
    p = [x.strip() for x in parts]

    def need(i, label):
        if i >= len(p) or not p[i]:
            raise ValueError(f'缺少「{label}」')
        return p[i]

    if slug == 'order':
        obj = Order(receiver=need(0, '收货点'), item_count=int(need(1, '件数')))
        if len(p) > 2 and p[2]:
            obj.wave = Wave.objects.filter(code=p[2]).first() or _raise(f'波次 {p[2]} 不存在')
        return obj
    if slug == 'picktask':
        wave = Wave.objects.filter(code=need(0, '波次编号')).first() or _raise(f'波次 {p[0]} 不存在')
        order = Order.objects.filter(code=need(1, '订单编号')).first() or _raise(f'订单 {p[1]} 不存在')
        obj = PickTask(wave=wave, order=order, location=need(2, '库位'), qty=int(need(3, '件数')))
        if len(p) > 4:
            obj.picker_alias = p[4]
        return obj
    if slug == 'wave':
        return Wave(name=need(0, '名称'), zone=(p[1] if len(p) > 1 else ''))
    if slug == 'vehicle':
        obj = Vehicle(name=need(0, '名称'))
        if len(p) > 1:
            obj.driver_alias = p[1]
        if len(p) > 2 and p[2]:
            obj.capacity = int(p[2])
        return obj
    if slug == 'route':
        obj = Route(name=need(0, '名称'))
        if len(p) > 1 and p[1]:
            obj.stops = int(p[1])
        return obj
    raise Http404('未知录入类型')


def _raise(msg):
    raise ValueError(msg)


@require_POST
def entry_bulk(request, slug):
    """AJAX：批量粘贴（每行一条，逗号分隔），逐行校验落库。"""
    cfg = _ecfg(slug)
    text = request.POST.get('bulk', '')
    lines = [ln for ln in (raw.strip() for raw in text.splitlines()) if ln]
    created, errors = [], []
    for idx, line in enumerate(lines, 1):
        parts = line.replace('，', ',').split(',')  # 中英文逗号皆可
        try:
            with transaction.atomic():
                obj = _bulk_row(slug, parts)
                obj.code = next_code(slug)
                obj.full_clean()
                obj.save()
            created.append({'pk': obj.pk, 'code': obj.code, 'text': cfg['summary'](obj)})
        except Exception as e:  # noqa: BLE001 —— 单行失败不影响其余行，逐行反馈
            errors.append({'line': idx, 'raw': line, 'msg': str(e)})
    return JsonResponse({
        'ok': not errors, 'created': created, 'errors': errors,
        'next_code': next_code(slug),
    })


# ---------------------------------------------------------------------------
# 智能调度：订单自动组波次分拣、按区域智能规划配送路线。
# 页面先「预览方案」（不落库），点按钮才「执行落库」，AJAX 返回执行汇总。
# ---------------------------------------------------------------------------

def _dispatch_ctx():
    """调度中心页上下文：候选订单概览 + 两类方案预览。"""
    from django.db.models import Count, Sum

    wave_cands = Order.objects.filter(wave__isnull=True, status='pending')
    route_cands = Order.objects.filter(wave__isnull=False, routes__isnull=True).distinct()

    def _by_zone(qs):
        buckets = {}
        for o in qs:
            b = buckets.setdefault(o.zone, {'zone': o.zone, 'orders': 0, 'items': 0})
            b['orders'] += 1
            b['items'] += o.item_count
        return [buckets[z] for z in sorted(buckets)]

    return {
        'wave_zones': _by_zone(wave_cands),
        'wave_total': wave_cands.count(),
        'route_zones': _by_zone(route_cands),
        'route_total': route_cands.count(),
        'wave_plan': scheduler.plan_waves(),
        'route_plan': scheduler.plan_routes(),
        'params': {
            'max_items': scheduler.WAVE_MAX_ITEMS,
            'max_orders': scheduler.WAVE_MAX_ORDERS,
        },
        'vehicles_ready': len(scheduler._available_vehicles()),
        'oversized_orders': wave_cands.filter(item_count__gt=scheduler.WAVE_MAX_ITEMS).count(),
        'recent_waves': Wave.objects.filter(name__endswith='自动波次')
                            .order_by('-created_at')[:8],
        'recent_routes': Route.objects.filter(name__endswith='智能线路')
                             .select_related('vehicle').order_by('-created_at')[:8],
    }


def dispatch_page(request):
    """智能调度中心：预览两类方案，一键执行。"""
    return render(request, 'board/dispatch.html', _dispatch_ctx())


@require_POST
def dispatch_run(request, action):
    """AJAX：执行组波（action=waves）或规划路线（action=routes），返回汇总。"""
    if action == 'waves':
        result = scheduler.apply_waves()
        msg = (f'已自动组建 {result["wave_count"]} 个波次，'
               f'{result["orders"]} 单入波，派生 {result["tasks"]} 条拣货任务。'
               if result['wave_count'] else '没有待组波的订单。')
    elif action == 'routes':
        result = scheduler.apply_routes()
        msg = (f'已智能规划 {result["route_count"]} 条配送路线，'
               f'覆盖 {result["orders"]} 单。'
               if result['route_count'] else '没有待规划路线的订单（或无可用配送车）。')
    else:
        raise Http404('未知调度动作')
    return JsonResponse({'ok': True, 'result': result, 'msg': msg})
