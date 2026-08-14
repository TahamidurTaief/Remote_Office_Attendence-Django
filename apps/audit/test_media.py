import os
import tempfile
from io import BytesIO
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from PIL import Image
from apps.audit.media_service import MediaService
from apps.audit.models import MediaAsset

User = get_user_model()

class MediaServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="testuser@example.com", password="Password123", role="staff")
        self.admin = User.objects.create_user(email="adminuser@example.com", password="Password123", role="admin")

    def create_dummy_image(self, format="JPEG", size=(500, 500)):
        file_obj = BytesIO()
        image = Image.new("RGB", size, color="blue")
        image.save(file_obj, format=format)
        file_obj.name = f"test.{format.lower()}"
        file_obj.seek(0)
        return SimpleUploadedFile(file_obj.name, file_obj.read(), content_type=f"image/{format.lower()}")

    def test_jpeg_to_webp_optimization(self):
        uploaded_file = self.create_dummy_image("JPEG")
        asset = MediaService.upload(uploaded_file, user=self.user, module="test_module")
        self.assertEqual(asset.mime_type, "image/webp")
        self.assertTrue(asset.url_path.endswith(".webp"))
        self.assertEqual(asset.status, "active")

    def test_png_to_webp_optimization(self):
        uploaded_file = self.create_dummy_image("PNG")
        asset = MediaService.upload(uploaded_file, user=self.user, module="test_module")
        self.assertEqual(asset.mime_type, "image/webp")
        self.assertTrue(asset.url_path.endswith(".webp"))

    def test_already_webp_optimization(self):
        uploaded_file = self.create_dummy_image("WEBP")
        asset = MediaService.upload(uploaded_file, user=self.user, module="test_module")
        self.assertEqual(asset.mime_type, "image/webp")
        self.assertTrue(asset.url_path.endswith(".webp"))

    def test_oversized_image_resize(self):
        uploaded_file = self.create_dummy_image("JPEG", size=(3000, 3000))
        temp_file = MediaService.optimize_image(uploaded_file, module="attendance")
        with Image.open(temp_file.name) as img:
            self.assertLessEqual(img.size[0], 1024)
            self.assertLessEqual(img.size[1], 1024)
        temp_file.close()
        try:
            os.remove(temp_file.name)
        except Exception:
            pass

    def test_corrupt_image_validation(self):
        corrupt_file = SimpleUploadedFile("corrupt.jpg", b"not-an-image-data", content_type="image/jpeg")
        with self.assertRaises(ValidationError):
            MediaService.upload(corrupt_file, user=self.user)

    def test_valid_pdf_validation(self):
        pdf_data = b"%PDF-1.4 dummy pdf content"
        uploaded_file = SimpleUploadedFile("test.pdf", pdf_data, content_type="application/pdf")
        asset = MediaService.upload(uploaded_file, user=self.user, module="documents")
        self.assertEqual(asset.mime_type, "application/pdf")
        self.assertEqual(asset.status, "active")

    def test_invalid_extension_rejection(self):
        malicious_file = SimpleUploadedFile("malicious.exe", b"malicious binary data", content_type="application/x-msdownload")
        with self.assertRaises(ValidationError):
            MediaService.upload(malicious_file, user=self.user)

    def test_duplicate_checksum_deduplication(self):
        uploaded_file_1 = self.create_dummy_image("JPEG")
        asset1 = MediaService.upload(uploaded_file_1, user=self.user, module="test")
        
        uploaded_file_2 = self.create_dummy_image("JPEG") # Same image content
        asset2 = MediaService.upload(uploaded_file_2, user=self.user, module="test")
        
        self.assertEqual(asset1.pk, asset2.pk) # Reused!

    def test_private_url_access_controls(self):
        uploaded_file = self.create_dummy_image("JPEG")
        asset = MediaService.upload(uploaded_file, user=self.user, module="employees", is_private=True)
        
        # Admin should access
        url = MediaService.get_secure_url(asset, self.admin)
        self.assertIsNotNone(url)
        
        # Other staff user should get permission error
        other_staff = User.objects.create_user(email="other@example.com", password="Password123", role="staff")
        with self.assertRaises(PermissionError):
            MediaService.get_secure_url(asset, other_staff)
