from django.urls import path
from . import views

app_name = 'employees'

urlpatterns = [
    path('', views.EmployeeListView.as_view(), name='employee_list'),
    path('add/', views.EmployeeCreateView.as_view(), name='employee_add'),
    path('<int:pk>/', views.EmployeeDetailView.as_view(), name='employee_detail'),
    path('<int:pk>/edit/', views.EmployeeEditView.as_view(), name='employee_edit'),
    path('<int:pk>/toggle-status/', views.ToggleStatusView.as_view(), name='toggle_status'),
    
    # Document CRUD
    path('<int:employee_pk>/documents/add/', views.EmployeeDocumentCreateView.as_view(), name='document_add'),
    path('documents/<int:pk>/edit/', views.EmployeeDocumentEditView.as_view(), name='document_edit'),
    path('documents/<int:pk>/delete/', views.EmployeeDocumentDeleteView.as_view(), name='document_delete'),
]
