import math

def is_within_geofence(lat, lng, branch):
    """
    Calculate distance between employee GPS location
    and branch office location using Haversine formula.
    Returns True if within branch.radius_meters.
    """
    R = 6371000  # Earth radius in meters
    
    lat1 = math.radians(float(branch.latitude))
    lat2 = math.radians(float(lat))
    dlat = math.radians(float(lat) - float(branch.latitude))
    dlng = math.radians(float(lng) - float(branch.longitude))
    
    a = (math.sin(dlat/2)**2 + 
         math.cos(lat1) * math.cos(lat2) * 
         math.sin(dlng/2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    distance = R * c
    
    return distance <= branch.radius_meters, distance
