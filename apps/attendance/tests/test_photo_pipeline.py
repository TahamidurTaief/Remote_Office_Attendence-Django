import io
import tempfile
import uuid
from PIL import Image
from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from apps.employees.models import EmployeeProfile
from apps.branches.models import Branch
from apps.attendance.models import Attendance, AttendancePolicy
from apps.attendance.transaction_service import AttendanceTransactionService, AttendanceTransactionError, validate_uploaded_photo

User = get_user_model()


def make_test_image(width=1280, height=960, fmt='JPEG', color='green'):
    buf = io.BytesIO()
    img = Image.new('RGB', (width, height), color=color)
    img.save(buf, format=fmt)
    buf.seek(0)
    ext = 'jpg' if fmt.upper() == 'JPEG' else fmt.lower()
    content_type = f'image/{ext}' if ext != 'jpg' else 'image/jpeg'
    return SimpleUploadedFile(f'test_{width}x{height}.{ext}', buf.read(), content_type=content_type)


class PhotoPipelineServerValidationTests(TestCase):
    """
    Validates server-side image validation, ImageKit processing,
    MIME type spoofing rejection, and offline bulk sync photo decoders.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_media = tempfile.TemporaryDirectory()
        cls._media_override = override_settings(MEDIA_ROOT=cls._temp_media.name)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        cls._temp_media.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            email='phototester@example.com',
            password='TestPassword123!'
        )
        self.branch = Branch.objects.create(
            name='Dhaka HQ',
            address='Dhaka, Bangladesh',
            latitude=23.8103,
            longitude=90.4125
        )
        self.employee = EmployeeProfile.objects.create(
            user=self.user,
            employee_id='EMP-PHOTO-1',
            full_name='Photo Tester',
            phone='01700000099',
            joined_date='2026-01-01',
            branch=self.branch,
            is_active=True
        )
        self.policy = AttendancePolicy.objects.create(
            branch=self.branch,
            photo_required=True,
            geofencing_policy='disabled'
        )

    def test_validate_uploaded_photo_valid_jpeg(self):
        photo = make_test_image(1280, 960, 'JPEG')
        # Should execute cleanly without exception
        validate_uploaded_photo(photo)
        self.assertEqual(photo.tell(), 0)

    def test_validate_uploaded_photo_valid_png_and_webp(self):
        png_photo = make_test_image(640, 480, 'PNG')
        validate_uploaded_photo(png_photo)

        webp_photo = make_test_image(640, 480, 'WEBP')
        validate_uploaded_photo(webp_photo)

    def test_reject_client_spoofed_mime_type(self):
        """A text/script file with spoofed image/jpeg content_type must be rejected."""
        fake_photo = SimpleUploadedFile(
            'malicious.jpg',
            b'<!DOCTYPE html><html><body><h1>Not an image</h1></body></html>',
            content_type='image/jpeg'
        )
        with self.assertRaises(AttendanceTransactionError) as ctx:
            validate_uploaded_photo(fake_photo)
        self.assertIn('invalid or corrupted image', str(ctx.exception).lower())

    def test_reject_photo_exceeding_10mb(self):
        """Files larger than 10MB must be rejected."""
        large_photo = SimpleUploadedFile('huge.jpg', b'0' * (10 * 1024 * 1024 + 1), content_type='image/jpeg')
        with self.assertRaises(AttendanceTransactionError) as ctx:
            validate_uploaded_photo(large_photo)
        self.assertIn('too large', str(ctx.exception).lower())

    def test_enforce_required_photo_policy(self):
        """When photo_required=True, check-in without photo raises AttendanceTransactionError."""
        self.policy.photo_required = True
        self.policy.save()

        data = {
            'latitude': 23.8103,
            'longitude': 90.4125,
            'sync_uuid': str(uuid.uuid4())
        }
        with self.assertRaises(AttendanceTransactionError) as ctx:
            AttendanceTransactionService.check_in(self.user, data, photo=None, validate_photo=True)
        self.assertIn('photo is required', str(ctx.exception).lower())

    def test_imagekit_processing_creates_valid_record(self):
        """Uploaded 1280x960 JPEG photo is processed by Django ImageKit and saved to disk."""
        photo = make_test_image(1280, 960, 'JPEG', color='blue')
        sync_uuid = str(uuid.uuid4())
        data = {
            'latitude': 23.8103,
            'longitude': 90.4125,
            'sync_uuid': sync_uuid,
            'client_event_time': '2026-10-26T08:00:00Z'
        }
        res = AttendanceTransactionService.check_in(self.user, data, photo=photo, validate_photo=True)
        self.assertTrue(res['success'])

        att = Attendance.objects.get(sync_uuid=sync_uuid)
        self.assertTrue(att.photo)
        self.assertTrue(att.photo.path)

        # ImageKit pipeline formats image as WEBP with max dimension <= 1280
        with Image.open(att.photo.path) as saved_img:
            self.assertEqual(saved_img.format, 'WEBP')
            self.assertLessEqual(max(saved_img.width, saved_img.height), 1280)

    def test_bulk_sync_with_offline_photo_decoding(self):
        """Verifies bulk_sync decodes base64 prepared photo and creates Attendance record."""
        client = Client()
        client.force_login(self.user)

        photo = make_test_image(1280, 720, 'JPEG', color='red')
        import base64
        photo_b64 = 'data:image/jpeg;base64,' + base64.b64encode(photo.read()).decode('ascii')
        sync_uuid = str(uuid.uuid4())

        payload = {
            'items': [{
                'action': 'check_in',
                'uuid': sync_uuid,
                'payload': {
                    'latitude': 23.8103,
                    'longitude': 90.4125,
                    'accuracy': 10,
                    'address': 'Dhaka Office',
                    'sync_uuid': sync_uuid,
                    'client_event_time': '2026-10-26T08:30:00Z',
                    'photo': photo_b64
                }
            }]
        }

        response = client.post('/attendance/bulk-sync/', data=payload, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertEqual(res_data['results'][0]['status'], 'success')

        att = Attendance.objects.get(sync_uuid=sync_uuid)
        self.assertTrue(att.photo)
