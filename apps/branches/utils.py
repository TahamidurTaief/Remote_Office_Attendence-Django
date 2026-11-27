import math

def haversine(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance between two points
    on the earth (specified in decimal degrees).
    Returns distance in meters, or None if coordinates are missing/invalid/non-finite.
    """
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return None
    try:
        lat1 = float(lat1)
        lon1 = float(lon1)
        lat2 = float(lat2)
        lon2 = float(lon2)
    except (TypeError, ValueError):
        return None

    if not (math.isfinite(lat1) and math.isfinite(lon1) and math.isfinite(lat2) and math.isfinite(lon2)):
        return None

    if (lat1 == 0.0 and lon1 == 0.0) or (lat2 == 0.0 and lon2 == 0.0):
        return None

    R = 6371000  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) *
         math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return R * c


def is_within_geofence(lat, lng, branch, radius_meters=None):
    """
    Calculate distance between employee GPS location
    and branch office location using Haversine formula.
    Returns (is_within: bool, distance: Optional[float]).
    If branch or coordinates are missing/invalid, returns (False, None).
    """
    if not branch or getattr(branch, 'latitude', None) is None or getattr(branch, 'longitude', None) is None:
        return False, None
    if lat is None or lng is None:
        return False, None

    distance = haversine(branch.latitude, branch.longitude, lat, lng)
    if distance is None:
        return False, None

    radius = radius_meters or getattr(branch, 'radius_meters', 100) or 100
    return distance <= radius, distance


def get_cached_branches():
    from django.core.cache import cache
    from .models import Branch
    branches = cache.get('active_branches')
    if branches is None:
        branches = list(Branch.objects.all().order_by('name'))
        cache.set('active_branches', branches, 300)
    return branches
