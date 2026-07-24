from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('', views.index_view, name='index'),
    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('logout/', views.CustomLogoutView.as_view(), name='logout'),
    path('change-password/', views.ChangePasswordView.as_view(), name='change_password'),

    # API and Session endpoints
    path('api/sync/', views.SyncApiView.as_view(), name='api_sync'),
    path('api/session/validate/', views.SessionValidateView.as_view(), name='api_session_validate'),
    path('account/sessions/', views.UserSessionsView.as_view(), name='user_sessions'),
]
