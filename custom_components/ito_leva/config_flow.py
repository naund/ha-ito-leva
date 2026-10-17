"""Config flow: only host + port (Ranchilio.md Phase 1 requirement)."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries

from .const import DEFAULT_HOST, DEFAULT_PORT, DOMAIN


class ItoLevaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the single-step host/port setup."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        if user_input is not None:
            host = user_input["host"].strip()
            port = user_input["port"]
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"ITO Leva! ({host})",
                data={"host": host, "port": port},
            )
        schema = vol.Schema(
            {
                vol.Required("host", default=DEFAULT_HOST): str,
                vol.Required("port", default=DEFAULT_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)
