# Screening evaluation - after fixes

Run 2026-10-24 15:11 UTC, commit `unknown`, dataset `2026.10-v1`. Embeddings: `hash`. LLM stages: off. Stage-1 cut 10, LLM review cut 6, metrics at k=5. **Result: PASS**

> Offline run with the hash stand-in for embeddings: retrieval numbers are not representative of the real model. Fairness and pipeline checks still apply.

## Ranking quality (macro average over 4 jobs)

| Mode | Stage-1 recall | Stage-2 recall | Precision@5 | Recall@5 | nDCG@5 | MRR |
|---|---|---|---|---|---|---|
| Screening applicants | 100% | 100% | 45% | 94% | 0.957 | 1.000 |
| Talent search | 100% | 100% | 50% | 100% | 0.968 | 1.000 |

### Per job

| Job | Mode | Relevant | nDCG@5 | Recall@5 | Top 5 (label, score) |
|---|---|---|---|---|---|
| backend | applications | 4 | 0.948 | 75% | backend_senior (3, 90), backend_aliases (3, 78), backend_prose (3, 77), backend_junior (1, 62), devops_k8s (1, 61) |
| backend | talent | 4 | 0.995 | 100% | backend_senior (3, 90), backend_aliases (3, 78), backend_prose (3, 77), devops_k8s (1, 61), backend_django (2, 58) |
| data | applications | 2 | 0.958 | 100% | ds_nlp (3, 90), ml_engineer (2, 58), data_engineer (1, 54), keyword_stuffer (0, 48), career_changer (1, 47) |
| data | talent | 2 | 0.958 | 100% | ds_nlp (3, 90), ml_engineer (2, 58), data_engineer (1, 54), keyword_stuffer (0, 48), career_changer (1, 47) |
| frontend | applications | 2 | 1.000 | 100% | frontend_react (3, 91), fullstack (2, 60), frontend_vue (1, 34), keyword_stuffer (0, 33), backend_senior (0, 21) |
| frontend | talent | 2 | 1.000 | 100% | frontend_react (3, 91), fullstack (2, 60), frontend_vue (1, 34), keyword_stuffer (0, 33), backend_senior (0, 21) |
| devops | applications | 2 | 0.920 | 100% | devops_k8s (3, 94), devops_mid (2, 72), backend_senior (1, 61), backend_prose (0, 53), fullstack (0, 46) |
| devops | talent | 2 | 0.920 | 100% | devops_k8s (3, 94), devops_mid (2, 72), backend_senior (1, 61), backend_prose (0, 53), fullstack (0, 46) |

## What each stage contributes (nDCG@5, whole pool, no LLM)

| Job | Vector only | + keyword (hybrid) | + skills and experience (full scorer) |
|---|---|---|---|
| backend | 0.922 | 0.922 | 0.948 |
| data | 0.958 | 0.958 | 0.958 |
| frontend | 0.993 | 1.000 | 1.000 |
| devops | 0.861 | 0.894 | 0.958 |
| **macro** | 0.933 | 0.944 | 0.966 |

## False negatives (1)

Relevant candidates (label 2-3) missing from the top 5, and why.

| Job | Mode | Candidate | Label | Lost at | Reason |
|---|---|---|---|---|---|
| backend | applications | backend_django | 2 | ranking | ranked #6 (score 58.2, cut-off #5); skills not recognised: AWS, FastAPI, Kafka, Kubernetes |

## Counterfactual fairness

Each twin is the control resume with one attribute changed. Tolerance: 1 point(s). Selected = score of 65 or more.

| Attribute | Twins | Mean change | Max abs change | Max rank change | Selection rate by level | Adverse impact ratio |
|---|---|---|---|---|---|---|
| career_break | 5 | +0.00 | 0.00 | 0 | none 80%, break 80% | 1.000 |
| city | 5 | +0.00 | 0.00 | 0 | Bengaluru 80%, small_city 80% | 1.000 |
| college | 10 | +0.00 | 0.00 | 0 | control 80%, tier1 80%, tier3 80% | 1.000 |
| name | 15 | +0.00 | 0.00 | 0 | A 80%, B 80%, C 80%, D 80% | 1.000 |
| phrasing | 5 | -1.21 | 2.73 | 8 | native 80%, non_native 80% | 1.000 |

Twins outside tolerance:

| Base | Job | Change | Score change | Semantic change | Experience change (yrs) | Skill change |
|---|---|---|---|---|---|---|
| backend_senior | backend | phrasing=non_native | -2.73 | -9.10 | +0.00 | +0.00 |
| frontend_react | frontend | phrasing=non_native | -1.97 | -6.56 | +0.00 | +0.00 |

## How to read this

- Synthetic, hand-labelled data (18 resumes, 40 twins): it shows *sensitivity* of the pipeline, not real-world outcomes.
- Selection rates use 5 twins per level, so the adverse impact ratio is indicative only; score changes on identical resumes are the stronger signal.
- Name, city and career-break changes must not move the score; college tier and phrasing changes are reported as warnings.
- Stage cut-offs are scaled to the pool (10 of 18 vs 100 of the full database in production).
