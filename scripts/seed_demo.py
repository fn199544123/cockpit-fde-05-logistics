"""生成脱敏演示数据（幂等）。用法：
    ./.venv/bin/python scripts/seed_demo.py

数据全部虚构、高度脱敏：只出现「某企业」、A/B/C 区库位、
一号~八号配送车、师傅化名（张/李/王…）、虚构编号，
绝不含任何真实企业 / 人名 / 地名。

设计目标：数据量足够让大屏看板与各子页面（预警 / 调度 / 分析 / 台账）
都饱满好看——多波次、多车辆、上百订单、状态分布均衡、
含超时/异常样本，并把创建时间铺到过去 7 天形成真实趋势曲线。
"""
import os
import sys
import random
import django
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'app.settings')
django.setup()

from django.utils import timezone
from board.models import Order, Wave, PickTask, Vehicle, Route

# 固定随机种子：保证每次生成的数据分布稳定、可复现（幂等演示）。
random.seed(20260923)

ZONES = ['A', 'B', 'C']
CN_NUM = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十']
# 师傅化名池（拣货员 / 司机通用），全部为虚构化名。
ALIASES = ['张师傅', '李师傅', '王师傅', '赵师傅', '刘师傅',
           '陈师傅', '杨师傅', '黄师傅', '周师傅', '吴师傅']
# 收货点后缀（脱敏，仅描述方位/用途，不含真实地名）。
SPOTS = ['北区仓', '南区仓', '东门月台', '西门月台', '中转仓',
         '前置仓', '集货区', '暂存区', '越库口', '退货区']
STREETS = ['虚构路', '演示大道', '样例街', '示范巷', '模拟环路', '测试路']


def _wave_status_by_time(planned_end, now):
    """按计划结束时间给出一个合理的波次状态分布。"""
    if planned_end < now - timedelta(hours=1):
        return 'done'
    if planned_end < now + timedelta(minutes=30):
        return 'picking'
    return random.choice(['planned', 'picking', 'planned'])


