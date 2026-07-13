"""Eval: incident-intelligence-agent's storm clustering vs. labeled duplicates.

Dataset: GitBugs Cassandra bugs (github.com/av9ash/gitbugs, CC BY 4.0,
arXiv:2504.09651). Ground truth = Jira duplicate links.

Scope note (important, report it): the agent clusters duplicate *storms* --
same service, shared error signature, arrival within window_minutes (default
30). Most tracker duplicates are long-range semantic dupes (median gap here:
33 days) and are OUT OF SCOPE by design. This eval measures the in-scope
regime: duplicate pairs filed within 30 minutes of each other.

Method:
  * positives  = dup pairs with both issues timestamped and delta <= 30 min
  * episode    = the pair + every other Cassandra bug filed within +/-30 min
                 of the pair's first issue (natural distractors)
  * run the agent's real cluster() with as-shipped default ClusterConfig
  * recall     = pairs grouped together / positives
  * false-merge = distractors that got pulled into the pair's group
    (caveat: labels are incomplete -- an unlabeled merge is *probably* wrong,
    not provably wrong)

Usage:
  PYTHONPATH=<agent>/src python3 eval2_storm_clustering.py <gitbugs>/cassandra
"""
import csv
import sys
from datetime import datetime

from triage_agent.clustering import cluster
from triage_agent.config import ClusterConfig

csv.field_size_limit(sys.maxsize)

WINDOW_MIN = 30


def load(cassandra_dir):
    issues = {}
    with open(f"{cassandra_dir}/cassandra_bugs.csv") as f:
        for row in csv.DictReader(f):
            iid = row["Issue id"].strip()
            try:
                ts = datetime.strptime(row["Created"].strip(), "%d/%b/%y %H:%M")
            except ValueError:
                continue
            issues[iid] = {
                "sys_id": iid,
                "number": iid,
                "short_description": row.get("Summary", "")[:200],
                "description": (row.get("Description") or "")[:2000],
                "cmdb_ci": "cassandra",
                "opened_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "_ts": ts,
            }
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
    return issues, pairs


def main(cassandra_dir):
    issues, pairs = load(cassandra_dir)
    positives = []
    for a, b in pairs:
        if a in issues and b in issues:
            delta = abs((issues[a]["_ts"] - issues[b]["_ts"]).total_seconds()) / 60
            if delta <= WINDOW_MIN:
                positives.append((a, b))
    print(f"issues: {len(issues)}  labeled pairs: {len(pairs)}  "
          f"in-scope (<= {WINDOW_MIN} min): {len(positives)}")

    cfg = ClusterConfig()  # as-shipped defaults
    hits, misses, fm_total, distractor_total = 0, [], 0, 0
    for a, b in positives:
        t0 = issues[a]["_ts"]
        episode = [
            {k: v for k, v in inc.items() if k != "_ts"}
            for iid, inc in issues.items()
            if abs((inc["_ts"] - t0).total_seconds()) / 60 <= WINDOW_MIN
        ]
        distractors = len(episode) - 2
        distractor_total += distractors
        groups = cluster(episode, cfg)
        together = next(
            (g for g in groups
             if {a, b} <= {i["number"] for i in g}), None)
        if together:
            hits += 1
            fm_total += len(together) - 2  # unlabeled extras merged in
        else:
            misses.append((a, b))

    n = len(positives)
    print(f"recall on in-scope pairs: {hits}/{n} ({100*hits/n:.0f}%)")
    print(f"distractors present across episodes: {distractor_total}")
    print(f"unlabeled issues merged into hit-groups: {fm_total}")
    if misses:
        print("missed pairs (for error analysis):")
        for a, b in misses:
            from triage_agent.clustering import signature
            sa = signature(issues[a], cfg.signature_tokens)
            sb = signature(issues[b], cfg.signature_tokens)
            print(f"  {a} vs {b}  sig_a={sa!r}  sig_b={sb!r}")


if __name__ == "__main__":
    main(sys.argv[1])
