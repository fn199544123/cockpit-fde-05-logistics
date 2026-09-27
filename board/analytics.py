"""分拣效率与车辆装载率分析引擎 —— 纯 ORM 聚合，可被视图 / 脚本 / 冒烟测试直接调用。

两大分析维度（结果均可 JSON 序列化，供分析页首屏渲染 + 无刷新轮询共用）：

1. 分拣效率（`_sorting()`）：从拣货任务（PickTask）出发，按
   「整体 / 拣货员 / 库区 / 波次」四个切面度量任务完成率、已拣件数与
   单位时间产能（件/小时，用波次真实起始时间估算工时）。
2. 车辆装载率（`_loading()`）：从配送路线（Route）出发，按
   「整体 / 单车 / 单路线」度量承运件数占额定载件的装载率，
   识别满载 / 欠载 / 闲置车辆，辅助运力调度。

数据高度脱敏：仅虚构编号、A/B/C 区库位、师傅化名。
"""
from collections import defaultdict

from django.utils import timezone

from .models import PickTask, Wave, Vehicle, Route


# —— 分级阈值（可按现场基线调整）——
LOAD_FULL_PCT = 90      # 装载率 ≥ 此值视为「饱和/满载」
LOAD_LOW_PCT = 55       # 装载率 < 此值视为「欠载」（运力浪费）
EFF_GOOD_PCT = 80       # 完成率 ≥ 此值视为「高效」
EFF_LOW_PCT = 40        # 完成率 < 此值视为「滞后」


def _pct(part, whole):
    return round(part * 100 / whole) if whole else 0


def _eff_tone(rate):
    """完成率 → 语义配色 slug（与 dashboard.css 的 tone-* 一致）。"""
    if rate >= EFF_GOOD_PCT:
        return 'good'
    if rate < EFF_LOW_PCT:
        return 'crit'
    return 'warn'


def _load_tone(rate):
    """装载率 → 语义配色 slug：超载红、满载青、正常蓝、欠载黄。"""
    if rate > 100:
        return 'crit'
    if rate >= LOAD_FULL_PCT:
        return 'good'
    if rate < LOAD_LOW_PCT:
        return 'warn'
    return 'info'


def _elapsed_hours(wave, now):
    """波次已投入工时（小时）：优先按计划开始，取不到用创建时间兜底，下限 0.1h。"""
    start = wave.planned_start or wave.created_at
    secs = (now - start).total_seconds() if start else 0
    return max(secs / 3600.0, 0.1)


# ---------------------------------------------------------------------------
# 分拣效率
# ---------------------------------------------------------------------------

def _sorting():
    now = timezone.now()

    tasks = list(
        PickTask.objects.select_related('wave', 'order').all()
    )
    total = len(tasks)
    done = [t for t in tasks if t.status == 'done']
    doing = [t for t in tasks if t.status == 'doing']
    todo = [t for t in tasks if t.status == 'todo']

    picked_items = sum(t.qty for t in done)
    total_items = sum(t.qty for t in tasks)
    done_rate = _pct(len(done), total)

    # —— 拣货员效率榜：完成任务数 / 已拣件数 / 完成率（按已拣件数降序）——
    pbucket = defaultdict(lambda: {'tasks': 0, 'done': 0, 'items': 0})
    for t in tasks:
        name = t.picker_alias or '未分派'
        b = pbucket[name]
        b['tasks'] += 1
        if t.status == 'done':
            b['done'] += 1
            b['items'] += t.qty
    pickers = []
    for name, b in pbucket.items():
        rate = _pct(b['done'], b['tasks'])
        pickers.append({
            'name': name, 'tasks': b['tasks'], 'done': b['done'],
            'items': b['items'], 'rate': rate, 'tone': _eff_tone(rate),
        })
    pickers.sort(key=lambda p: (-p['items'], -p['done']))

    # —— 库区效率：各区任务完成率与已拣/待拣件数（zone 取自波次库区）——
    zbucket = defaultdict(lambda: {'tasks': 0, 'done': 0, 'items': 0, 'pending_items': 0})
    for t in tasks:
        zone = (t.wave.zone or '其它') if t.wave else '其它'
        b = zbucket[zone]
        b['tasks'] += 1
        if t.status == 'done':
            b['done'] += 1
            b['items'] += t.qty
        else:
            b['pending_items'] += t.qty
    zones = []
    for zone in sorted(zbucket):
        b = zbucket[zone]
        rate = _pct(b['done'], b['tasks'])
        zones.append({
            'zone': zone, 'tasks': b['tasks'], 'done': b['done'],
            'items': b['items'], 'pending_items': b['pending_items'],
            'rate': rate, 'tone': _eff_tone(rate),
        })

    # —— 波次效率：完成率 + 已拣件数 + 工时 + 产能（件/小时）——
    tasks_by_wave = defaultdict(list)
    for t in tasks:
        if t.wave_id:
            tasks_by_wave[t.wave_id].append(t)
    wave_rows = []
    sum_items, sum_hours = 0, 0.0
    for w in Wave.objects.all():
        wt = tasks_by_wave.get(w.id, [])
        if not wt:
            continue
        wdone = [t for t in wt if t.status == 'done']
        witems = sum(t.qty for t in wdone)
        hours = _elapsed_hours(w, now)
        rate = _pct(len(wdone), len(wt))
        throughput = round(witems / hours, 1)
        sum_items += witems
        sum_hours += hours
        wave_rows.append({
            'code': w.code, 'zone': w.zone or '—', 'status': w.get_status_display(),
            'status_slug': w.status, 'tasks': len(wt), 'done': len(wdone),
            'items': witems, 'rate': rate, 'tone': _eff_tone(rate),
            'hours': round(hours, 1), 'throughput': throughput,
        })
    wave_rows.sort(key=lambda r: (-r['rate'], -r['throughput']))

    # 整体件效：已拣件数 / 累计投入工时（各波次工时之和）
    overall_tph = round(sum_items / sum_hours, 1) if sum_hours else 0.0
    top_picker = pickers[0]['name'] if pickers and pickers[0]['items'] else '—'

    return {
        'total_tasks': total, 'done': len(done), 'doing': len(doing), 'todo': len(todo),
        'done_rate': done_rate, 'done_tone': _eff_tone(done_rate),
        'picked_items': picked_items, 'total_items': total_items,
        'items_rate': _pct(picked_items, total_items),
        'tph': overall_tph, 'top_picker': top_picker,
        'active_pickers': sum(1 for p in pickers if p['name'] != '未分派'),
        'pickers': pickers, 'zones': zones, 'waves': wave_rows,
    }


