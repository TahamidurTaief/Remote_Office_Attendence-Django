from django.urls import path
from . import views

app_name = 'tenants'

urlpatterns = [
    path('settings/company/', views.CompanyConfigurationView.as_view(), name='company_settings'),
]
