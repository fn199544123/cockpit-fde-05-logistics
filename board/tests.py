from django.test import TestCase,Client
from .models import Order,Wave,PickTask,Vehicle,Route
from . import scheduler

class LogisticsAcceptance(TestCase):
    def order(self,code='SO-TEST-1',qty=12,zone='A'):
        return Order.objects.create(code=code,receiver=f'某企业{zone}区测试点',item_count=qty)
    def vehicle(self,code='V-TEST-1',capacity=50):
        return Vehicle.objects.create(code=code,name='一号配送车',capacity=capacity)
    def test_full_picking_dispatch_delivery_flow(self):
        order=self.order();vehicle=self.vehicle()
        self.client.post('/dispatch/run/waves/')
        order.refresh_from_db();self.assertEqual(order.status,'picking')
        self.client.post('/dispatch/run/routes/')
        order.refresh_from_db();self.assertEqual(order.status,'picking')
        route=Route.objects.get();self.assertIsNone(route.depart_at)
        payload={'code':route.code,'name':route.name,'vehicle':vehicle.pk,'orders':[order.pk],'stops':1,'status':'running','depart_at':''}
        r=self.client.post(f'/ledger/route/{route.pk}/edit/',payload)
        self.assertEqual(r.status_code,200);route.refresh_from_db();self.assertEqual(route.status,'planned')
        task=PickTask.objects.get()
        r=self.client.post(f'/ledger/picktask/{task.pk}/edit/',{'code':task.code,'wave':task.wave_id,'order':order.pk,'location':task.location,'picker_alias':'一号师傅','qty':12,'status':'done'})
        self.assertEqual(r.status_code,302);order.refresh_from_db();self.assertEqual(order.status,'picked')
        self.assertEqual(Wave.objects.get().status,'done')
        self.assertEqual(self.client.post(f'/ledger/route/{route.pk}/edit/',payload).status_code,302)
        order.refresh_from_db();vehicle.refresh_from_db();self.assertEqual(order.status,'shipped');self.assertEqual(vehicle.status,'delivering')
        payload['status']='done';self.assertEqual(self.client.post(f'/ledger/route/{route.pk}/edit/',payload).status_code,302)
        vehicle.refresh_from_db();self.assertEqual(vehicle.status,'idle')
    def test_small_vehicle_never_overloaded_or_double_booked(self):
        self.vehicle(capacity=50);self.vehicle('V-TEST-2',10)
        for i in range(4):self.order(f'SO-T-{i}',30)
        scheduler.apply_waves();scheduler.apply_routes()
        self.assertEqual(Route.objects.count(),1)
        for route in Route.objects.all():self.assertLessEqual(sum(o.item_count for o in route.orders.all()),route.vehicle.capacity)
        self.assertEqual(scheduler.apply_routes()['route_count'],0)
        self.assertEqual(Order.objects.filter(routes__isnull=True).count(),3)
    def test_oversized_order_stays_pending(self):
        order=self.order(qty=61);self.assertEqual(scheduler.apply_waves()['wave_count'],0)
        order.refresh_from_db();self.assertIsNone(order.wave_id);self.assertEqual(order.status,'pending')
    def test_zero_and_negative_quantities_rejected(self):
        for qty in (0,-1):
            r=self.client.post('/entry/order/create/',{'receiver':'某企业A区','item_count':qty,'status':'pending'})
            self.assertEqual(r.status_code,400)
        r=self.client.post('/entry/order/bulk/',{'bulk':'某企业A区,0\n某企业B区,5'})
        self.assertEqual(len(r.json()['errors']),1);self.assertEqual(len(r.json()['created']),1)
    def test_cross_wave_task_rejected(self):
        order=self.order();scheduler.apply_waves();other=Wave.objects.create(code='W-OTHER')
        r=self.client.post('/entry/picktask/create/',{'order':order.pk,'wave':other.pk,'location':'A-01','qty':1,'status':'todo'})
        self.assertEqual(r.status_code,400)
    def test_route_capacity_manual_validation(self):
        order=self.order(qty=20);vehicle=self.vehicle(capacity=10);scheduler.apply_waves()
        r=self.client.post('/ledger/route/new/',{'code':'R-T','name':'A区测试线路','vehicle':vehicle.pk,'orders':[order.pk],'stops':1,'status':'planned'})
        self.assertEqual(r.status_code,200);self.assertEqual(Route.objects.count(),0)
    def test_pages_metrics_and_csrf(self):
        self.order();scheduler.apply_waves()
        for path in ['/','/dispatch/','/alerts/','/analysis/','/entry/order/','/ledger/order/']:
            self.assertEqual(self.client.get(path).status_code,200,path)
        m=self.client.get('/dashboard/data/').json();self.assertEqual(m['stats']['orders'],1);self.assertEqual(m['stats']['tasks'],1)
        a=self.client.get('/alerts/data/').json();self.assertEqual(a['counts']['total'],len(a['items']))
        self.assertEqual(Client(enforce_csrf_checks=True).post('/dispatch/run/waves/').status_code,403)
    def test_dispatch_is_idempotent(self):
        self.order();self.vehicle();scheduler.apply_waves();scheduler.apply_routes()
        self.assertEqual(scheduler.apply_waves()['wave_count'],0);self.assertEqual(scheduler.apply_routes()['route_count'],0)
