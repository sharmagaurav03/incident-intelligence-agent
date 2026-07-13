"""CLI: `run` triages open incidents once, `serve` runs the webhook."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import AppConfig, load_config
from .llm import make_llm
from .pipeline import TriagePipeline
from .adapters.file_adapters import (
    FileChangeSource, FileFixPublisher, FileHistoryStore, FileLogSource,
    FileMetricSource, FileTicketSystem,
)


def build_pipeline(cfg: AppConfig) -> TriagePipeline:
    a = cfg.adapters
    s: dict[str, Any] = a.settings

    if a.tickets == "servicenow":
        from .adapters.servicenow import ServiceNowTicketSystem
        tickets = ServiceNowTicketSystem(s.get("servicenow", {}))
    else:
        tickets = FileTicketSystem(cfg.data_dir, cfg.output_dir)

    if a.logs == "gcp_logging":
        from .adapters.gcp import GcpLogSource
        logs = GcpLogSource(s.get("gcp", {}))
    else:
        logs = FileLogSource(cfg.data_dir)

    if a.metrics == "gcp_monitoring":
        from .adapters.gcp import GcpMetricSource
        metrics = GcpMetricSource(s.get("gcp", {}))
    else:
        metrics = FileMetricSource(cfg.data_dir)

    changes = FileChangeSource(cfg.data_dir)
    history = FileHistoryStore(cfg.data_dir)  # TODO pgvector once we have volume

    if a.fix_publisher == "github":
        from .adapters.github_fix import GitHubFixPublisher
        fixes = GitHubFixPublisher(s.get("github", {}))
    else:
        fixes = FileFixPublisher(cfg.output_dir, cfg.source_dir)

    return TriagePipeline(
        cfg, tickets, logs, metrics, changes, history, fixes, make_llm(cfg.llm)
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="triage_agent")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="triage all open incidents once")
    run_p.add_argument("--config", default=None)
    run_p.add_argument("--incident", default=None, help="triage one INC number")

    serve_p = sub.add_parser("serve", help="run the webhook trigger server")
    serve_p.add_argument("--config", default=None)

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    pipeline = build_pipeline(cfg)

    if args.command == "run":
        for r in pipeline.run(only_number=args.incident):
            if r.assist_only:
                print(f"{r.incident_number}: Sev1 assist-only")
            else:
                dupes = f" (+dupes {r.duplicates})" if r.duplicates else ""
                fix = f"  fix: {r.fix_ref}" if r.fix_ref else ""
                print(
                    f"{r.incident_number}{dupes}: {r.category} "
                    f"@ {r.confidence:.2f}  redactions={r.redactions} "
                    f"({r.elapsed_s}s){fix}"
                )
        u = pipeline.llm.usage
        print(
            f"LLM usage: {u.calls} calls, "
            f"{u.input_tokens} in / {u.output_tokens} out tokens, "
            f"{u.errors} errors"
        )
        return 0

    if args.command == "serve":
        from .webhook_server import serve

        def triage_by_sys_id(sys_id):
            inc = pipeline.tickets.get_incident(sys_id)
            if inc is None:
                print(f"trigger for unknown sys_id={sys_id}; ignoring")
                return
            print(json.dumps(pipeline.triage_cluster([inc]).__dict__))

        serve(cfg, triage_by_sys_id)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
