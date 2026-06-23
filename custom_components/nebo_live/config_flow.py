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

    # Parse sensor entries from the markers array.
    # The HTML inside uses escaped quotes: href=\"...\", class=\"...\"
    # In the Python string these are literal backslash + double-quote.
    sensors: list[dict[str, str]] = []
    seen_slugs: set[str] = set()

    # Regex: href=\" + /en/{city}/sensors/{slug} + \"
    # In raw string: href=\\"  = literal backslash + quote
    slug_pattern = re.compile(
        r'href=\\"/en/' + re.escape(city_slug) + r'/sensors/([^\\"/]+)\\"'
    )

    for m in slug_pattern.finditer(markers_json):
        slug = m.group(1)
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        # Extract sensor name between > and </a>
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


class NeboLiveConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Nebo.Live."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: ask for city slug, auto-discover all sensors."""
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
                city_name = KNOWN_CITIES.get(city_slug, city_slug.capitalize())
                _LOGGER.debug(
                    "Discovered %d sensors in '%s'",
                    len(sensors), city_slug,
                )

                return self.async_create_entry(
                    title=f"Nebo.Live {city_name}",
                    data={
                        CONF_CITY_SLUG: city_slug,
                        "sensors": sensors,
                    },
                )

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

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> NeboLiveOptionsFlowHandler:
        return NeboLiveOptionsFlowHandler()


class NeboLiveOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Nebo.Live options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=current_interval,
                ): vol.All(vol.Coerce(int), vol.Range(min=60, max=86400)),
            }),
        )
