"""Sensor platform for Nebo.Live Air Quality.

Scrapes public sensor pages from nebo.live to get air quality metrics
without requiring an API key.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    ATTR_AQI,
    ATTR_HUMIDITY,
    ATTR_LAST_UPDATED,
    ATTR_PM01,
    ATTR_PM10,
    ATTR_PM25,
    ATTR_PRESSURE,
    ATTR_TEMPERATURE,
    BASE_URL,
    CONF_CITY_SLUG,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    HEADERS,
    METRICS,
)

_LOGGER = logging.getLogger(__name__)

# ── Sensor descriptions per metric ──────────────────────────────────

SENSOR_DESCRIPTIONS: dict[str, SensorEntityDescription] = {
    ATTR_AQI: SensorEntityDescription(
        key=ATTR_AQI,
        name="AQI",
        native_unit_of_measurement="AQI",
        icon="mdi:air-filter",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ATTR_PM25: SensorEntityDescription(
        key=ATTR_PM25,
        name="PM2.5",
        native_unit_of_measurement="µg/m³",
        icon="mdi:chemical-weapon",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ATTR_PM10: SensorEntityDescription(
        key=ATTR_PM10,
        name="PM10",
        native_unit_of_measurement="µg/m³",
        icon="mdi:blur",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ATTR_PM01: SensorEntityDescription(
        key=ATTR_PM01,
        name="PM1",
        native_unit_of_measurement="µg/m³",
        icon="mdi:blur",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ATTR_TEMPERATURE: SensorEntityDescription(
        key=ATTR_TEMPERATURE,
        name="Temperature",
        native_unit_of_measurement="°C",
        icon="mdi:thermometer",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ATTR_HUMIDITY: SensorEntityDescription(
        key=ATTR_HUMIDITY,
        name="Humidity",
        native_unit_of_measurement="%",
        icon="mdi:water-percent",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ATTR_PRESSURE: SensorEntityDescription(
        key=ATTR_PRESSURE,
        name="Pressure",
        native_unit_of_measurement="hPa",
        icon="mdi:gauge",
        state_class=SensorStateClass.MEASUREMENT,
    ),
}


# ── HTML Parser ─────────────────────────────────────────────────────

def _parse_sensor_page(html: str, slug: str) -> dict[str, Any]:
    """Parse a sensor detail page and return all metrics.

    The HTML may use single or double quotes for attributes.
    HTML entities for micro (µ = &micro;) and degree (° = &deg;) are normalized.

    Returns dict with keys from METRICS plus 'name', 'city', 'last_updated'.
    """
    data: dict[str, Any] = {}

    # Regex helper: matches either ' or " for HTML attribute quoting
    Q = "['\"]"

    # Normalize HTML entities so regex patterns can match plain characters
    html = html.replace("&deg;", "°").replace("&deg", "°")
    html = html.replace("&micro;", "µ")

    # --- Sensor name ---
    m = re.search(fr"<div class={Q}title-adr{Q}>\s*(.*?)\s*</div>", html)
    if m:
        data["name"] = m.group(1).strip()

    # --- City name ---
    m = re.search(
        fr"<div class={Q}title-city{Q}>\s*<a[^>]*>(.*?)</a>\s*</div>", html
    )
    if m:
        data["city"] = m.group(1).strip()

    # --- Last update time ---
    m = re.search(r'<time datetime="([^"]+)"', html)
    if m:
        data[ATTR_LAST_UPDATED] = m.group(1)

    # --- AQI ---
    m = re.search(
        fr"<div class={Q}value zone\d{Q}[^>]*>\s*<div class={Q}text{Q}>\s*(\d+)\s*</div>\s*<div class={Q}measure{Q}>\s*aqi",
        html,
    )
    if m:
        data[ATTR_AQI] = int(m.group(1))

    # --- PM2.5, PM1, PM10 ---
    pm_patterns = {
        ATTR_PM25: fr"PM2\.5.*?<div class={Q}text{Q}>\s*(\d+(?:\.\d+)?)\s*µg/m",
        ATTR_PM01: fr"PM1[^05].*?<div class={Q}text{Q}>\s*(\d+(?:\.\d+)?)\s*µg/m",
        ATTR_PM10: fr"PM10.*?<div class={Q}text{Q}>\s*(\d+(?:\.\d+)?)\s*µg/m",
    }
    for key, pattern in pm_patterns.items():
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                data[key] = int(m.group(1))
            except ValueError:
                try:
                    data[key] = float(m.group(1))
                except ValueError:
                    pass

    # --- Temperature, Humidity, Pressure ---
    # Temperature
    m = re.search(
        fr'alt={Q}temperature{Q}[^>]*/?>.*?</div>\s*<div class={Q}value{Q}>\s*(\d+(?:\.\d+)?)\s*°C',
        html,
        re.DOTALL,
    )
    if m:
        try:
            data[ATTR_TEMPERATURE] = float(m.group(1))
        except ValueError:
            pass

    # Humidity
    m = re.search(
        fr'alt={Q}humidity{Q}[^>]*/?>.*?</div>\s*<div class={Q}value{Q}>\s*(\d+(?:\.\d+)?)\s*%',
        html,
        re.DOTALL,
    )
    if m:
        try:
            data[ATTR_HUMIDITY] = float(m.group(1))
        except ValueError:
            pass

    # Pressure
    m = re.search(
        fr'alt={Q}pressure{Q}[^>]*/?>.*?</div>\s*<div class={Q}value{Q}>\s*(\d+(?:\.\d+)?)\s*hPa',
        html,
        re.DOTALL,
    )
    if m:
        try:
            data[ATTR_PRESSURE] = float(m.group(1))
        except ValueError:
            pass

    _LOGGER.debug("Parsed sensor '%s': %s", slug, data)
    return data


# ── Coordinator ─────────────────────────────────────────────────────

class NeboLiveCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinator that fetches data for all sensors in a city.

    Data structure: { sensor_slug: { metric_key: value, ... } }
    """

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        entry: ConfigEntry,
    ) -> None:
        """Initialize coordinator."""
        city_slug: str = entry.data[CONF_CITY_SLUG]
        sensors: list[dict[str, str]] = entry.data.get("sensors", [])
        scan_interval_seconds = entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{city_slug}",
            update_interval=timedelta(seconds=scan_interval_seconds),
        )
        self._session = session
        self._city_slug = city_slug
        self._sensors = sensors

    @property
    def city_slug(self) -> str:
        """Return the city slug."""
        return self._city_slug

    @property
    def sensors(self) -> list[dict[str, str]]:
        """Return the list of registered sensors."""
        return self._sensors

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch data for all sensors in parallel."""
        results: dict[str, dict[str, Any]] = {}

        async def fetch_one(sensor: dict[str, str]) -> tuple[str, dict[str, Any]]:
            slug = sensor["slug"]
            url = f"{BASE_URL}/en/{self._city_slug}/sensors/{slug}"
            try:
                async with self._session.get(
                    url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        data = _parse_sensor_page(html, slug)
                        if not data:
                            _LOGGER.warning(
                                "Empty data from sensor %s — page may have changed",
                                slug,
                            )
                        return slug, data
                    else:
                        _LOGGER.warning(
                            "Sensor %s returned status %s", slug, resp.status
                        )
                        return slug, {}
            except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError) as err:
                _LOGGER.warning(
                    "Failed to fetch sensor %s: %s (%s)",
                    slug, err, type(err).__name__,
                )
                return slug, {}

        # Fetch all sensors in parallel
        tasks = [fetch_one(s) for s in self._sensors]
        for task in asyncio.as_completed(tasks):
            slug, data = await task
            results[slug] = data

        if not results:
            raise UpdateFailed("No sensor data could be fetched from Nebo.Live")

        return results


# ── Sensor Entity ───────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Nebo.Live sensors from a config entry."""
    session = async_get_clientsession(hass)
    coordinator = NeboLiveCoordinator(hass, session, entry)
    await coordinator.async_config_entry_first_refresh()

    entities: list[NeboLiveSensor] = []
    for sensor_info in coordinator.sensors:
        slug = sensor_info["slug"]
        sensor_name = sensor_info["name"]
        for metric in METRICS:
            entities.append(
                NeboLiveSensor(coordinator, entry, slug, sensor_name, metric)
            )

    async_add_entities(entities)


