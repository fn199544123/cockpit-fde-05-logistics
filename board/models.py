"""仓储分拣调度看板 —— 核心数据模型。

演示系统，数据高度脱敏：仅使用虚构编号、A/B/C 区库位、
一号/二号配送车、师傅化名等，不含任何真实企业/人名/地名。
"""

from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError


class TimeStamped(models.Model):
    """通用时间戳基类。"""

    created_at = models.DateTimeField('创建时间', default=timezone.now)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    def clean(self):
        super().clean()
        errors={}
        for field in ('capacity','item_count','qty'):
            value=getattr(self,field,None)
            if value is not None and value<=0:errors[field]='数量必须大于零。'
        if errors:raise ValidationError(errors)

    class Meta:
        abstract = True


class Vehicle(TimeStamped):
    """配送车。"""

    STATUS_CHOICES = [
        ('idle', '待命'),
        ('loading', '装车中'),
        ('delivering', '配送中'),
        ('maintenance', '维保'),
    ]

    code = models.CharField('车辆编号', max_length=32, unique=True)   # 如 V-001
    name = models.CharField('车辆名称', max_length=64)               # 如 一号配送车
    driver_alias = models.CharField('司机化名', max_length=32, blank=True)  # 如 张师傅
    capacity = models.PositiveIntegerField('额定载件数', default=200)
    status = models.CharField('状态', max_length=16, choices=STATUS_CHOICES, default='idle')

    class Meta:
        verbose_name = '配送车'
        verbose_name_plural = '配送车'
        ordering = ['code']

    def __str__(self):
        return f'{self.code} {self.name}'


class Wave(TimeStamped):
    """分拣波次：一次集中分拣的批次。"""

    STATUS_CHOICES = [
        ('planned', '已排定'),
        ('picking', '分拣中'),
        ('done', '已完成'),
        ('paused', '暂停'),
    ]

    code = models.CharField('波次编号', max_length=32, unique=True)   # 如 W-20260923-01
    name = models.CharField('波次名称', max_length=64, blank=True)
    zone = models.CharField('作业库区', max_length=8, blank=True)     # A / B / C
    status = models.CharField('状态', max_length=16, choices=STATUS_CHOICES, default='planned')
    planned_start = models.DateTimeField('计划开始', null=True, blank=True)
    planned_end = models.DateTimeField('计划结束', null=True, blank=True)

    class Meta:
        verbose_name = '分拣波次'
        verbose_name_plural = '分拣波次'
        ordering = ['-created_at']

    def __str__(self):
        return self.code

    @property
    def total_orders(self):
        return self.orders.count()

    @property
    def total_tasks(self):
        return self.tasks.count()


class Order(TimeStamped):
    """订单：一个收货点 + 件数。"""

    STATUS_CHOICES = [
        ('pending', '待分拣'),
        ('picking', '分拣中'),
        ('picked', '已拣完'),
        ('shipped', '已发运'),
    ]

    code = models.CharField('订单编号', max_length=32, unique=True)      # 如 SO-100001
    receiver = models.CharField('收货点', max_length=64)                # 如 某企业北区仓
    address = models.CharField('收货地址', max_length=128, blank=True)  # 脱敏地址
    item_count = models.PositiveIntegerField('件数', default=1)
    status = models.CharField('状态', max_length=16, choices=STATUS_CHOICES, default='pending')
    wave = models.ForeignKey(
        Wave, verbose_name='所属波次', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='orders',
    )

    class Meta:
        verbose_name = '订单'
        verbose_name_plural = '订单'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.code} → {self.receiver}'

    @property
    def zone(self):
        """从收货点/地址推断作业库区（A/B/C）。演示脱敏数据中区名内嵌于文本。

        规则：优先取地址/收货点里的「X区」；取不到则回退到所属波次的库区；
        再取不到则归入 A 区，保证任何订单都可被自动调度覆盖。
        """
        import re as _re
        for text in (self.address, self.receiver):
            m = _re.search(r'([A-Z])\s*区', text or '')
            if m:
                return m.group(1)
        if self.wave and self.wave.zone:
            return self.wave.zone
        return 'A'


class PickTask(TimeStamped):
    """拣货任务：波次内针对某订单在某库位的拣货作业。"""

    STATUS_CHOICES = [
        ('todo', '待拣'),
        ('doing', '拣货中'),
        ('done', '已完成'),
    ]

    code = models.CharField('任务编号', max_length=32, unique=True)     # 如 PT-100001
    wave = models.ForeignKey(
        Wave, verbose_name='所属波次', on_delete=models.CASCADE, related_name='tasks',
    )
    order = models.ForeignKey(
        Order, verbose_name='关联订单', on_delete=models.CASCADE, related_name='tasks',
    )
    location = models.CharField('库位', max_length=32)                 # 如 A-03-12
    picker_alias = models.CharField('拣货员化名', max_length=32, blank=True)  # 如 李师傅
    qty = models.PositiveIntegerField('拣货件数', default=1)
    status = models.CharField('状态', max_length=16, choices=STATUS_CHOICES, default='todo')

    class Meta:
        verbose_name = '拣货任务'
        verbose_name_plural = '拣货任务'
        ordering = ['location']

    def __str__(self):
        return f'{self.code} @ {self.location}'

    def clean(self):
        super().clean()
        if self.order_id and self.wave_id:
            if self.order.wave_id != self.wave_id:
                raise ValidationError({'wave':'任务波次必须与订单波次一致。'})
            other=self.order.tasks.exclude(pk=self.pk).aggregate(n=models.Sum('qty'))['n'] or 0
            if self.qty is not None and other+self.qty>self.order.item_count:
                raise ValidationError({'qty':'任务合计件数不能超过订单件数。'})

    def save(self,*args,**kwargs):
        from django.db import transaction
        with transaction.atomic():
            super().save(*args,**kwargs)
            order=self.order
            if order.status!='shipped':
                finished=order.tasks.filter(status='done').aggregate(n=models.Sum('qty'))['n'] or 0
                complete=finished==order.item_count and not order.tasks.exclude(status='done').exists()
                Order.objects.filter(pk=order.pk).update(status='picked' if complete else 'picking',updated_at=timezone.now())
            wave=self.wave
            if wave.status!='paused':
                complete=wave.orders.exists() and not wave.orders.exclude(status__in=['picked','shipped']).exists()
                Wave.objects.filter(pk=wave.pk).update(status='done' if complete else 'picking',updated_at=timezone.now())


class Route(TimeStamped):
    """配送路线：一辆配送车承运一批订单的一条线路。"""

    STATUS_CHOICES = [
        ('planned', '待发车'),
        ('running', '配送中'),
        ('done', '已送达'),
    ]

    code = models.CharField('路线编号', max_length=32, unique=True)     # 如 R-20260923-01
    name = models.CharField('路线名称', max_length=64, blank=True)      # 如 A区东线
    vehicle = models.ForeignKey(
        Vehicle, verbose_name='配送车', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='routes',
    )
    orders = models.ManyToManyField(
        Order, verbose_name='配送订单', blank=True, related_name='routes',
    )
    stops = models.PositiveIntegerField('站点数', default=0)
    status = models.CharField('状态', max_length=16, choices=STATUS_CHOICES, default='planned')
    depart_at = models.DateTimeField('发车时间', null=True, blank=True)

    class Meta:
        verbose_name = '配送路线'
        verbose_name_plural = '配送路线'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.code} {self.name}'.strip()
