"""Shared geodesy primitives for the build pipeline.

Single source of truth for the Earth-radius constant and the
great-circle distance formula. Previously duplicated across
fetch_pois.py (POI dedup) and compute_route_stats.py (route
distance + elevation sampling); consolidating here means tuning
the constant in one place if we ever switch to a more accurate
ellipsoidal model.

The runtime (templates/app.js) carries its own haversine because
JavaScript and Python don't share modules. It uses the WGS84
equatorial radius (6378137 m) rather than the mean radius (6371000)
this module uses; the two differ by ~0.1%, which is below the
accuracy floor of any trail-scale measurement we care about.
"""

import math


# Mean Earth radius in metres. Matches the convention used by every
# Python script in the build pipeline. Off by ~0.1% from the WGS84
# equatorial radius (templates/app.js:EARTH_RADIUS_M = 6378137) — the
# inconsistency is intentional and irrelevant at trail scales.
EARTH_R_M = 6371000.0


def haversine_m(lng1, lat1, lng2, lat2):
    """Great-circle distance between two (lng, lat) points, in metres.

    Standard haversine formula; accuracy ~0.5% over distances up to
    a few thousand km, well within the trail-scale tolerance the
    framework cares about.
    """
    rlat1 = math.radians(lat1)
    rlat2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2)
    return EARTH_R_M * 2 * math.asin(math.sqrt(a))
