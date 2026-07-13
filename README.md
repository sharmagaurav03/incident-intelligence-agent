# incident-intelligence-agent

First-pass triage for incident tickets, done by a machine so the on-call
doesn't have to. When a ticket comes in, the agent groups duplicate reports,
pulls evidence (logs, metrics, recent deploys and flag changes), strips PII
out of it, asks an LLM to classify the root cause, and writes the diagnosis
back on the ticket as a work note. If it's very sure the problem is a code
defect, it opens a draft PR with a suggested fix. It never merges anything.

Every external system sits behind an adapter, so the same pipeline runs
completely offline against files in `data/` or against the real thing
(ServiceNow, GCP logging/monitoring, GitHub, Anthropic API).

## What it will and won't do

The model picks exactly one category:

```
infrastructure  code-defect  dependency-api-failure  change-induced
config          data         capacity                unknown
```

Anything malformed or off-menu gets clamped to `unknown`. On top of that,
three hard gates, enforced in code rather than in the prompt:

- Severity 1 is assist-only. We never auto-classify a Sev1; humans own it.
- Confidence below 0.5 is forced to `unknown` and routed to a human.
- Fix suggestions require category `code-defect`, confidence >= 0.8, and a
  concrete file hint. Even then they only ever land as draft PRs on a
  branch. The agent doesn't edit source files and doesn't merge.

## Quick start

Mock mode, no credentials needed:

```bash
python3 -m venv venv && . venv/bin/activate
pip install -r requirements-dev.txt
make test          # or: python -m pytest -q
make run           # triages the sample incidents in data/
```

You should see INC1001 come back as `code-defect @ 0.90` (INC1002 gets
deduplicated into it, fix suggestion written to `out/`) and INC1003 as
`dependency-api-failure @ 0.85`.

Real LLM, everything else still mocked:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
TRIAGE__llm__mode=real PYTHONPATH=src python -m triage_agent run \
    --config config/default.yaml --incident INC1003
```

## Configuration

Behavior lives in yaml, one file per environment, with typed
`TRIAGE__section__key` env overrides on top:

```bash
TRIAGE__gates__fix_min_confidence=0.9
TRIAGE__llm__mode=real
```

Secrets never go in the yaml. The config holds the *names* of env vars,
and you export the values: `SN_USERNAME`, `SN_PASSWORD`, `GITHUB_TOKEN`,
`TRIAGE_WEBHOOK_SECRET`, `ANTHROPIC_API_KEY`, and for local GCP testing
`GCP_TOKEN=$(gcloud auth print-access-token)` -- on GKE or Cloud Run the
metadata server handles tokens on its own.

## Production setup

Copy `config/production.yaml.example` to `config/production.yaml` and fill
in your instance URL / project id / repo. Per system:

- **ServiceNow**: service account with read on incident and write on work
  notes. The `u_agent_*` custom fields don't exist on a stock instance;
  leave `write_custom_fields: false` until an admin adds them and the agent
  folds those values into work notes instead. Install
  `scripts/servicenow_business_rule.js` as an after-insert async Business
  Rule on incident and set the `triage.agent.url` / `triage.agent.secret`
  properties it reads.
- **GCP**: `roles/logging.viewer` and `roles/monitoring.viewer` for the
  agent's service account. Log and metric filters are config templates
  because label schemes vary; adjust `resource.labels.*` to whatever you
  use.
- **GitHub**: fine-grained token scoped to one sandbox repo. PR mode needs
  contents:write and pull_requests:write; issue mode only needs
  issues:write. Suggestions go to a `triage-agent/incNNNN` branch as a file
  under `triage-suggestions/` and open as a draft PR. Branch protection
  stays the gate.

Then:

```bash
PYTHONPATH=src python -m triage_agent run --config config/production.yaml    # one pass
PYTHONPATH=src python -m triage_agent serve --config config/production.yaml  # webhook
```

## How triggering works

ServiceNow POSTs `{"sys_id": "..."}` to `/trigger`, signed with
`X-Triage-Signature: hex(hmac_sha256(secret, body))`. The payload is just
the id -- the agent re-reads the ticket from the source of truth, so a
forged body can't inject anything. Deliveries are idempotent per sys_id
(optionally persisted across restarts), and there's a configurable
debounce (90s in the prod template) so a duplicate storm arrives before
clustering starts. The trigger fires on insert only, never update,
otherwise our own work note would re-trigger us. A cron running
`triage_agent run` every 10 minutes catches lost webhooks; idempotency
makes the overlap harmless.

## PII scrubbing

All evidence is scrubbed before it reaches the LLM or disk: emails,
phone numbers, SSNs, card numbers, and credential-shaped tokens (API keys,
GitHub tokens, AWS keys, Slack tokens). The policy is that over-redacting
is fine and under-redacting isn't, with one deliberate exception:
card-shaped numbers that fail Luhn are kept, because they're almost always
order ids and we want those in the diagnosis.

## Tests

```bash
python -m pytest -q
```

89 tests, all offline, under a second. They cover the happy paths end to
end, the gate boundaries (0.79 vs 0.80, 0.49 vs 0.50, cluster window at
30:00 vs 30:01), cross-day dedup, malformed LLM output, forged and
replayed webhooks, missing creds, API-down degradation, path traversal via
hostile service names, and a PII byte-scan of every output file. Luhn is
cross-checked against a second independent implementation on 2000 random
inputs plus the public test PANs; HMAC against RFC 4231 vector 2.

CI (`.github/workflows/ci.yaml`) runs pyflakes and the suite on 3.10 and
3.12, a mock smoke run, and a docker build. There's a manually-dispatched
job that runs one real-LLM incident using repo secrets, so API spend stays
deliberate.

## Rolling it out

Shadow mode first: work notes only, no field writes, fix publisher in
issue mode on a sandbox repo. Measure agreement between the agent's
category and the one the human resolves with; enable field writes once
that holds at 0.7 or better for a few weeks. Keep a frozen set of solved
incidents and replay it before shipping any prompt or model change --
quality regresses silently otherwise. Kill switch: disable the Business
Rule property or scale the deployment to zero.
