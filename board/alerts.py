"""分拣 / 配送超时预警引擎。

统一的「异常/超标自动预警」逻辑：扫描波次、配送路线、订单、拣货任务，
按各自的 SLA（时限）判定「超时(critical)」与「临期(warning)」，并识别
「异常暂停」等状态异常，产出结构化预警列表与分级计数，供大屏看板突出展示、
预警中心页详列、以及 JSON 接口轮询刷新。

纯 ORM 逻辑，可被视图 / 脚本 / 冒烟测试直接调用；数据高度脱敏。
时限阈值集中在本文件顶部常量，按现场产能与承诺时效调整即可。
"""
from django.utils import timezone

from .models import Order, Wave, Route, PickTask


# —— 预警时限（SLA）参数，单位：分钟（可按现场承诺时效调整）——
ORDER_PICK_SLA_MIN = 120      # 订单自创建起「应拣完」时限；超出即分拣超时
PICK_TASK_SLA_MIN = 90        # 单条拣货任务从建单到完成的时限
ROUTE_RUN_MAX_MIN = 180       # 路线自发车起在途时限；超出视为配送超时未回
SOON_MIN = 30                 # 距时限 ≤ 此值且未超 → 临期预警（黄）

# 分级：critical 已超标（红）、warning 临期/状态异常（黄）
LEVEL_LABEL = {'critical': '超时', 'warning': '预警'}
CAT_ICON = {'wave': '🌊', 'route': '🚚', 'order': '📦', 'pick': '🧰'}


def _minutes(delta):
    """timedelta → 分钟（向下取整，负数归零）。"""
    return max(0, int(delta.total_seconds() // 60))


def _dur_text(minutes):
    """分钟数 → 「Xh Ym」中文可读时长。"""
    if minutes >= 60:
        h, m = divmod(minutes, 60)
        return f'{h}小时{m}分' if m else f'{h}小时'
    return f'{minutes}分钟'


def _alert(level, cat, code, subject, reason, ref_min):
    """构造一条预警记录。ref_min = 超时分钟数(critical) 或 距时限分钟数(warning)。"""
    return {
        'level': level,
        'level_label': LEVEL_LABEL[level],
        'category': cat,
        'icon': CAT_ICON[cat],
        'code': code,
        'subject': subject,
        'reason': reason,
        'ref_min': ref_min,
        'ref_text': _dur_text(ref_min),
    }


def compute_alerts(now=None):
    """扫描全量在办对象，返回 {'items': [...], 'counts': {...}}。

    items 已排序：先 critical 后 warning，同级按「超时/临近」程度降序，
    使最紧急的预警排在最前，便于大屏置顶突出。
    """
    now = now or timezone.now()
    items = []

    # —— 波次：计划结束已过而未完成 = 超时；临近结束未完成 = 临期；暂停 = 状态异常 ——
    for w in Wave.objects.all():
        subject = w.name or w.code
        if w.status == 'done':
            continue
        if w.status == 'paused':
            items.append(_alert('warning', 'wave', w.code, subject, '波次异常暂停，需人工介入', 0))
            continue
        if w.planned_end:
            if w.planned_end < now:
                items.append(_alert(
                    'critical', 'wave', w.code, subject,
                    f'计划{w.planned_end:%H:%M}结束仍未完成，已超时', _minutes(now - w.planned_end)))
            elif _minutes(w.planned_end - now) <= SOON_MIN:
                items.append(_alert(
                    'warning', 'wave', w.code, subject,
                    f'距计划结束仅剩{_dur_text(_minutes(w.planned_end - now))}，尚未完成',
                    _minutes(w.planned_end - now)))

    # —— 配送路线：应发车而未发 = 发车超时；在途超时限 = 配送超时未回；临近发车 = 临期 ——
    for r in Route.objects.select_related('vehicle'):
        subject = f'{r.code} {r.name}'.strip()
        if r.status == 'done' or not r.depart_at:
            continue
        veh = r.vehicle.name if r.vehicle else '未派车'
        if r.status == 'planned':
            if r.depart_at < now:
                items.append(_alert(
                    'critical', 'route', r.code, subject,
                    f'计划{r.depart_at:%H:%M}发车（{veh}）仍未发出，发车超时',
                    _minutes(now - r.depart_at)))
            elif _minutes(r.depart_at - now) <= SOON_MIN:
                items.append(_alert(
                    'warning', 'route', r.code, subject,
                    f'距计划发车仅剩{_dur_text(_minutes(r.depart_at - now))}（{veh}）',
                    _minutes(r.depart_at - now)))
        elif r.status == 'running':
            in_transit = _minutes(now - r.depart_at)
            if in_transit > ROUTE_RUN_MAX_MIN:
                items.append(_alert(
                    'critical', 'route', r.code, subject,
                    f'{veh}在途已{_dur_text(in_transit)}，超时限未回', in_transit - ROUTE_RUN_MAX_MIN))

    # —— 订单：自创建起超过应拣完时限而仍未拣完 = 分拣超时；临近时限 = 临期 ——
    for o in Order.objects.filter(status__in=('pending', 'picking')):
        age = _minutes(now - o.created_at)
        over = age - ORDER_PICK_SLA_MIN
        if over > 0:
            items.append(_alert(
                'critical', 'order', o.code, o.receiver,
                f'{o.get_status_display()}已{_dur_text(age)}，超分拣时限', over))
        elif ORDER_PICK_SLA_MIN - age <= SOON_MIN:
            items.append(_alert(
                'warning', 'order', o.code, o.receiver,
                f'{o.get_status_display()}已{_dur_text(age)}，逼近分拣时限', ORDER_PICK_SLA_MIN - age))

    # —— 拣货任务：超时限而未完成 = 拣货超时（只报超时，避免与订单预警重复刷屏）——
    for t in PickTask.objects.select_related('order').filter(status__in=('todo', 'doing')):
        age = _minutes(now - t.created_at)
        over = age - PICK_TASK_SLA_MIN
        if over > 0:
            items.append(_alert(
                'critical', 'pick', t.code, f'{t.location}·{t.order.receiver}',
                f'{t.get_status_display()}已{_dur_text(age)}，拣货超时', over))

    order_rank = {'critical': 0, 'warning': 1}
    items.sort(key=lambda a: (order_rank[a['level']], -a['ref_min']))

    counts = {
        'critical': sum(1 for a in items if a['level'] == 'critical'),
        'warning': sum(1 for a in items if a['level'] == 'warning'),
        'total': len(items),
    }
    for cat in CAT_ICON:
        counts[cat] = sum(1 for a in items if a['category'] == cat)
    return {'items': items, 'counts': counts}
