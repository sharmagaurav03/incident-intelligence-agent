"""Build a small eval dataset for incident-intelligence-agent from GitBugs.

Takes the Cassandra bugs whose labeled duplicates arrived within the 30-min
storm window (the in-scope regime) and writes them as incidents in the
agent's own schema, into data_eval/.

Run from the agent repo root:
    python3 make_eval_dataset.py /path/to/gitbugs/cassandra
"""
import csv
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

csv.field_size_limit(sys.maxsize)
WINDOW_MIN = 30


def main(cassandra_dir):
    issues = {}
    with open(f"{cassandra_dir}/cassandra_bugs.csv") as f:
        for row in csv.DictReader(f):
            iid = row["Issue id"].strip()
            try:
                ts = datetime.strptime(row["Created"].strip(), "%d/%b/%y %H:%M")
            except ValueError:
                continue
            issues[iid] = (ts, row)

    pairs = set()
    with open(f"{cassandra_dir}/cassandra_bugs-combined.csv") as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            if len(row) < 2:
                continue
            a = row[0].strip()
            for b in row[1].replace('"', "").split(","):
                b = b.strip()
                if a and b and a != b:
                    pairs.add(tuple(sorted((a, b))))

    selected = set()
    for a, b in pairs:
        if a in issues and b in issues:
            delta = abs((issues[a][0] - issues[b][0]).total_seconds()) / 60
            if delta <= WINDOW_MIN:
                selected.update((a, b))

    incidents = []
    for iid in sorted(selected, key=lambda i: issues[i][0]):
        ts, row = issues[iid]
        incidents.append({
            "sys_id": f"gitbugs-{iid}",
            "number": iid,
            "short_description": (row.get("Summary") or "")[:200],
            "description": (row.get("Description") or "")[:2000],
            "cmdb_ci": "cassandra",
            "priority": "3",
            "severity": "3",   # keep clear of the Sev1 assist-only gate
            "state": "New",
            "opened_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "assignment_group": "eval",
            "work_notes": [],
        })

    out = Path("data_eval")
    out.mkdir(exist_ok=True)
    # reuse the sample aux files so the file adapters have something to read;
    # there are no cassandra logs/metrics, so evidence will be thin -- that is
    # a known, reportable property of this eval, not a bug
    for name in ("deploys.json", "flags.json", "history.json"):
        shutil.copy(Path("data") / name, out / name)
    for name in ("logs", "metrics"):
        if (out / name).exists():
            shutil.rmtree(out / name)
        shutil.copytree(Path("data") / name, out / name)
    (out / "incidents.json").write_text(
        json.dumps({"incidents": incidents}, indent=2))
    print(f"wrote {len(incidents)} incidents "
          f"({len(selected)//2}+ labeled storm pairs) to data_eval/")


if __name__ == "__main__":
    main(sys.argv[1])
