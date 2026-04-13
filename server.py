import urllib.request
import json
from datetime import datetime, timezone, timedelta
from sgp4.api import Satrec, WGS72, jday
import math
from pathlib import Path

EARTH_RADIUS_KM = 6371.0
EARTH_RADIUS_UNITS = 2.0
CACHE_FILE = Path("sat_cache.json")
CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=json"


def _build_satrec(record):
    epoch = datetime.fromisoformat(record["EPOCH"].replace("Z", "+00:00")).astimezone(
        timezone.utc
    )
    epoch0 = datetime(1949, 12, 31, tzinfo=timezone.utc)
    epoch_days = (epoch - epoch0).total_seconds() / 86400.0

    rev_per_day = record["MEAN_MOTION"]
    no_kozai = rev_per_day * 2.0 * math.pi / 1440.0  # rad/min
    ndot = record["MEAN_MOTION_DOT"] * 2.0 * math.pi / (1440.0**2)
    nddot = record["MEAN_MOTION_DDOT"] * 2.0 * math.pi / (1440.0**3)

    sat = Satrec()
    sat.sgp4init(
        WGS72,
        "i",
        record["NORAD_CAT_ID"],
        epoch_days,
        record["BSTAR"],
        ndot,
        nddot,
        record["ECCENTRICITY"],
        math.radians(record["ARG_OF_PERICENTER"]),
        math.radians(record["INCLINATION"]),
        math.radians(record["MEAN_ANOMALY"]),
        no_kozai,
        math.radians(record["RA_OF_ASC_NODE"]),
    )

    return sat


def sat_record_to_pos(record, dt_utc=None):
    if dt_utc is None:
        dt_utc = datetime.now(timezone.utc)

    sat = _build_satrec(record)
    jd, fr = jday(
        dt_utc.year,
        dt_utc.month,
        dt_utc.day,
        dt_utc.hour,
        dt_utc.minute,
        dt_utc.second + dt_utc.microsecond / 1e6,
    )
    e, r, _ = sat.sgp4(jd, fr)
    if e != 0:
        raise RuntimeError(f"SGP4 error code: {e}")

    scale = EARTH_RADIUS_UNITS / EARTH_RADIUS_KM
    return r[0] * scale, r[1] * scale, r[2] * scale


def get_sat_record(index=0, timeout=5.0):
    """
    Try to download live data. If that fails, use cached data.
    """
    try:
        res = urllib.request.urlopen(CELESTRAK_URL, timeout=timeout).read()
        res_json = res.decode("utf8").replace("'", '"')
        data = json.loads(res_json)

        CACHE_FILE.write_text(json.dumps(data[index], indent=2))
        return data[index]

    except Exception as e:
        print(f"[WARN] Live fetch failed: {e}")
        if CACHE_FILE.exists():
            print("[INFO] Using cached satellite data.")
            return json.loads(CACHE_FILE.read_text())

        raise RuntimeError(
            "No satellite data available (live fetch failed and cache missing)."
        )


def sample_orbit(record, samples=180):
    mean_motion = record["MEAN_MOTION"]  # rev/day
    period_minutes = 1440.0 / mean_motion

    now = datetime.now(timezone.utc)
    points = []
    for i in range(samples + 1):
        minutes = (period_minutes * i) / samples
        t = now + timedelta(minutes=minutes)
        points.append(sat_record_to_pos(record, t))
    return points
