from django.urls import path
from . import views

app_name = 'branches'

urlpatterns = [
    path('', views.BranchListView.as_view(), name='branch_list'),
    path('add/', views.BranchCreateView.as_view(), name='branch_add'),
    path('<int:pk>/edit/', views.BranchEditView.as_view(), name='branch_edit'),
    path('<int:pk>/delete/', views.BranchDeleteView.as_view(), name='branch_delete'),
]
