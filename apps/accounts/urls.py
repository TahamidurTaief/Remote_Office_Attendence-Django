from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('', views.index_view, name='index'),
    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('login/lock-status/', views.LoginLockStatusView.as_view(), name='lock_status'),
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

    # Workspace Lock & Heartbeat Security Endpoints
    path('security/workspace-lock/lock/', views.WorkspaceLockView.as_view(), name='workspace_lock'),
    path('security/workspace-lock/unlock/', views.WorkspaceUnlockView.as_view(), name='workspace_unlock'),
    path('security/heartbeat/', views.SecurityHeartbeatView.as_view(), name='security_heartbeat'),

    # MFA Security Endpoints
    path('account/mfa/setup/', views.MFASetupView.as_view(), name='mfa_setup'),
    path('account/mfa/disable/', views.MFADisableView.as_view(), name='mfa_disable'),
    path('login/mfa/verify/', views.LoginMFAVerifyView.as_view(), name='mfa_login_verify'),
    path('admin-panel/users/<int:pk>/mfa/disable/', views.AdminDisableUserMFAView.as_view(), name='admin_disable_user_mfa'),

    # Security Policy & Re-Auth Endpoints
    path('admin-panel/security/policies/', views.AdminSecurityPolicyListView.as_view(), name='admin_security_policies'),
    path('security/reauth/', views.SecurityReauthView.as_view(), name='security_reauth'),

    # API and Session endpoints
    path('api/sync/', views.SyncApiView.as_view(), name='api_sync'),
    path('api/session/validate/', views.SessionValidateView.as_view(), name='api_session_validate'),
    path('account/sessions/', views.UserSessionsView.as_view(), name='user_sessions'),

    # Security Settings Page + MFA Wizard (Phase 1 – Step 9 UX refactor)
    path('account/security/', views.SecuritySettingsView.as_view(), name='security_settings'),
    path('account/security/mfa/wizard/gate/', views.MFAWizardGateView.as_view(), name='mfa_wizard_gate'),
    path('account/security/mfa/wizard/qr/', views.MFAWizardQRView.as_view(), name='mfa_wizard_qr'),
    path('account/security/mfa/wizard/verify/', views.MFAWizardVerifyView.as_view(), name='mfa_wizard_verify'),
    path('account/security/mfa/wizard/complete/', views.MFAWizardCompleteView.as_view(), name='mfa_wizard_complete'),
    path('account/security/mfa/disable/', views.MFADisableWizardView.as_view(), name='mfa_disable_wizard'),
    path('account/security/trusted-device/<int:pk>/remove/', views.TrustedDeviceRemoveView.as_view(), name='trusted_device_remove'),
    path('account/security/backup-codes/regenerate/', views.BackupCodesRegenerateView.as_view(), name='backup_codes_regenerate'),
    path('account/security/pin/setup/', views.SetupPINView.as_view(), name='setup_pin'),
]

