"""Publish fix suggestions to GitHub.

PR mode: branch + commit a review file under triage-suggestions/ + open a
DRAFT pr. We deliberately don't touch source files -- a machine-suggested
patch might not apply cleanly and the suggestion doc is the actual
deliverable. Issue mode exists for repos where the bot shouldn't have
contents:write.
"""
from __future__ import annotations

import base64
import os
from typing import Any

import requests

from .base import FixPublisher

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class GitHubFixPublisher(FixPublisher):
    def __init__(self, settings: dict[str, Any]):
        self.owner = settings["owner"]
        self.repo = settings["repo"]
        self.mode = settings.get("mode", "pr")  # pr | issue
        token = os.environ.get(settings.get("token_env", "GITHUB_TOKEN"))
        if not token:
            raise GitHubError("GitHub token missing: set the env var named by token_env")
        self.timeout = int(settings.get("timeout_seconds", 30))
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def _check(self, resp):
        if not resp.ok:
            raise GitHubError(f"GitHub {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def _repo_url(self, path=""):
        return f"{API}/repos/{self.owner}/{self.repo}{path}"

    def publish(self, incident, fix_hint, narrative):
        number = incident.get("number", "INC")
        title = f"[triage-agent] Fix suggestion for {number}: {fix_hint['problem'][:80]}"
        body = (
            f"Automated fix suggestion for incident **{number}**.\n\n"
            f"**Problem:** {fix_hint['problem']}\n\n"
            f"**Diagnosis:** {narrative}\n\n"
            f"**Suggested patch (needs human review):**\n\n"
            f"```\n{fix_hint['suggested_patch']}\n```\n\n"
            f"_Opened automatically; never merge without engineer review._"
        )
        if self.mode == "issue":
            data = self._check(
                self.session.post(
                    self._repo_url("/issues"),
                    json={"title": title, "body": body},
                    timeout=self.timeout,
                )
            )
            return data["html_url"]

        repo = self._check(self.session.get(self._repo_url(), timeout=self.timeout))
        base_branch = repo["default_branch"]
        base_ref = self._check(
            self.session.get(
                self._repo_url(f"/git/ref/heads/{base_branch}"), timeout=self.timeout
            )
        )
        branch = f"triage-agent/{number.lower()}"
        resp = self.session.post(
            self._repo_url("/git/refs"),
            json={"ref": f"refs/heads/{branch}", "sha": base_ref["object"]["sha"]},
            timeout=self.timeout,
        )
        if resp.status_code == 422 and "already exists" in resp.text.lower():
            pass  # rerun for the same incident, branch is already there
        elif not resp.ok:
            raise GitHubError(f"GitHub {resp.status_code}: {resp.text[:300]}")

        path = f"triage-suggestions/{number}.md"
        put_body: dict[str, Any] = {
            "message": f"triage-agent: fix suggestion for {number}",
            "content": base64.b64encode(body.encode()).decode(),
            "branch": branch,
        }
        existing = self.session.get(
            self._repo_url(f"/contents/{path}"),
            params={"ref": branch},
            timeout=self.timeout,
        )
        if existing.ok:
            put_body["sha"] = existing.json()["sha"]  # updates need the blob sha
        self._check(
            self.session.put(
                self._repo_url(f"/contents/{path}"), json=put_body, timeout=self.timeout
            )
        )
        pr = self.session.post(
            self._repo_url("/pulls"),
            json={
                "title": title,
                "head": branch,
                "base": base_branch,
                "body": body,
                "draft": True,
            },
            timeout=self.timeout,
        )
        if pr.status_code == 422 and "already exists" in pr.text.lower():
            return f"https://github.com/{self.owner}/{self.repo}/tree/{branch}"
        return self._check(pr)["html_url"]