# ---------------------------------------------------------------------------
# 车辆装载率
# ---------------------------------------------------------------------------

def _loading():
    routes = list(
        Route.objects.select_related('vehicle').prefetch_related('orders').all()
    )

    route_rows = []
    veh_bucket = defaultdict(lambda: {'routes': 0, 'items': 0, 'orders': 0, 'stops': 0})
    sum_load, load_n, full_n, low_n = 0, 0, 0, 0

    for r in routes:
        orders = list(r.orders.all())
        items = sum(o.item_count for o in orders)
        cap = r.vehicle.capacity if r.vehicle else 0
        rate = _pct(items, cap)
        route_rows.append({
            'code': r.code, 'name': r.name or '—', 'zone': _route_zone(orders),
            'vehicle': r.vehicle.name if r.vehicle else '未派车',
            'vehicle_code': r.vehicle.code if r.vehicle else '',
            'items': items, 'capacity': cap, 'rate': rate, 'tone': _load_tone(rate),
            'orders': len(orders), 'stops': r.stops or len({o.receiver for o in orders}),
            'status': r.get_status_display(), 'status_slug': r.status,
        })
        if r.vehicle_id:
            b = veh_bucket[r.vehicle_id]
            b['routes'] += 1
            b['items'] += items
            b['orders'] += len(orders)
            b['stops'] += r.stops or len({o.receiver for o in orders})
        if cap:
            sum_load += rate
            load_n += 1
            if rate >= LOAD_FULL_PCT:
                full_n += 1
            elif rate < LOAD_LOW_PCT:
                low_n += 1
    route_rows.sort(key=lambda r: -r['rate'])

    # —— 单车装载率：多趟平均（总承运件数 / (额定载件 × 趟数)）——
    veh_rows, idle = [], []
    for v in Vehicle.objects.all():
        b = veh_bucket.get(v.id)
        if not b or not b['routes']:
            if v.status != 'maintenance':
                idle.append({'code': v.code, 'name': v.name, 'capacity': v.capacity})
            continue
        denom = v.capacity * b['routes']
        rate = _pct(b['items'], denom)
        veh_rows.append({
            'code': v.code, 'name': v.name, 'driver': v.driver_alias or '—',
            'capacity': v.capacity, 'routes': b['routes'], 'items': b['items'],
            'orders': b['orders'], 'stops': b['stops'],
            'rate': rate, 'tone': _load_tone(rate),
            'status': v.get_status_display(), 'status_slug': v.status,
        })
    veh_rows.sort(key=lambda r: -r['rate'])

    avg_load = round(sum_load / load_n) if load_n else 0
    return {
        'avg_load': avg_load, 'avg_tone': _load_tone(avg_load),
        'routes_total': len(route_rows), 'full': full_n, 'low': low_n,
        'idle_count': len(idle), 'idle': idle,
        'active_vehicles': len(veh_rows),
        'vehicles': veh_rows, 'routes': route_rows,
        'thresholds': {'full': LOAD_FULL_PCT, 'low': LOAD_LOW_PCT},
    }


def _route_zone(orders):
    """路线库区：取承运订单的多数库区（脱敏演示中一条线路基本同区）。"""
    if not orders:
        return '—'
    counts = defaultdict(int)
    for o in orders:
        counts[o.zone] += 1
    return max(counts, key=counts.get)


# ---------------------------------------------------------------------------
# 对外统一入口
# ---------------------------------------------------------------------------

def compute_analytics():
    """汇总分拣效率与车辆装载率分析结果（可 JSON 序列化）。"""
    return {'sorting': _sorting(), 'loading': _loading()}
