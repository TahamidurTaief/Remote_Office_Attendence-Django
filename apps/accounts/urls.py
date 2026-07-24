from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('', views.index_view, name='index'),
    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('logout/', views.CustomLogoutView.as_view(), name='logout'),
    path('change-password/', views.ChangePasswordView.as_view(), name='change_password'),

    # Forgot Password multi-step endpoints
    path('forgot-password/', views.ForgotPasswordView.as_view(), name='forgot_password'),
    path('forgot-password/request/', views.ForgotPasswordRequestView.as_view(), name='forgot_password_request'),
    path('forgot-password/verify/', views.ForgotPasswordVerifyView.as_view(), name='forgot_password_verify'),
    path('forgot-password/reset/', views.ForgotPasswordResetView.as_view(), name='forgot_password_reset'),

    # Admin User Lock & Force Logout Actions
    path('admin-panel/users/<int:pk>/force-logout/', views.AdminForceLogoutUserView.as_view(), name='admin_force_logout'),
    path('admin-panel/users/<int:pk>/unlock/', views.AdminUnlockUserView.as_view(), name='admin_unlock_user'),
    path('admin-panel/login-activity/', views.AdminLoginActivityView.as_view(), name='admin_login_activity'),

    # API and Session endpoints
    path('api/sync/', views.SyncApiView.as_view(), name='api_sync'),
    path('api/session/validate/', views.SessionValidateView.as_view(), name='api_session_validate'),
    path('account/sessions/', views.UserSessionsView.as_view(), name='user_sessions'),
]
