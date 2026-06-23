"""Config flow for Nebo.Live integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    BASE_URL,
    CONF_CITY_SLUG,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    HEADERS,
    KNOWN_CITIES,
)

_LOGGER = logging.getLogger(__name__)


async def _discover_sensors(
    session: aiohttp.ClientSession, city_slug: str
) -> list[dict[str, str]] | None:
    """Scrape the city page and discover sensors from the JS markers array.

    The page embeds:
      var markers = [["Name", lat, lng, aqi,
        "<a ... href=\\\"/en/{city}/sensors/{slug}\\\">Name</a>"], ...]

    Returns [{slug, name}, ...] or None if city not found.
    """
    url = f"{BASE_URL}/en/{city_slug}"
    try:
        async with session.get(
            url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.warning("Failed to fetch city page %s: %s", url, err)
        return None

    # Find the `var markers` JavaScript array
    markers_match = re.search(
        r"var markers\s*=\s*(\[[\s\S]*?\])\s*;",
        html,
    )
    if not markers_match:
        _LOGGER.warning("No markers array found on city page %s", url)
        return []

    markers_json = markers_match.group(1)

    sensors: list[dict[str, str]] = []
    seen_slugs: set[str] = set()

    slug_pattern = re.compile(
        r'href=\\"/en/' + re.escape(city_slug) + r'/sensors/([^\\"/]+)\\"'
    )

    for m in slug_pattern.finditer(markers_json):
        slug = m.group(1)
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        a_end = m.end()
        gt = markers_json.find(">", a_end)
        if gt == -1:
            continue
        close = markers_json.find("</a>", gt)
        if close == -1:
            continue
        name = markers_json[gt + 1 : close].strip()
        if not name:
            name = slug

        sensors.append({"slug": slug, "name": name})

    _LOGGER.debug("Discovered %d sensors in '%s'", len(sensors), city_slug)
    return sensors


# ── Config flow ─────────────────────────────────────────────────────

class NeboLiveConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Nebo.Live."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._city_slug: str = ""
        self._discovered_sensors: list[dict[str, str]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: ask for city slug, discover sensors."""
        errors = {}

        if user_input is not None:
            city_slug = user_input[CONF_CITY_SLUG].strip().lower()
            session = async_get_clientsession(self.hass)
            sensors = await _discover_sensors(session, city_slug)

            if sensors is None:
                errors[CONF_CITY_SLUG] = "city_not_found"
            elif not sensors:
                errors[CONF_CITY_SLUG] = "no_sensors"
            else:
                self._city_slug = city_slug
                self._discovered_sensors = sensors
                return await self.async_step_select_sensors()

        known_list = ", ".join(
            f"{k} ({v})" for k, v in list(KNOWN_CITIES.items())[:8]
        )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_CITY_SLUG): str,
            }),
            errors=errors,
            description_placeholders={
                "example_cities": known_list,
                "example_slug": "krs",
                "example_city": "Krasnoyarsk",
            },
        )

    async def async_step_select_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2: let user pick which sensors to track."""
        errors = {}

        if user_input is not None:
            selected_slugs: list[str] = user_input.get("selected_sensors", [])
            if not selected_slugs:
                errors["selected_sensors"] = "no_selection"
            else:
                # Filter discovered sensors to only selected ones
                selected = [
                    s for s in self._discovered_sensors
                    if s["slug"] in selected_slugs
                ]
                city_name = KNOWN_CITIES.get(
                    self._city_slug, self._city_slug.capitalize()
                )

                return self.async_create_entry(
                    title=f"Nebo.Live {city_name}",
                    data={
                        CONF_CITY_SLUG: self._city_slug,
                        "sensors": selected,
                    },
                )

        # Build multi-select with sensor addresses as labels
        options = [
            {"value": s["slug"], "label": s["name"]}
            for s in self._discovered_sensors
        ]

        data_schema = vol.Schema({
            vol.Required("selected_sensors", default=[]): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            ),
        })

        return self.async_show_form(
            step_id="select_sensors",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "city_name": KNOWN_CITIES.get(
                    self._city_slug, self._city_slug.capitalize()
                ),
                "sensor_count": str(len(self._discovered_sensors)),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> NeboLiveOptionsFlowHandler:
        return NeboLiveOptionsFlowHandler()


# ── Options flow ────────────────────────────────────────────────────

class NeboLiveOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Nebo.Live options — scan interval + sensor re-selection."""

    def __init__(self) -> None:
        """Initialize."""
        self._city_slug: str = ""
        self._discovered_sensors: list[dict[str, str]] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Options menu: choose what to configure."""
        if user_input is not None:
            if user_input.get("configure") == "sensors":
                # Re-discover and go to sensor selection
                self._city_slug = self.config_entry.data[CONF_CITY_SLUG]
                session = async_get_clientsession(self.hass)
                sensors = await _discover_sensors(session, self._city_slug)
                if sensors:
                    self._discovered_sensors = sensors
                    return await self.async_step_select_sensors()
                else:
                    return self.async_abort(reason="no_sensors")

            # Otherwise update scan interval
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

        data_schema = vol.Schema({
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=current_interval,
            ): vol.All(vol.Coerce(int), vol.Range(min=60, max=86400)),
            vol.Required("configure", default="interval"): vol.In({
                "interval": "Change update interval (seconds)",
                "sensors": "Re-select which sensors to track",
            }),
        })

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
        )

    async def async_step_select_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Re-select sensors (from options flow)."""
        errors = {}

        if user_input is not None:
            selected_slugs: list[str] = user_input.get("selected_sensors", [])
            if not selected_slugs:
                errors["selected_sensors"] = "no_selection"
            else:
                selected = [
                    s for s in self._discovered_sensors
                    if s["slug"] in selected_slugs
                ]
                # Update the config entry data with new sensor selection
                new_data = dict(self.config_entry.data)
                new_data["sensors"] = selected
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                # Trigger reload so new set of sensors takes effect
                await self.hass.config_entries.async_reload(
                    self.config_entry.entry_id
                )
                return self.async_create_entry(title="", data=self.config_entry.options)

        # Preselect current sensors
        current_slugs = {
            s["slug"]
            for s in self.config_entry.data.get("sensors", [])
        }
        default_selection = [
            s["slug"] for s in self._discovered_sensors
            if s["slug"] in current_slugs
        ]

        options = [
            {"value": s["slug"], "label": s["name"]}
            for s in self._discovered_sensors
        ]

        data_schema = vol.Schema({
            vol.Required(
                "selected_sensors",
                default=default_selection,
            ): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            ),
        })

        return self.async_show_form(
            step_id="select_sensors",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "city_name": KNOWN_CITIES.get(
                    self._city_slug, self._city_slug.capitalize()
                ),
            },
        )
