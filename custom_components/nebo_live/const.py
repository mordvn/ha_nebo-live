"""Constants for the Nebo.Live integration."""

DOMAIN = "nebo_live"
DEFAULT_NAME = "Nebo.Live"

CONF_CITY_SLUG = "city_slug"
CONF_SENSOR_IDS = "sensor_ids"
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 600  # 10 minutes

BASE_URL = "https://nebo.live"

# Sensor metric keys
ATTR_AQI = "aqi"
ATTR_PM25 = "pm25"
ATTR_PM10 = "pm10"
ATTR_PM01 = "pm01"
ATTR_TEMPERATURE = "temperature"
ATTR_HUMIDITY = "humidity"
ATTR_PRESSURE = "pressure"
ATTR_LAST_UPDATED = "last_updated"

# All available metrics
METRICS = [
    ATTR_AQI,
    ATTR_PM25,
    ATTR_PM10,
    ATTR_PM01,
    ATTR_TEMPERATURE,
    ATTR_HUMIDITY,
    ATTR_PRESSURE,
]

# City slug → human name mapping (known cities that work with nebo.live)
# Users can also enter any custom slug not in this list
KNOWN_CITIES = {
    "krs": "Krasnoyarsk",
    "msk": "Moscow",
    "spb": "Saint Petersburg",
    "nsk": "Novosibirsk",
    "ekb": "Yekaterinburg",
    "nnv": "Nizhny Novgorod",
    "kzn": "Kazan",
    "che": "Chelyabinsk",
    "omsk": "Omsk",
    "smr": "Samara",
    "rnd": "Rostov-on-Don",
    "ufa": "Ufa",
    "krd": "Krasnodar",
    "vrn": "Voronezh",
    "per": "Perm",
    "vlg": "Volgograd",
}

# HTTP headers for scraping
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}
