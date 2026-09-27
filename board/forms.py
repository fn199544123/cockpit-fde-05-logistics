"""基础台账 —— 各主数据的表单（ModelForm）。

统一给控件挂上暗色大屏用的 class，日期字段用 datetime-local。
演示系统，字段说明与占位符均为脱敏虚构内容。
"""

from django import forms

from .models import Vehicle, Wave, Order, PickTask, Route


class _DateTimeLocal(forms.DateTimeInput):
    """HTML5 datetime-local 控件，兼容浏览器原生选择器。"""

    input_type = 'datetime-local'

    def __init__(self, attrs=None):
        attrs = {**(attrs or {})}
        super().__init__(attrs=attrs, format='%Y-%m-%dT%H:%M')


class _StyledMixin:
    """给所有字段控件统一挂 class，便于暗色样式接管。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            w = field.widget
            css = 'inp'
            if isinstance(w, forms.Select):
                css = 'inp sel'
            elif isinstance(w, forms.CheckboxInput):
                css = 'chk'
            elif isinstance(w, forms.SelectMultiple):
                css = 'inp multi'
            w.attrs.setdefault('class', css)


class VehicleForm(_StyledMixin, forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = ['code', 'name', 'driver_alias', 'capacity', 'status']
        widgets = {
            'code': forms.TextInput(attrs={'placeholder': '如 V-001'}),
            'name': forms.TextInput(attrs={'placeholder': '如 一号配送车'}),
            'driver_alias': forms.TextInput(attrs={'placeholder': '如 张师傅'}),
        }


class WaveForm(_StyledMixin, forms.ModelForm):
    class Meta:
        model = Wave
        fields = ['code', 'name', 'zone', 'status', 'planned_start', 'planned_end']
        widgets = {
            'code': forms.TextInput(attrs={'placeholder': '如 W-20260923-01'}),
            'name': forms.TextInput(attrs={'placeholder': '如 上午 A 区波次'}),
            'zone': forms.TextInput(attrs={'placeholder': 'A / B / C'}),
            'planned_start': _DateTimeLocal(),
            'planned_end': _DateTimeLocal(),
        }


class OrderForm(_StyledMixin, forms.ModelForm):
    class Meta:
        model = Order
        fields = ['code', 'receiver', 'address', 'item_count', 'status', 'wave']
        widgets = {
            'code': forms.TextInput(attrs={'placeholder': '如 SO-100001'}),
            'receiver': forms.TextInput(attrs={'placeholder': '如 某企业北区仓'}),
            'address': forms.TextInput(attrs={'placeholder': '脱敏地址，如 A 区 3 号门'}),
        }


class PickTaskForm(_StyledMixin, forms.ModelForm):
    class Meta:
        model = PickTask
        fields = ['code', 'wave', 'order', 'location', 'picker_alias', 'qty', 'status']
        widgets = {
            'code': forms.TextInput(attrs={'placeholder': '如 PT-100001'}),
            'location': forms.TextInput(attrs={'placeholder': '如 A-03-12'}),
            'picker_alias': forms.TextInput(attrs={'placeholder': '如 李师傅'}),
        }


class RouteForm(_StyledMixin, forms.ModelForm):
    def clean(self):
        data=super().clean();vehicle=data.get('vehicle');orders=data.get('orders')
        if vehicle and orders is not None:
            if sum(o.item_count for o in orders)>vehicle.capacity:
                self.add_error('orders','承运件数超过车辆额定容量。')
            if data.get('status')!='done' and vehicle.routes.exclude(pk=self.instance.pk).filter(status__in=['planned','running']).exists():
                self.add_error('vehicle','该车辆已有活动路线。')
            if data.get('status')!='done' and vehicle.status=='maintenance':
                self.add_error('vehicle','维保车辆不能承运。')
        if orders is not None and orders.exclude(routes__pk=self.instance.pk).filter(routes__status__in=['planned','running']).exists():
            self.add_error('orders','订单已在其他活动路线上。')
        if data.get('status') in ['running','done']:
            if not vehicle:self.add_error('vehicle','发车前必须指派配送车。')
            if orders is None or not orders.exists():self.add_error('orders','发车前必须关联订单。')
            elif orders.exclude(status__in=['picked','shipped']).exists():self.add_error('orders','订单尚未完成拣货，不能发车。')
        return data

    def save(self,commit=True):
        from django.db import transaction
        from django.utils import timezone
        with transaction.atomic():
            obj=super().save(commit=commit)
            if commit and obj.status in ['running','done']:
                obj.orders.update(status='shipped',updated_at=timezone.now())
                if obj.vehicle_id:Vehicle.objects.filter(pk=obj.vehicle_id).update(status='delivering' if obj.status=='running' else 'idle',updated_at=timezone.now())
                if obj.status=='running' and not obj.depart_at:
                    obj.depart_at=timezone.now();obj.save(update_fields=['depart_at','updated_at'])
            return obj

    class Meta:
        model = Route
        fields = ['code', 'name', 'vehicle', 'orders', 'stops', 'status', 'depart_at']
        widgets = {
            'code': forms.TextInput(attrs={'placeholder': '如 R-20260923-01'}),
            'name': forms.TextInput(attrs={'placeholder': '如 A 区东线'}),
            'depart_at': _DateTimeLocal(),
        }


# ---------------------------------------------------------------------------
# 快速录入表单：只保留高频必填字段，编号（code）由后端自动生成，不再手填。
# 供「数据录入 / 采集」页 /entry/ 使用，追求「打开即录、连续快录」。
# ---------------------------------------------------------------------------

class QuickVehicleForm(_StyledMixin, forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = ['name', 'driver_alias', 'capacity', 'status']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': '如 四号配送车', 'autofocus': True}),
            'driver_alias': forms.TextInput(attrs={'placeholder': '如 赵师傅'}),
        }


class QuickWaveForm(_StyledMixin, forms.ModelForm):
    class Meta:
        model = Wave
        fields = ['name', 'zone', 'status']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': '如 下午 B 区波次', 'autofocus': True}),
            'zone': forms.TextInput(attrs={'placeholder': 'A / B / C'}),
        }


class QuickOrderForm(_StyledMixin, forms.ModelForm):
    class Meta:
        model = Order
        fields = ['receiver', 'item_count', 'wave', 'status']
        widgets = {
            'receiver': forms.TextInput(attrs={'placeholder': '如 某企业 A 区收货点', 'autofocus': True}),
        }


class QuickPickTaskForm(_StyledMixin, forms.ModelForm):
    class Meta:
        model = PickTask
        fields = ['wave', 'order', 'location', 'picker_alias', 'qty', 'status']
        widgets = {
            'location': forms.TextInput(attrs={'placeholder': '如 A-03-12', 'autofocus': True}),
            'picker_alias': forms.TextInput(attrs={'placeholder': '如 李师傅'}),
        }


class QuickRouteForm(_StyledMixin, forms.ModelForm):
    def clean_status(self):
        status=self.cleaned_data['status']
        if status!='planned':raise forms.ValidationError('请先创建待发车路线，在台账关联订单并完成拣货后发车。')
        return status
    class Meta:
        model = Route
        fields = ['name', 'vehicle', 'stops', 'status']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': '如 B 区西线', 'autofocus': True}),
        }
