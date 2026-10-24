# Screening quality and fairness evaluation

`run_eval.py` measures how well the ranking pipeline finds the right people, and whether things that
shouldn't matter (a name, a city, a career break) change anyone's score.

It loads a labelled synthetic dataset (`dataset.py`) through the real API into an empty, throwaway
database, so everything under test is the production code path: PDF parsing, skill extraction,
embeddings, retrieval, SQL filters and scoring.

## Run it

```bash
createdb ats_eval
DATABASE_URL=postgresql://postgres:postgres@localhost/ats_eval python database/migrate.py --local
python eval/run_eval.py --database-url postgresql://postgres:postgres@localhost/ats_eval
```

| Option | Use |
|---|---|
| `--embeddings hash` | Offline stand-in for the embedding model (CI). Fairness checks hold; retrieval numbers aren't representative |
| `--llm` | Also run the Bedrock extraction and re-ranking stages (AWS credentials in the environment) |
| `--strict --min-ndcg 0.85` | Exit 1 if any fairness check fails or quality drops below the floor (used by the test suite) |

It refuses to run against Supabase or a database that already has applicants. Each run writes
`reports/eval-<time>.md` and `.json`, and a row in `eval_runs` with its metrics and findings.

## What it measures

| | |
|---|---|
| **Dataset** | 18 hand-labelled resumes across 4 jobs (relevance 0-3), including deliberate hard cases: skills written as aliases, skills only in prose with "since 2020" dates, a keyword-stuffed sales resume, a career changer. Plus 40 counterfactual twins |
| **Funnel** | Stage-1 recall (did retrieval keep the relevant people), stage-2 recall (did the SQL filters), then precision@5, recall@5, nDCG@5 and MRR of the final ranking. For both screening applicants and talent search |
| **Ablation** | nDCG with vector search only, with keywords added (hybrid), and with the full scorer, to show each stage earns its place |
| **False negatives** | Every relevant candidate outside the top 5, attributed to the stage that lost them and why |
| **Counterfactual fairness** | Five resumes copied with one attribute changed: four name sets (gender, region and religion cues), college tier, city, a career break (same total experience), non-native English phrasing. Reports the score change against the original, rank change, selection rate per level and the adverse impact ratio (four-fifths rule). Name, city and career-break changes must not move the score at all |

## What the first run found, and what changed

`reports/01-baseline-before-fixes.md` is the first run on the original code;
`reports/02-after-fixes.md` is the same dataset after the fixes below.

| Finding (baseline) | Cause | Fix |
|---|---|---|
| A sales resume listing every hot skill ranked **#2 for Backend Engineer** | Skills only *listed* earned the same credit as skills *used* | Skills not shown in work history or projects get half credit (`evidenced_skills`, `ranking._skill_score`) |
| A platform engineer's experience read as **3 years instead of 11** | The education-line filter matched "nit" inside "moni**t**oring" and "defi**nit**ions" and dropped those work lines | Whole-word matching (`parsing._EDU_WORDS`) |
| A career break **added a year** of experience | Break lines were counted as work | Break lines are skipped (`_BREAK_WORDS`) |
| "Software Engineer at Payly, since 2020" read as **0 years**, so talent search filtered a strong candidate out | Only "YYYY - YYYY" ranges were understood | "since YYYY" is understood |
| A strong candidate writing "Postgres, k8s, Python3" was **not retrieved** | Aliases were only applied to LLM output; retrieval used vector distance alone | Aliases are recognised in free text; the embedding uses canonical skill names; stage 1 is now a real hybrid (union of vector and keyword top-N) |
| Changing only the **name** moved scores by up to 0.8 points; **college** by 1.3; **city** by 0.3 | Names, contact details, city and education lines were part of the embedded text | `parsing.embedding_text` strips them before embedding |

| Metric (screening, macro avg) | Baseline | After |
|---|---|---|
| nDCG@5 | 0.832 | 0.957 |
| Recall@5 | 81% | 94% |
| Stage-1 recall | 94% | 100% |
| False negatives | 5 | 1 |
| Max score change: name / city / college / career break | 0.79 / 0.28 / 1.28 / 1.26 | 0 / 0 / 0 / 0 |

**Still open, reported as a warning:** non-native English phrasing of the same facts lowers the
semantic score by up to ~2.7 points (with the offline hash embeddings; re-run with `fastembed` to
measure the real model). Skill and experience scores are unaffected. Possible next steps: weight the
semantic component lower, or embed a normalised summary instead of raw prose.

## Limits

Synthetic data shows how *sensitive* the pipeline is to a change; it doesn't measure real-world
outcomes. Selection rates rest on 5 twins per level, so the adverse impact ratio is indicative; the
score change on otherwise identical resumes is the stronger signal. The app deliberately collects no
gender, caste, religion or age data, which is why fairness is tested with counterfactual twins rather
than by auditing real users.
