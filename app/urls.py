from django.contrib import admin
from django.urls import path

from board import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('dashboard/data/', views.dashboard_data, name='dashboard_data'),

    # 超时 / 异常预警：预警中心页 + JSON 轮询接口
    path('alerts/', views.alerts_page, name='alerts'),
    path('alerts/data/', views.alerts_data, name='alerts_data'),

    # 智能调度：订单自动组波次、按区域智能规划配送路线
    path('dispatch/', views.dispatch_page, name='dispatch'),
    path('dispatch/run/<slug:action>/', views.dispatch_run, name='dispatch_run'),

    # 分拣效率与车辆装载率分析
    path('analysis/', views.analysis_page, name='analysis'),
    path('analysis/data/', views.analysis_data, name='analysis_data'),

    # 数据录入 / 采集（快速录入）
    path('entry/', views.entry_page, name='entry_home'),
    path('entry/<slug:slug>/', views.entry_page, name='entry_page'),
    path('entry/<slug:slug>/create/', views.entry_create, name='entry_create'),
    path('entry/<slug:slug>/bulk/', views.entry_bulk, name='entry_bulk'),

    # 基础台账（主数据）管理
    path('ledger/', views.ledger_index, name='ledger_index'),
    path('ledger/<slug:slug>/', views.ledger_list, name='ledger_list'),
    path('ledger/<slug:slug>/new/', views.ledger_edit, name='ledger_new'),
    path('ledger/<slug:slug>/<int:pk>/edit/', views.ledger_edit, name='ledger_edit'),
    path('ledger/<slug:slug>/<int:pk>/delete/', views.ledger_delete, name='ledger_delete'),

    path('admin/', admin.site.urls),
]