def run():
    # 幂等：清空既有演示数据后重建。
    PickTask.objects.all().delete()
    for r in Route.objects.all():
        r.orders.clear()
    Route.objects.all().delete()
    Order.objects.all().delete()
    Wave.objects.all().delete()
    Vehicle.objects.all().delete()

    now = timezone.now()

    # —— 配送车队：8 辆，状态覆盖 待命/装车中/配送中/维保 ——
    veh_status_plan = ['delivering', 'delivering', 'loading', 'idle',
                       'delivering', 'idle', 'loading', 'maintenance']
    vehicles = []
    for i in range(1, 9):
        vehicles.append(Vehicle.objects.create(
            code=f'V-{i:03d}',
            name=f'{CN_NUM[i]}号配送车',
            driver_alias=ALIASES[(i - 1) % len(ALIASES)],
            capacity=180 + (i % 4) * 30,
            status=veh_status_plan[i - 1],
        ))
    active_vehicles = [v for v in vehicles if v.status != 'maintenance']

    order_idx = 100001
    task_idx = 100001
    route_seq = 1
    waves = []

    # —— 常规波次：过去 6 天 ~ 未来 1 天，每天每区各 1 个波次 ——
    # 天数越近订单越多，形成「近 7 日趋势」的自然起伏。
    day_offsets = [6, 5, 4, 3, 2, 1, 0, -1]      # 负数=未来（已排定）
    orders_per_wave_by_off = {6: 4, 5: 5, 4: 6, 3: 7, 2: 8, 1: 9, 0: 10, -1: 4}

    for off in day_offsets:
        day = now - timedelta(days=off)
        for zi, zone in enumerate(ZONES):
            wid = len(waves) + 1
            planned_start = day.replace(hour=8 + zi * 3, minute=0, second=0, microsecond=0)
            planned_end = planned_start + timedelta(hours=3)
            wave = Wave.objects.create(
                code=f'W-{day:%Y%m%d}-{zi + 1:02d}',
                name=f'{zone}区{day:%m%d}第{CN_NUM[zi + 1]}波',
                zone=zone,
                status=_wave_status_by_time(planned_end, now),
                planned_start=planned_start,
                planned_end=planned_end,
            )
            waves.append(wave)

            # 该波次订单状态：已完成波次多为已拣完/已发运，进行中的更分散。
            n_orders = orders_per_wave_by_off[off]
            for oi in range(1, n_orders + 1):
                if wave.status == 'done':
                    o_status = random.choice(['shipped', 'shipped', 'picked'])
                elif wave.status == 'picking':
                    o_status = random.choice(['pending', 'picking', 'picking', 'picked'])
                else:  # planned / paused
                    o_status = random.choice(['pending', 'pending', 'picking'])

                spot = random.choice(SPOTS)
                street = random.choice(STREETS)
                order = Order.objects.create(
                    code=f'SO-{order_idx}',
                    receiver=f'某企业{zone}区{spot}',
                    address=f'{zone}区{street}{random.randint(1, 99)}号',
                    item_count=random.randint(2, 60),
                    status=o_status,
                    wave=wave,
                )
                order_idx += 1

                # 每单 1~3 条拣货任务，状态跟随订单进度。
                for ti in range(1, random.randint(2, 4)):
                    if o_status in ('picked', 'shipped'):
                        t_status = 'done'
                    elif o_status == 'picking':
                        t_status = random.choice(['doing', 'done', 'todo'])
                    else:
                        t_status = 'todo'
                    PickTask.objects.create(
                        code=f'PT-{task_idx}',
                        wave=wave,
                        order=order,
                        location=f'{zone}-{random.randint(1, 20):02d}-{random.randint(1, 40):02d}',
                        picker_alias=random.choice(ALIASES),
                        qty=max(1, order.item_count // random.randint(1, 3)),
                        status=t_status,
                    )
                    task_idx += 1

            # 为「已完成 / 进行中」波次配一条路线，装上其中已拣完/已发运的订单。
            shippable = list(wave.orders.filter(status__in=['picked', 'shipped']))
            if shippable:
                if wave.status == 'done':
                    r_status, depart = 'done', planned_end + timedelta(minutes=30)
                else:
                    r_status, depart = random.choice(
                        [('running', now - timedelta(minutes=random.randint(10, 120))),
                         ('planned', now + timedelta(minutes=random.randint(20, 90)))]
                    )
                route = Route.objects.create(
                    code=f'R-{day:%Y%m%d}-{route_seq:02d}',
                    name=f'{zone}区{random.choice(["东线", "西线", "南线", "北线", "环线"])}',
                    vehicle=random.choice(active_vehicles),
                    stops=len(shippable),
                    status=r_status,
                    depart_at=depart,
                )
                route.orders.add(*shippable[:random.randint(min(2, len(shippable)), len(shippable))])
                route_seq += 1

    # —— 散单：未排波、待分拣的自由订单（留给「智能调度」页现场组波 / 规划路线）——
    for zone in ZONES:
        for oi in range(1, random.randint(6, 9)):
            Order.objects.create(
                code=f'SO-{order_idx}',
                receiver=f'某企业{zone}区{random.choice(SPOTS)}',
                address=f'{zone}区{random.choice(STREETS)}{random.randint(100, 199)}号',
                item_count=random.randint(3, 40),
                status='pending',
                wave=None,
            )
            order_idx += 1

    # —— 超时 / 异常演示样本：让「超时预警」开箱即有内容（全脱敏、虚构编号）——
    # 1) 已过计划结束仍在分拣的波次（波次超时）
    late_wave = Wave.objects.create(
        code='W-20260923-91', name='A区加急超时波', zone='A', status='picking',
        planned_start=now - timedelta(hours=3), planned_end=now - timedelta(hours=1),
    )
    waves.append(late_wave)
    # 2) 异常暂停波次
    Wave.objects.create(
        code='W-20260923-92', name='B区异常暂停波', zone='B', status='paused',
        planned_start=now - timedelta(hours=1), planned_end=now + timedelta(hours=1),
    )
    # 3) 到点未发车的路线（发车超时）
    Route.objects.create(
        code='R-20260923-91', name='A区超时线路', vehicle=active_vehicles[0],
        stops=4, status='planned', depart_at=now - timedelta(minutes=40),
    )
    # 4) 在途过久的路线（配送超时未回）
    Route.objects.create(
        code='R-20260923-93', name='C区在途超时线路', vehicle=active_vehicles[1],
        stops=6, status='running', depart_at=now - timedelta(hours=4),
    )
    # 5) 建单已久仍未拣完的订单（分拣超时）+ 关联超时拣货任务（拣货超时）
    for oi in range(1, 4):
        o = Order.objects.create(
            code=f'SO-{order_idx}', receiver=f'某企业A区加急月台{oi}',
            address=f'A区急件路{oi}号', item_count=oi * 4 + 6,
            status='picking', wave=late_wave,
        )
        Order.objects.filter(pk=o.pk).update(created_at=now - timedelta(hours=3, minutes=oi * 5))
        t = PickTask.objects.create(
            code=f'PT-{task_idx}', wave=late_wave, order=o,
            location=f'A-9{oi}-01', picker_alias=ALIASES[oi % len(ALIASES)],
            qty=o.item_count or 1, status='doing',
        )
        PickTask.objects.filter(pk=t.pk).update(created_at=now - timedelta(hours=2, minutes=30))
        order_idx += 1
        task_idx += 1

    # —— 让「近 7 日作业趋势」有真实曲线：把非超时订单/任务的 created_at
    #    对齐到其所属波次那天（散单铺散到近几天），超时样本保持今天 ——
    for o in Order.objects.exclude(receiver__contains='加急'):
        if o.wave and o.wave.planned_start and o.wave.code not in ('W-20260923-91', 'W-20260923-92'):
            base = o.wave.planned_start
        else:
            base = now - timedelta(days=random.randint(0, 6))
        ts = base + timedelta(hours=random.randint(0, 6), minutes=random.randint(0, 59))
        if ts > now:
            ts = now - timedelta(minutes=random.randint(5, 120))
        Order.objects.filter(pk=o.pk).update(created_at=ts)
        PickTask.objects.filter(order=o).update(created_at=ts + timedelta(minutes=30))

    print(
        'seed done: '
        f'waves={Wave.objects.count()} '
        f'orders={Order.objects.count()} '
        f'tasks={PickTask.objects.count()} '
        f'vehicles={Vehicle.objects.count()} '
        f'routes={Route.objects.count()}'
    )


if __name__ == '__main__':
    run()
