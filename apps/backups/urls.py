from django.urls import path
from .views import (
    backup_list,
    create_manual_backup,
    upload_to_drive_view,
    delete_backup,
    save_drive_config,
    test_drive_connection_view,
    download_backup,
    manage_encryption,
    preview_portable_import,
    get_import_preview_status,
    execute_portable_import,
)

app_name = "backups"

urlpatterns = [
    path("", backup_list, name="backup_list"),
    path("create/", create_manual_backup, name="create_backup"),
    path("<int:pk>/download/", download_backup, name="download_backup"),
    path("<int:pk>/upload-drive/", upload_to_drive_view, name="upload_to_drive"),
    path("<int:pk>/delete/", delete_backup, name="delete_backup"),
    path("settings/", save_drive_config, name="save_drive_config"),
    path("test-drive/", test_drive_connection_view, name="test_drive"),
    path("encryption/", manage_encryption, name="manage_encryption"),
    path("import/preview/", preview_portable_import, name="preview_import"),
    path("import/preview/<str:preview_id>/", get_import_preview_status, name="preview_status"),
    path("import/execute/", execute_portable_import, name="execute_import"),
]

