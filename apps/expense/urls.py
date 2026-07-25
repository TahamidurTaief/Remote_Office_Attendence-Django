from django.urls import path
from . import views

app_name = 'expense'

urlpatterns = [
    # Staff URLs
    path('staff/', views.StaffExpenseListView.as_view(), name='staff_expense_list'),
    path('staff/create/', views.StaffExpenseCreateView.as_view(), name='staff_expense_create'),
    path('staff/<int:pk>/', views.ExpenseDetailView.as_view(), name='expense_detail'),
    path('staff/<int:pk>/edit/', views.StaffExpenseUpdateView.as_view(), name='staff_expense_edit'),
    path('staff/<int:pk>/submit/', views.SubmitExpenseDraftView.as_view(), name='submit_draft'),
    
    # Admin URLs
    path('admin/', views.AdminExpenseListView.as_view(), name='admin_expense_list'),
    path('admin/<int:pk>/approve/', views.ApproveExpenseView.as_view(), name='approve_expense'),
    path('admin/<int:pk>/reject/', views.RejectExpenseView.as_view(), name='reject_expense'),
    path('admin/<int:pk>/return/', views.ReturnExpenseView.as_view(), name='return_expense'),
]