class NeboLiveSensor(CoordinatorEntity[NeboLiveCoordinator], SensorEntity):
    """Sensor for a single metric from a Nebo.Live air quality station."""

    def __init__(
        self,
        coordinator: NeboLiveCoordinator,
        entry: ConfigEntry,
        sensor_slug: str,
        sensor_name: str,
        metric: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)

        self._sensor_slug = sensor_slug
        self._metric = metric
        self._sensor_name = sensor_name

        self.entity_description = SENSOR_DESCRIPTIONS[metric]
        self._attr_unique_id = f"{entry.entry_id}_{sensor_slug}_{metric}"
        self._attr_has_entity_name = True

        # Device grouping: one device per physical sensor location
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_{sensor_slug}")},
            "name": f"Nebo {sensor_name}",
            "manufacturer": "Nebo Devices Ltd.",
            "model": "NeboAir Sensor",
            "sw_version": "Public Web Scraper",
            "via_device": (DOMAIN, entry.entry_id),
        }

    @property
    def native_value(self) -> Any:
        """Return the sensor value from coordinator data."""
        sensor_data = self.coordinator.data.get(self._sensor_slug, {})
        return sensor_data.get(self._metric)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        sensor_data = self.coordinator.data.get(self._sensor_slug, {})

        attrs = {
            "sensor_name": self._sensor_name,
            "city_slug": self.coordinator.city_slug,
        }

        # Attach the sensor address as location info
        if "name" in sensor_data:
            attrs["address"] = sensor_data["name"]
        if "city" in sensor_data:
            attrs["city"] = sensor_data["city"]
        if ATTR_LAST_UPDATED in sensor_data:
            attrs["last_update"] = sensor_data[ATTR_LAST_UPDATED]

        return attrs

    @property
    def available(self) -> bool:
        """Return True if we have data for this sensor."""
        if not self.coordinator.last_update_success:
            return False
        sensor_data = self.coordinator.data.get(self._sensor_slug, {})
        return self._metric in sensor_data and sensor_data[self._metric] is not None
