import urllib.request
import json
from datetime import datetime, timezone, timedelta
from sgp4.api import Satrec, WGS72, jday
import math
from pathlib import Path

EARTH_RADIUS_KM = 6371.0
EARTH_RADIUS_UNITS = 2.0
CACHE_FILE = Path("sat_cache.json")
CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=galileo&FORMAT=json"


def _build_satrec(record):
    epoch = datetime.fromisoformat(record["EPOCH"].replace("Z", "+00:00")).astimezone(
        timezone.utc
    )
    epoch0 = datetime(1949, 12, 31, tzinfo=timezone.utc)
    epoch_days = (epoch - epoch0).total_seconds() / 86400.0

    rev_per_day = record["MEAN_MOTION"]
    no_kozai = rev_per_day * 2.0 * math.pi / 1440.0
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

    if record.get("kind") == "custom":
        return _custom_orbit_to_pos(record, dt_utc)

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

        modified = data[index]
        modified["team"] = "enemy"

        return modified

    except Exception as e:
        print(f"[WARN] Live fetch failed: {e}")
        if CACHE_FILE.exists():
            print("[INFO] Using cached satellite data.")
            modified = json.loads(CACHE_FILE.read_text())
            modified["team"] = "enemy"

            return modified

        raise RuntimeError(
            "No satellite data available (live fetch failed and cache missing)."
        )


def sample_orbit(record, samples=180):
    if record.get("kind") == "custom":
        altitude_km = float(record.get("altitude_km", 550.0))
        r_km = EARTH_RADIUS_KM + altitude_km
        mu = 398600.4418
        n = math.sqrt(mu / (r_km**3))
        period_seconds = (2.0 * math.pi) / n

        now = datetime.now(timezone.utc)
        points = []
        for i in range(samples + 1):
            t = now + timedelta(seconds=(period_seconds * i) / samples)
            points.append(sat_record_to_pos(record, t))
        return points

    mean_motion = record["MEAN_MOTION"]
    period_minutes = 1440.0 / mean_motion

    now = datetime.now(timezone.utc)
    points = []
    for i in range(samples + 1):
        minutes = (period_minutes * i) / samples
        t = now + timedelta(minutes=minutes)
        points.append(sat_record_to_pos(record, t))
    return points


def _custom_orbit_to_pos(record: dict, dt_utc: datetime) -> tuple[float, float, float]:
    altitude_km = float(record.get("altitude_km", 550.0))
    incl = math.radians(float(record.get("inclination_deg", 53.0)))
    raan = math.radians(float(record.get("raan_deg", 0.0)))
    phase0 = math.radians(float(record.get("phase_deg", 0.0)))

    epoch_str = record.get("epoch_utc")
    if epoch_str:
        epoch = datetime.fromisoformat(epoch_str.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    else:
        epoch = datetime.now(timezone.utc)

    r_km = EARTH_RADIUS_KM + altitude_km

    mu = 398600.4418
    n = math.sqrt(mu / (r_km**3))

    dt = (dt_utc - epoch).total_seconds()
    theta = phase0 + n * dt

    x_orb = r_km * math.cos(theta)
    y_orb = r_km * math.sin(theta)
    z_orb = 0.0

    x1 = x_orb
    y1 = y_orb * math.cos(incl) - z_orb * math.sin(incl)
    z1 = y_orb * math.sin(incl) + z_orb * math.cos(incl)

    x = x1 * math.cos(raan) - y1 * math.sin(raan)
    y = x1 * math.sin(raan) + y1 * math.cos(raan)
    z = z1

    scale = EARTH_RADIUS_UNITS / EARTH_RADIUS_KM
    return x * scale, y * scale, z * scale
