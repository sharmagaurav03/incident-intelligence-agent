"""GCP Cloud Logging + Monitoring over plain REST.

Skipped the google-cloud SDKs on purpose -- two endpoints don't justify
the dependency tree. Token comes from an env var locally
(gcloud auth print-access-token) or the metadata server when running on
GCP. Filters are config templates because everyone labels their
resources differently.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests

from .base import LogSource, MetricSource

_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)


def make_token_provider(settings: dict[str, Any]) -> Callable[[], str]:
    env_name = settings.get("access_token_env", "GCP_TOKEN")

    def provider() -> str:
        token = os.environ.get(env_name)
        if token:
            return token
        resp = requests.get(
            _METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"}, timeout=5
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    return provider


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GcpLogSource(LogSource):
    def __init__(self, settings: dict[str, Any]):
        self.project = settings["project_id"]
        self.filter_template = settings.get(
            "filter_template",
            'resource.labels.container_name="{service}" '
            'AND severity>=ERROR AND timestamp>="{start}"',
        )
        self.page_size = int(settings.get("page_size", 50))
        self.timeout = int(settings.get("timeout_seconds", 30))
        self._token = make_token_provider(settings)

    def recent_errors(self, service, minutes=60):
        start = _rfc3339(datetime.now(timezone.utc) - timedelta(minutes=minutes))
        body = {
            "resourceNames": [f"projects/{self.project}"],
            "filter": self.filter_template.format(service=service, start=start),
            "orderBy": "timestamp desc",
            "pageSize": self.page_size,
        }
        resp = requests.post(
            "https://logging.googleapis.com/v2/entries:list",
            json=body,
            headers={"Authorization": f"Bearer {self._token()}"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        lines = []
        for entry in resp.json().get("entries", []):
            payload = (
                entry.get("textPayload")
                or str(entry.get("jsonPayload", ""))
                or str(entry.get("protoPayload", ""))
            )
            lines.append(f"{entry.get('timestamp','?')} {entry.get('severity','?')} {payload}")
        return "\n".join(lines)


class GcpMetricSource(MetricSource):
    def __init__(self, settings: dict[str, Any]):
        self.project = settings["project_id"]
        self.metric_filters = settings.get(
            "metric_filters",
            [
                'metric.type="run.googleapis.com/request_count" '
                'AND resource.labels.service_name="{service}"',
            ],
        )
        self.timeout = int(settings.get("timeout_seconds", 30))
        self._token = make_token_provider(settings)

    def recent_series(self, service, minutes=60):
        now = datetime.now(timezone.utc)
        params_base = {
            "interval.endTime": _rfc3339(now),
            "interval.startTime": _rfc3339(now - timedelta(minutes=minutes)),
        }
        out = []
        for template in self.metric_filters:
            params = dict(params_base, filter=template.format(service=service))
            resp = requests.get(
                f"https://monitoring.googleapis.com/v3/projects/{self.project}/timeSeries",
                params=params,
                headers={"Authorization": f"Bearer {self._token()}"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            for series in resp.json().get("timeSeries", []):
                mtype = series.get("metric", {}).get("type", "?")
                for point in series.get("points", [])[:20]:
                    val = point.get("value", {})
                    scalar = (
                        val.get("doubleValue")
                        or val.get("int64Value")
                        or val.get("distributionValue", {}).get("mean")
                    )
                    end = point.get("interval", {}).get("endTime", "?")
                    out.append(f"{end} {mtype}={scalar}")
        return "\n".join(out)
