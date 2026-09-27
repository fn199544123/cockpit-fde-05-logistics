"""智能调度引擎 —— 订单自动组波次、按区域智能规划配送路线。

两大能力（均为纯 ORM 逻辑，可被视图 / 脚本 / 冒烟测试直接调用）：

1. `plan_waves` / `apply_waves`：把「未排波、待分拣」的订单按库区（A/B/C）聚类，
   受「单波最大件数 / 最大订单数」约束切成多个波次，并为每个订单自动派生拣货任务。
2. `plan_routes` / `apply_routes`：把「已入波、尚未上线路」的订单按库区聚类，
   用「首次适配递减（FFD）装箱 + 额定载件约束」分配到配送车，生成配送路线；
   路线内按地址门牌号做最近邻式排序，站点数=去重收货点数。

`plan_*` 只算方案（不落库，供页面预览），`apply_*` 真正落库并返回执行结果。
数据高度脱敏：仅虚构编号、A/B/C 区库位、师傅化名。
"""
import re

from django.db import transaction
from django.utils import timezone

from .models import Order, Wave, PickTask, Vehicle, Route


# —— 调度参数（可按现场产能调整）——
WAVE_MAX_ITEMS = 60      # 单波次最大总件数（分拣产能约束）
WAVE_MAX_ORDERS = 6      # 单波次最大订单数
TASKS_PER_ORDER = 1      # 每订单派生的拣货任务数（演示：一单一任务）


# ---------------------------------------------------------------------------
# 编号生成（全项目唯一来源；views.py 从此处导入复用）
# ---------------------------------------------------------------------------

def _max_num(model, prefix):
    """取某前缀下已用的最大数字序号（忽略非数字尾巴）。"""
    best = 0
    for c in model.objects.filter(code__startswith=prefix).values_list('code', flat=True):
        m = re.search(r'(\d+)$', c or '')
        if m:
            best = max(best, int(m.group(1)))
    return best


def next_code(slug):
    """按既有编号规则生成下一个不冲突的编号。"""
    today = timezone.localtime().strftime('%Y%m%d')
    if slug == 'vehicle':
        return f'V-{_max_num(Vehicle, "V-") + 1:03d}'
    if slug == 'order':
        return f'SO-{max(_max_num(Order, "SO-"), 100000) + 1}'
    if slug == 'picktask':
        return f'PT-{max(_max_num(PickTask, "PT-"), 100000) + 1}'
    if slug == 'wave':
        prefix = f'W-{today}-'
        return f'{prefix}{_max_num(Wave, prefix) + 1:02d}'
    if slug == 'route':
        prefix = f'R-{today}-'
        return f'{prefix}{_max_num(Route, prefix) + 1:02d}'
    raise ValueError(f'未知编号类型：{slug}')


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _door_no(order):
    """从脱敏地址里抽门牌号，用于路线内排序（取不到按订单号兜底）。"""
    m = re.search(r'(\d+)', order.address or '')
    return int(m.group(1)) if m else order.pk


def _group_by_zone(orders):
    """按库区聚类，返回 {zone: [order, ...]}，zone 有序 A→B→C→其它。"""
    buckets = {}
    for o in orders:
        buckets.setdefault(o.zone, []).append(o)
    return dict(sorted(buckets.items()))


# ---------------------------------------------------------------------------
# 能力一：订单自动组波次分拣
# ---------------------------------------------------------------------------

def _wave_candidates():
    """待组波的订单：尚未排入任何波次、且处于「待分拣」。"""
    return list(Order.objects.filter(wave__isnull=True, status='pending', item_count__gt=0, item_count__lte=WAVE_MAX_ITEMS))


def _chunk_orders(orders):
    """把同库区订单切成若干波次组，受单波最大件数/订单数双约束。

    订单按件数降序装箱：优先塞满，塞不下就新开一组，保证波次饱满而不超限。
    """
    orders = sorted(orders, key=lambda o: -o.item_count)
    groups, cur, cur_items = [], [], 0
    for o in orders:
        over_items = cur_items + o.item_count > WAVE_MAX_ITEMS
        over_count = len(cur) >= WAVE_MAX_ORDERS
        if cur and (over_items or over_count):
            groups.append(cur)
            cur, cur_items = [], 0
        cur.append(o)
        cur_items += o.item_count
    if cur:
        groups.append(cur)
    return groups


def plan_waves():
    """预览方案（不落库）：返回每个待建波次的 {zone, orders, items} 列表。"""
    plan = []
    for zone, orders in _group_by_zone(_wave_candidates()).items():
        for grp in _chunk_orders(orders):
            plan.append({
                'zone': zone,
                'orders': [o.code for o in grp],
                'order_count': len(grp),
                'items': sum(o.item_count for o in grp),
            })
    return plan


