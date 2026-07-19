import datetime
from django.utils import timezone

def parse_and_validate_client_time(client_time_str):
    """
    Parses an ISO8601 datetime string and validates it against the trust rules:
    1. client_time <= server_now (no future timestamps)
    2. abs(client_time - server_now) <= 24 hours (covers realistic offline gap)
    """
    if not client_time_str:
        return None
    try:
        # datetime.fromisoformat handles standard ISO8601 strings (including 'Z' in Python 3.11+)
        # For compatibility with potential format variations:
        if isinstance(client_time_str, str) and client_time_str.endswith('Z'):
            client_time_str = client_time_str[:-1] + '+00:00'
        dt = datetime.datetime.fromisoformat(client_time_str)
        # Ensure it is timezone-aware
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        
        now = timezone.now()
        # No future timestamps allowed (client_time <= now)
        if dt > now:
            return None
        
        # Difference must be <= 24 hours
        if (now - dt).total_seconds() > 86400:
            return None
            
        return dt
    except Exception:
        pass
    return None
