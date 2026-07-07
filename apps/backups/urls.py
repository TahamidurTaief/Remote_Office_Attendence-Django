from django.urls import path
from .views import (
    backup_list,
    create_manual_backup,
    upload_to_drive_view,
    delete_backup,
    save_drive_config,
    test_drive_connection_view,
    download_backup,
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
]