@transaction.atomic
def apply_waves():
    """执行组波：建波次、订单入波并置「分拣中」、派生拣货任务。返回执行汇总。"""
    now = timezone.now()
    waves_made, orders_assigned, tasks_made = [], 0, 0
    for zone, orders in _group_by_zone(_wave_candidates()).items():
        for grp in _chunk_orders(orders):
            wave = Wave.objects.create(
                code=next_code('wave'), name=f'{zone}区自动波次', zone=zone,
                status='planned', planned_start=now,
            )
            waves_made.append(wave.code)
            for oi, o in enumerate(grp, 1):
                o.wave = wave
                o.status = 'picking'
                o.save(update_fields=['wave', 'status', 'updated_at'])
                orders_assigned += 1
                for ti in range(1, TASKS_PER_ORDER + 1):
                    PickTask.objects.create(
                        code=next_code('picktask'), wave=wave, order=o,
                        location=f'{zone}-{oi:02d}-{ti:02d}',
                        qty=o.item_count, status='todo',
                    )
                    tasks_made += 1
    return {
        'waves': waves_made, 'wave_count': len(waves_made),
        'orders': orders_assigned, 'tasks': tasks_made,
    }


# ---------------------------------------------------------------------------
# 能力二：按区域智能规划配送路线
# ---------------------------------------------------------------------------

def _route_candidates():
    """待规划路线的订单：已入波、尚未挂到任何配送路线。"""
    return list(
        Order.objects.filter(wave__isnull=False, routes__isnull=True, status__in=['picking', 'picked'], item_count__gt=0)
        .distinct()
    )


def _available_vehicles():
    """可用配送车：排除维保中，额定载件大者优先（利于装箱）。"""
    return list(
        Vehicle.objects.filter(status='idle',capacity__gt=0).exclude(routes__status__in=['planned','running']).distinct().order_by('-capacity', 'code')
    )


def _bin_pack(orders, capacity):
    """首次适配递减（FFD）：把订单按件数装入不超过 capacity 的若干箱。"""
    orders = sorted(orders, key=lambda o: -o.item_count)
    bins = []  # [(已装件数, [order, ...])]
    for o in orders:
        placed = False
        for b in bins:
            if b[0] + o.item_count <= capacity:
                b[0] += o.item_count
                b[1].append(o)
                placed = True
                break
        if not placed:
            bins.append([o.item_count, [o]])
    return [(items, grp) for items, grp in bins]


def _plan_routes_raw():
    """内部：算出路线方案（含真实 order 对象），供预览与落库共用。

    返回 [{zone, vehicle, orders, items, stops}]。车辆按库区轮转复用
    （车比库区路线少时循环分配），额定载件用轮到的车辆容量做装箱上限。
    """
    vehicles = _available_vehicles()
    if not vehicles:
        return []
    routes = []
    for zone, orders in _group_by_zone(_route_candidates()).items():
        pending = sorted(orders, key=lambda o: (-o.item_count,o.pk))
        for veh in list(vehicles):
            grp=[];items=0
            for order in pending:
                if items+order.item_count<=veh.capacity:
                    grp.append(order);items+=order.item_count
            if not grp:continue
            pending=[o for o in pending if o not in grp]
            vehicles.remove(veh)
            grp = sorted(grp, key=_door_no)  # 路线内按门牌号排序（最近邻式）
            routes.append({
                'zone': zone, 'vehicle': veh, 'orders': grp,
                'items': items, 'stops': len({o.receiver for o in grp}),
            })
    return routes


def plan_routes():
    """预览方案（不落库）：返回每条待建路线的可读摘要列表。"""
    return [{
        'zone': r['zone'],
        'vehicle': r['vehicle'].name if r['vehicle'] else '—',
        'orders': [o.code for o in r['orders']],
        'order_count': len(r['orders']),
        'items': r['items'],
        'stops': r['stops'],
    } for r in _plan_routes_raw()]


@transaction.atomic
def apply_routes():
    """执行预规划；不改变实际拣货进度，每辆车最多占用一个活动路线。"""
    now = timezone.now()
    routes_made, orders_routed = [], 0
    for r in _plan_routes_raw():
        route = Route.objects.create(
            code=next_code('route'), name=f'{r["zone"]}区智能线路',
            vehicle=r['vehicle'], stops=r['stops'], status='planned',
            depart_at=None,
        )
        route.orders.set(r['orders'])
        routes_made.append(route.code)
        orders_routed += len(r['orders'])
    return {
        'routes': routes_made, 'route_count': len(routes_made),
        'orders': orders_routed,
    }
