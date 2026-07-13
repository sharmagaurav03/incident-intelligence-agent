"""ServiceNow via the Table API.

GET  /api/now/table/incident?sysparm_query=...
GET  /api/now/table/incident/{sys_id}
PATCH /api/now/table/incident/{sys_id}

Basic auth with a service account; creds come from env vars named in the
adapter settings, never from yaml. The u_agent_* custom fields won't
exist on a stock instance -- set write_custom_fields: false until an
admin adds them and we fold the values into work notes instead.
"""
from __future__ import annotations

import os
from typing import Any

import requests

from .base import TicketSystem


class ServiceNowError(RuntimeError):
    pass


class ServiceNowTicketSystem(TicketSystem):
    def __init__(self, settings: dict[str, Any]):
        self.base = settings["instance_url"].rstrip("/")
        user = os.environ.get(settings.get("username_env", "SN_USERNAME"))
        pwd = os.environ.get(settings.get("password_env", "SN_PASSWORD"))
        if not user or not pwd:
            raise ServiceNowError(
                "ServiceNow credentials missing: set the env vars named by "
                "username_env/password_env in adapter settings"
            )
        self.query = settings.get("query", "state=1")
        self.limit = int(settings.get("limit", 50))
        self.write_custom_fields = bool(settings.get("write_custom_fields", True))
        self.timeout = int(settings.get("timeout_seconds", 30))
        self.session = requests.Session()
        self.session.auth = (user, pwd)
        self.session.headers.update(
            {"Accept": "application/json", "Content-Type": "application/json"}
        )

    def _url(self, path):
        return f"{self.base}/api/now/table/{path}"

    def _check(self, resp):
        if resp.status_code == 401:
            raise ServiceNowError("ServiceNow auth failed (401): check credentials")
        if resp.status_code == 403:
            raise ServiceNowError("ServiceNow forbidden (403): check ACLs/roles")
        if not resp.ok:
            raise ServiceNowError(f"ServiceNow error {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def open_incidents(self):
        params = {
            "sysparm_query": self.query,
            "sysparm_limit": str(self.limit),
            "sysparm_display_value": "false",
        }
        data = self._check(
            self.session.get(self._url("incident"), params=params, timeout=self.timeout)
        )
        return data.get("result", [])

    def get_incident(self, sys_id):
        resp = self.session.get(self._url(f"incident/{sys_id}"), timeout=self.timeout)
        if resp.status_code == 404:
            return None
        return self._check(resp).get("result")

    def add_work_note(self, sys_id, note):
        self._check(
            self.session.patch(
                self._url(f"incident/{sys_id}"),
                json={"work_notes": note},
                timeout=self.timeout,
            )
        )

    def set_fields(self, sys_id, fields):
        if not self.write_custom_fields:
            note = "Agent fields: " + ", ".join(f"{k}={v}" for k, v in fields.items())
            self.add_work_note(sys_id, note)
            return
        self._check(
            self.session.patch(
                self._url(f"incident/{sys_id}"), json=fields, timeout=self.timeout
            )
        )
