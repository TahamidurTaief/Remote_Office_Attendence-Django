from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q

class PhoneOrEmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        # Retrieve the identifier from username or email keyword arguments
        identifier = username or kwargs.get('email') or kwargs.get('username')
        if not identifier:
            return None
        
        User = get_user_model()
        try:
            # Check both email and phone number
            user = User.objects.get(Q(email=identifier) | Q(phone=identifier))
            if user.check_password(password):
                return user
        except User.DoesNotExist:
            return None
        return None
