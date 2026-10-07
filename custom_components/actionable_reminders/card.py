"""Serve the reminders Lovelace card from the integration itself.

Registering the static path and adding it as an extra frontend module means
the card works as soon as the integration loads — no manual dashboard resource
to add, and none to forget to update. The integration version rides on the URL
so a new release busts the browser cache.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_FILENAME = "actionable-reminders-card.js"
CARD_URL_BASE = f"/{DOMAIN}_frontend"
_CARD_PATH = Path(__file__).parent / "frontend" / CARD_FILENAME


async def async_register_card(hass: HomeAssistant) -> None:
    """Expose the card JS and load it on every dashboard. Never raises."""
    if hass.data.setdefault(DOMAIN, {}).get("_card_registered"):
        return
    try:
        # Imported here: http/frontend are runtime dependencies only, and the
        # test harness stubs neither.
        from homeassistant.components.frontend import add_extra_js_url
        from homeassistant.components.http import StaticPathConfig
        from homeassistant.loader import async_get_integration

        integration = await async_get_integration(hass, DOMAIN)
        version = str(integration.version or "0")
        await hass.http.async_register_static_paths([
            StaticPathConfig(f"{CARD_URL_BASE}/{CARD_FILENAME}", str(_CARD_PATH), True)
        ])
        add_extra_js_url(hass, f"{CARD_URL_BASE}/{CARD_FILENAME}?v={version}")
        hass.data[DOMAIN]["_card_registered"] = True
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Reminders card not registered (add it as a resource by hand): %s", err)
