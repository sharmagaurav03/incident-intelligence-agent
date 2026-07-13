# Eval: end-to-end replay against GitBugs Cassandra

Run date: 2026-07-13. Models: claude-haiku-4-5 (summaries), claude-sonnet-4-6
(diagnosis). Repo at the commit this file was introduced in.

## Dataset

GitBugs (github.com/av9ash/gitbugs, CC BY 4.0). Cite: Patil, "GitBugs: Bug
Reports for Duplicate Detection...", arXiv:2504.09651.

Cassandra tracker: 4,612 timestamped bugs, 164 Jira-labeled duplicate pairs.
Scope note: median gap between labeled duplicates is 33 days — long-range
semantic dupes are out of scope for a storm clusterer by design. The eval set
is the 25 pairs filed within the 30-minute window: 46 unique tickets,
built by `evals/make_eval_dataset.py`.

## Results (real-LLM run, `results/eval_run.log`)

- 46 tickets -> 20 clusters; 22/25 labeled pairs recovered (88%)
- 9/20 clusters returned `unknown` (45%). Expected and correct: the eval
  provides no logs/metrics/deploys for these tickets, so diagnoses run
  evidence-thin and the confidence gate abstains.
- 4 fix suggestions, all category code-defect at confidence >= 0.82, all as
  draft patch files (`results/fix_*.md`). A code-defect at 0.72 correctly
  produced no fix (gate is 0.8).
- 20 LLM calls, 12,164 in / 9,825 out tokens, 0 errors. Cost <= ~$0.12.

## Misses (the interesting part)

- 13362685 / 13362689 and 13560036 / 13560037: exact-match signatures don't
  forgive one ticket matching "failing" when its twin doesn't.
- 13559309 / 13559316: the cluster window is anchored to the group head, so
  13559316 was absorbed into the large 13559294 storm cluster while its twin,
  nine minutes away, fell outside that head's window.

## Caveats

- Duplicate labels are incomplete: 43 co-clustered pairs are unlabeled
  (mostly inside the 10-ticket 13559294 storm, visibly one event series) and
  are counted neither for nor against.
- No root-cause ground truth exists in GitBugs, so no classification
  accuracy is claimed — only gate behavior.
- A separate offline eval with natural distractors from the full tracker
  (`evals/eval2_storm_clustering.py`) scores 21/25; the difference is one
  head-anchoring case triggered by distractors.

## Reproduce

    git clone --depth 1 https://github.com/av9ash/gitbugs.git /tmp/gitbugs
    python3 evals/make_eval_dataset.py /tmp/gitbugs/cassandra
    cp config/default.yaml config/eval.yaml   # set data_dir: data_eval, output_dir: out_eval
    export ANTHROPIC_API_KEY=...
    TRIAGE__llm__mode=real PYTHONPATH=src python -m triage_agent run --config config/eval.yaml
