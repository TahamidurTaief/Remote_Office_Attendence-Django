import re
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


def get_identifier_query(identifier):
    """
    Builds a Q filter that matches a user by case-insensitive email
    or by phone number including common Bangladeshi and international variations.
    """
    identifier = (identifier or '').strip()
    if not identifier:
        return Q(pk__isnull=True)

    q = Q(email__iexact=identifier) | Q(phone__iexact=identifier)
    digits = re.sub(r'\D', '', identifier)
    if digits and len(digits) >= 6:
        variations = {digits}
        if digits.startswith('880'):
            variations.add('0' + digits[3:])
            variations.add('+' + digits)
        elif digits.startswith('0'):
            variations.add('+88' + digits)
            variations.add('88' + digits)
        else:
            variations.add('+880' + digits)
            variations.add('0' + digits)

        for v in variations:
            q |= Q(phone=v)

    return q


class PhoneOrEmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        # Retrieve identifier from username, email, or phone keyword arguments
        identifier = username or kwargs.get('email') or kwargs.get('username') or kwargs.get('phone')
        if not identifier or not password:
            return None

        identifier = str(identifier).strip()
        User = get_user_model()

        # Find matching candidates and test password
        q_filter = get_identifier_query(identifier)
        candidates = User.objects.filter(q_filter)
        for user in candidates:
            if user.check_password(password):
                return user

        return None

