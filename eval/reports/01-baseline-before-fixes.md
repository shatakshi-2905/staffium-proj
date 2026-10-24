# Screening evaluation - baseline, before fixes

Run 2026-10-24 15:07 UTC, commit `unknown`, dataset `2026.10-v1`. Embeddings: `hash`. LLM stages: off. Stage-1 cut 10, LLM review cut 6, metrics at k=5. **Result: FAIL**

> Offline run with the hash stand-in for embeddings: retrieval numbers are not representative of the real model. Fairness and pipeline checks still apply.

## Ranking quality (macro average over 4 jobs)

| Mode | Stage-1 recall | Stage-2 recall | Precision@5 | Recall@5 | nDCG@5 | MRR |
|---|---|---|---|---|---|---|
| Screening applicants | 94% | 94% | 35% | 81% | 0.832 | 1.000 |
| Talent search | 94% | 88% | 40% | 88% | 0.843 | 1.000 |

### Per job

| Job | Mode | Relevant | nDCG@5 | Recall@5 | Top 5 (label, score) |
|---|---|---|---|---|---|
| backend | applications | 4 | 0.501 | 25% | backend_senior (3, 86), keyword_stuffer (0, 69), backend_junior (1, 58), devops_k8s (1, 58), ml_engineer (1, 57) |
| backend | talent | 4 | 0.548 | 50% | backend_senior (3, 86), keyword_stuffer (0, 69), devops_k8s (1, 58), ml_engineer (1, 57), backend_django (2, 57) |
| data | applications | 2 | 0.913 | 100% | ds_nlp (3, 93), keyword_stuffer (0, 65), ml_engineer (2, 61), data_engineer (1, 53), career_changer (1, 44) |
| data | talent | 2 | 0.913 | 100% | ds_nlp (3, 93), keyword_stuffer (0, 65), ml_engineer (2, 61), data_engineer (1, 53), career_changer (1, 44) |
| frontend | applications | 2 | 0.993 | 100% | frontend_react (3, 85), fullstack (2, 64), keyword_stuffer (0, 45), frontend_vue (1, 32), ds_nlp (0, 23) |
| frontend | talent | 2 | 0.993 | 100% | frontend_react (3, 85), fullstack (2, 64), keyword_stuffer (0, 45), frontend_vue (1, 32), ds_nlp (0, 23) |
| devops | applications | 2 | 0.920 | 100% | devops_k8s (3, 90), devops_mid (2, 69), backend_senior (1, 59), fullstack (0, 44), ml_engineer (0, 41) |
| devops | talent | 2 | 0.920 | 100% | devops_k8s (3, 90), devops_mid (2, 69), backend_senior (1, 59), fullstack (0, 44), ml_engineer (0, 41) |

## What each stage contributes (nDCG@5, whole pool, no LLM)

| Job | Vector only | + keyword (hybrid) | + skills and experience (full scorer) |
|---|---|---|---|
| backend | 0.713 | 0.739 | 0.501 |
| data | 0.920 | 0.962 | 0.913 |
| frontend | 0.988 | 0.993 | 0.993 |
| devops | 0.874 | 0.874 | 0.913 |
| **macro** | 0.874 | 0.892 | 0.830 |

## False negatives (5)

Relevant candidates (label 2-3) missing from the top 5, and why.

| Job | Mode | Candidate | Label | Lost at | Reason |
|---|---|---|---|---|---|
| backend | applications | backend_aliases | 3 | retrieval | not retrieved: semantic rank 13 of 18, stage 1 keeps 10 |
| backend | applications | backend_prose | 3 | ranking | ranked #8 (score 47.58, cut-off #5); skills not recognised: Kafka, Kubernetes; experience read as 0 yrs |
| backend | applications | backend_django | 2 | ranking | ranked #6 (score 56.54, cut-off #5); skills not recognised: AWS, FastAPI, Kafka, Kubernetes |
| backend | talent | backend_aliases | 3 | retrieval | not retrieved: semantic rank 13 of 18, stage 1 keeps 10 |
| backend | talent | backend_prose | 3 | filter | filtered out: experience read as 0 yrs < required 3 |

## Counterfactual fairness

Each twin is the control resume with one attribute changed. Tolerance: 1 point(s). Selected = score of 65 or more.

| Attribute | Twins | Mean change | Max abs change | Max rank change | Selection rate by level | Adverse impact ratio |
|---|---|---|---|---|---|---|
| career_break | 5 | -0.42 | 1.26 | 8 | none 80%, break 80% | 1.000 |
| city | 5 | -0.24 | 0.28 | 6 | Bengaluru 80%, small_city 80% | 1.000 |
| college | 10 | +0.27 | 1.28 | 6 | control 80%, tier1 80%, tier3 80% | 1.000 |
| name | 15 | -0.16 | 0.79 | 7 | A 80%, B 80%, C 80%, D 80% | 1.000 |
| phrasing | 5 | -1.41 | 3.62 | 8 | native 80%, non_native 80% | 1.000 |

Twins outside tolerance:

| Base | Job | Change | Score change | Semantic change | Experience change (yrs) | Skill change |
|---|---|---|---|---|---|---|
| backend_senior | backend | phrasing=non_native | -3.62 | -12.04 | +0.00 | +0.00 |
| ds_nlp | data | career_break=break | -1.26 | -4.19 | +1.00 | +0.00 |
| frontend_react | frontend | college=tier3 | +1.28 | +4.26 | +0.00 | +0.00 |
| frontend_react | frontend | phrasing=non_native | -1.72 | -5.73 | +0.00 | +0.00 |
| devops_k8s | devops | phrasing=non_native | -1.06 | -3.53 | -8.00 | +0.00 |

## How to read this

- Synthetic, hand-labelled data (18 resumes, 40 twins): it shows *sensitivity* of the pipeline, not real-world outcomes.
- Selection rates use 5 twins per level, so the adverse impact ratio is indicative only; score changes on identical resumes are the stronger signal.
- Name, city and career-break changes must not move the score; college tier and phrasing changes are reported as warnings.
- Stage cut-offs are scaled to the pool (10 of 18 vs 100 of the full database in production).
