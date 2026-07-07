import json
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def get_drive_service(service_account_json_str):
    """
    Create Google Drive service from service account JSON string.
    """
    try:
        key_data = json.loads(service_account_json_str)
        credentials = service_account.Credentials.from_service_account_info(
            key_data, scopes=SCOPES
        )
        service = build(
            "drive",
            "v3",
            credentials=credentials,
            cache_discovery=False,
        )
        return service
    except Exception as e:
        raise Exception(f"Drive auth failed: {str(e)}")


def upload_to_drive(backup_record, filepath, config):
    """Upload backup file to Google Drive."""
    if not config.service_account_json:
        raise Exception("Google Drive not configured")

    service = get_drive_service(config.service_account_json)

    file_metadata = {
        "name": backup_record.file_name,
        "mimeType": "application/json",
        "parents": [config.folder_id],
    }

    media = MediaFileUpload(
        filepath, mimetype="application/json", resumable=True
    )

    file = (
        service.files()
        .create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )

    backup_record.gdrive_uploaded = True
    backup_record.gdrive_file_id = file.get("id", "")
    backup_record.gdrive_link = file.get("webViewLink", "")
    backup_record.save(update_fields=["gdrive_uploaded", "gdrive_file_id", "gdrive_link"])

    return file


def test_drive_connection(service_account_json_str, folder_id=None):
    """Test if Drive credentials work."""
    try:
        service = get_drive_service(service_account_json_str)
        service.files().list(
            pageSize=1,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        return True, "Connection successful"
    except Exception as e:
        return False, str(e)
