"""Config flow: host + port, including a real connection test."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant import config_entries

from .client import ItoLevaClient
from .const import CONNECT_TIMEOUT, DEFAULT_HOST, DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

SCHEMA = vol.Schema(
    {
        vol.Required("host", default=DEFAULT_HOST): str,
        vol.Required("port", default=DEFAULT_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
    }
)


class ItoLevaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the single-step host/port setup."""

    VERSION = 2  # v2 = stable entity IDs; see async_migrate_entry

    async def async_step_user(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input["host"].strip()
            port = user_input["port"]
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            client = ItoLevaClient(host, port)
            await client.async_start()
            try:
                connected = await client.wait_ready(CONNECT_TIMEOUT)
            finally:
                await client.async_stop()

            if connected:
                return self.async_create_entry(
                    title=f"ITO Leva! ({host})",
                    data={"host": host, "port": port},
                )
            _LOGGER.warning("Config flow: %s:%s did not answer", host, port)
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user", data_schema=SCHEMA, errors=errors
        )
