import os
import hashlib
import tempfile
import logging
from django.conf import settings
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from PIL import Image, ImageOps
from apps.audit.models import MediaAsset
try:
    from imagekitio import ImageKit
except ImportError:
    ImageKit = None

logger = logging.getLogger(__name__)

# Allowed sizes (in bytes)
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_PDF_SIZE = 20 * 1024 * 1024    # 20MB

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_PDF_EXTENSIONS = {".pdf"}

class MediaService:
    @staticmethod
    def get_ik_client():
        pub = os.environ.get("IMAGEKIT_PUBLIC_KEY") or getattr(settings, "IMAGEKIT_PUBLIC_KEY", None)
        priv = os.environ.get("IMAGEKIT_PRIVATE_KEY") or getattr(settings, "IMAGEKIT_PRIVATE_KEY", None)
        endpoint = os.environ.get("IMAGEKIT_URL_ENDPOINT") or getattr(settings, "IMAGEKIT_URL_ENDPOINT", None)
        if pub and priv and endpoint:
            try:
                return ImageKit(
                    private_key=priv,
                    public_key=pub,
                    url_endpoint=endpoint
                )
            except Exception as e:
                logger.error(f"Failed to initialize ImageKit client: {str(e)}")
        return None

    @staticmethod
    def compute_checksum(file_obj):
        hasher = hashlib.sha256()
        file_obj.seek(0)
        for chunk in file_obj.chunks():
            hasher.update(chunk)
        file_obj.seek(0)
        return hasher.hexdigest()

    @staticmethod
    def validate_file(file_obj):
        name = file_obj.name.lower()
        ext = os.path.splitext(name)[1]
        
        if ext in ALLOWED_IMAGE_EXTENSIONS:
            if file_obj.size > MAX_IMAGE_SIZE:
                raise ValidationError("Image file exceeds the 10MB limit.")
            # Check if image is malformed
            try:
                file_obj.seek(0)
                with Image.open(file_obj) as img:
                    img.verify()
            except Exception:
                raise ValidationError("Malformed or corrupted image file.")
            file_obj.seek(0)
            return "image"
            
        elif ext in ALLOWED_PDF_EXTENSIONS:
            if file_obj.size > MAX_PDF_SIZE:
                raise ValidationError("PDF file exceeds the 20MB limit.")
            # Check PDF header
            file_obj.seek(0)
            header = file_obj.read(4)
            file_obj.seek(0)
            if header != b"%PDF":
                raise ValidationError("Malformed or corrupted PDF file.")
            return "pdf"
            
        else:
            raise ValidationError("Unsupported file format.")

    @staticmethod
    def optimize_image(file_obj, module=""):
        # Determine target size based on module/content
        quality = 80
        max_dim = 2048
        
        if module == "attendance":
            quality = 75
            max_dim = 1024
        elif module == "thumbnail":
            quality = 70
            max_dim = 300
            
        file_obj.seek(0)
        img = Image.open(file_obj)
        
        # Preserve orientation from EXIF
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
            
        # Strip EXIF / metadata by creating a new image object
        data = list(img.getdata())
        clean_img = Image.new(img.mode, img.size)
        clean_img.putdata(data)
        
        # Resize if overly large
        w, h = clean_img.size
        if w > max_dim or h > max_dim:
            if w > h:
                new_w = max_dim
                new_h = int(h * (max_dim / w))
            else:
                new_h = max_dim
                new_w = int(w * (max_dim / h))
            clean_img = clean_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            
        temp_file = tempfile.NamedTemporaryFile(suffix=".webp", delete=False)
        clean_img.save(temp_file.name, format="WEBP", quality=quality)
        temp_file.seek(0)
        return temp_file

    @staticmethod
    def upload(file_obj, user=None, module="", object_type="", object_id="", is_private=False):
        # Validate file
        file_type = MediaService.validate_file(file_obj)
        
        # Compute checksum
        checksum = MediaService.compute_checksum(file_obj)
        
        # Deduplication check
        existing = MediaAsset.objects.filter(checksum=checksum, status="active", is_private=is_private).first()
        if existing:
            # Reuse existing asset reference
            return existing

        temp_path = None
        optimized_size = None
        
        try:
            if file_type == "image":
                temp_file = MediaService.optimize_image(file_obj, module=module)
                temp_path = temp_file.name
                optimized_size = os.path.getsize(temp_path)
                file_name = os.path.splitext(file_obj.name)[0] + ".webp"
                mime_type = "image/webp"
            else:
                file_name = file_obj.name
                mime_type = "application/pdf"
                
            ik = MediaService.get_ik_client()
            
            if ik:
                # Upload to ImageKit.io
                folder = f"/{module}" if module else "/general"
                try:
                    if file_type == "image":
                        with open(temp_path, "rb") as upload_file:
                            res = ik.upload_file(
                                file=upload_file,
                                file_name=file_name,
                                options={
                                    "folder": folder,
                                    "is_private_file": is_private,
                                    "use_unique_file_name": True
                                }
                            )
                    else:
                        file_obj.seek(0)
                        res = ik.upload_file(
                            file=file_obj,
                            file_name=file_name,
                            options={
                                "folder": folder,
                                "is_private_file": is_private,
                                "use_unique_file_name": True
                            }
                        )
                        
                    # Save metadata in DB
                    asset = MediaAsset.objects.create(
                        provider="imagekit",
                        provider_file_id=res.file_id,
                        original_name=file_obj.name,
                        mime_type=mime_type,
                        original_size=file_obj.size,
                        optimized_size=optimized_size or file_obj.size,
                        checksum=checksum,
                        url_path=res.url or res.path,
                        module=module,
                        object_type=object_type,
                        object_id=object_id,
                        uploaded_by=user,
                        is_private=is_private,
                        status="active"
                    )
                    return asset
                except Exception as e:
                    logger.error(f"ImageKit upload failed: {str(e)}", exc_info=True)
                    # Mark as failed/pending for retry
                    asset = MediaAsset.objects.create(
                        provider="imagekit",
                        original_name=file_obj.name,
                        mime_type=mime_type,
                        original_size=file_obj.size,
                        checksum=checksum,
                        url_path="",
                        module=module,
                        object_type=object_type,
                        object_id=object_id,
                        uploaded_by=user,
                        is_private=is_private,
                        status="failed"
                    )
                    raise ValidationError("Upload service temporarily unavailable. Stored in failed state.")
            else:
                # Fallback to local storage
                folder = f"{module}/" if module else "general/"
                if file_type == "image":
                    with open(temp_path, "rb") as f:
                        saved_path = default_storage.save(f"uploads/{folder}{file_name}", ContentFile(f.read()))
                else:
                    file_obj.seek(0)
                    saved_path = default_storage.save(f"uploads/{folder}{file_name}", file_obj)
                    
                asset = MediaAsset.objects.create(
                    provider="local",
                    provider_file_id=saved_path,
                    original_name=file_obj.name,
                    mime_type=mime_type,
                    original_size=file_obj.size,
                    optimized_size=optimized_size or file_obj.size,
                    checksum=checksum,
                    url_path=saved_path,
                    module=module,
                    object_type=object_type,
                    object_id=object_id,
                    uploaded_by=user,
                    is_private=is_private,
                    status="active"
                )
                return asset
                
        finally:
            # Clean up temp file
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    @staticmethod
    def get_secure_url(asset, user):
        if not asset.is_private:
            if asset.provider == "imagekit":
                return asset.url_path
            return settings.MEDIA_URL + asset.url_path
            
        # RBAC Check
        if not user or not user.is_authenticated:
            raise PermissionError("Authentication required.")
            
        from apps.accounts.engine import PermissionEngine
        has_manage = user.is_superuser or PermissionEngine.evaluate(user, f"{asset.module}.view").allowed
        if asset.module == "employees" and not has_manage:
            # Staff can view their own documents
            if not asset.object_id or asset.object_id != str(getattr(user, "employee_master_id", "")):
                raise PermissionError("Access denied to private document.")
                
        # Generate signed URL or secure local link
        if asset.provider == "imagekit":
            ik = MediaService.get_ik_client()
            if ik:
                try:
                    # Get path component from url_path
                    endpoint = os.environ.get("IMAGEKIT_URL_ENDPOINT") or getattr(settings, "IMAGEKIT_URL_ENDPOINT", "")
                    path = asset.url_path.replace(endpoint, "") if endpoint else asset.url_path
                    return ik.url({
                        "path": path,
                        "signed": True,
                        "expireSeconds": 3600
                    })
                except Exception as e:
                    logger.error(f"Failed to generate signed URL: {str(e)}")
            return asset.url_path
        else:
            # Local secure file view link
            from django.urls import reverse
            return reverse("audit:secure_media_view", kwargs={"pk": asset.pk})

    @staticmethod
    def delete(asset, actor=None):
        asset.status = "deleted"
        asset.deleted_at = timezone.now()
        asset.save()
        
        # Check if other active assets use the same physical file
        other_exists = MediaAsset.objects.filter(
            provider_file_id=asset.provider_file_id, 
            status="active"
        ).exclude(pk=asset.pk).exists()
        
        if not other_exists:
            if asset.provider == "imagekit":
                ik = MediaService.get_ik_client()
                if ik and asset.provider_file_id:
                    try:
                        ik.delete_file(asset.provider_file_id)
                    except Exception as e:
                        logger.error(f"Failed to delete file from ImageKit: {str(e)}")
            elif asset.provider == "local":
                if default_storage.exists(asset.provider_file_id):
                    try:
                        default_storage.delete(asset.provider_file_id)
                    except Exception as e:
                        logger.error(f"Failed to delete local file: {str(e)}")

    @staticmethod
    def cleanup_orphaned_files():
        # Clean up files in local temp directories that are not associated with active DB records
        pass
