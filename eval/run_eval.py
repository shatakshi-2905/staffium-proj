#!/usr/bin/env python3
"""Screening quality and fairness evaluation.

Loads the labelled synthetic dataset (eval/dataset.py) into a THROWAWAY database through the real
API (real resume parsing, skill extraction, embeddings and ranking code), then measures:

  Quality      stage-1 recall (retrieval), stage-2 recall (SQL filters), precision@k, recall@k,
               nDCG@k and MRR for the final ranking; in both applicant screening and talent search.
  Ablation     the same nDCG for vector-only, hybrid (vector + keyword) and the full scorer, to show
               what each stage contributes.
  False negatives  every relevant candidate missing from the top k, with the stage and reason.
  Fairness     counterfactual twins (same resume, one attribute changed: name, college tier, city,
               career break, non-native phrasing): score change vs the control, plus selection
               rates and the adverse impact ratio (four-fifths rule) per attribute level.

Results go to eval/reports/<timestamp>.{md,json} and to the eval_* tables.

Usage (from the repo root, against an empty database with all migrations applied):
    createdb ats_eval && DATABASE_URL=postgresql://postgres:postgres@localhost/ats_eval python database/migrate.py --local
    python eval/run_eval.py --database-url postgresql://postgres:postgres@localhost/ats_eval
Options: --llm (also run Bedrock stages; needs AWS credentials in the environment),
         --embeddings hash (offline smoke runs only), --strict (exit 1 on any failing check).
"""
import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "eval"))

import dataset as ds  # noqa: E402
import metrics as M  # noqa: E402

SELECT_THRESHOLD = 65.0      # MATCH or better counts as "selected" for adverse-impact checks
PROTECTED_LIKE = {"name", "city", "career_break"}   # changes here must not move the score at all


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database-url", default=os.environ.get("EVAL_DATABASE_URL"))
    ap.add_argument("--embeddings", choices=["fastembed", "hash"], default=os.environ.get("EMBEDDINGS_PROVIDER", "fastembed"))
    ap.add_argument("--llm", action="store_true", help="include the Bedrock extraction and re-ranking stages")
    ap.add_argument("--stage1", type=int, default=10, help="stage-1 cut-off, scaled to the 18-resume pool (production: 100)")
    ap.add_argument("--stage2", type=int, default=6, help="LLM review cut-off, scaled (production: 20)")
    ap.add_argument("--k", type=int, default=5, help="cut-off for precision/recall/nDCG of the final ranking")
    ap.add_argument("--tolerance", type=float, default=1.0, help="max allowed score change for counterfactual twins")
    ap.add_argument("--label", default="", help="free-text label stored with the run")
    ap.add_argument("--out", default=str(ROOT / "eval" / "reports"))
    ap.add_argument("--min-ndcg", type=float, default=None, help="fail the run if macro nDCG@k (screening) is below this")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any check fails")
    return ap.parse_args(argv)


def configure_environment(args) -> None:
    url = args.database_url
    if not url:
        sys.exit("Set --database-url (or EVAL_DATABASE_URL) to an empty throwaway database.")
    host = urlparse(url).hostname or ""
    if "supabase" in host or "pooler" in host:
        sys.exit("Refusing to run against Supabase: the evaluation creates test users. Use a local throwaway database.")
    os.environ.update({
        "ENVIRONMENT": "test", "DATABASE_URL": url, "SUPABASE_URL": "https://eval.invalid",
        "SUPABASE_ANON_KEY": "eval", "SUPABASE_JWT_SECRET": secrets.token_hex(32),
        "FRONTEND_ORIGIN": "http://localhost", "EMBEDDINGS_PROVIDER": args.embeddings,
        "LLM_ENABLED": "true" if args.llm else "false", "WORKER_ENABLED": "false", "S3_BUCKET_NAME": "eval",
    })


# ---------------------------------------------------------------------------------------------------
# Loading the dataset through the API
# ---------------------------------------------------------------------------------------------------
class Loader:
    def __init__(self, client, db, settings):
        self.client, self.db, self.s = client, db, settings

    def headers(self, uid):
        import jwt
        now = int(time.time())
        token = jwt.encode({"sub": str(uid), "aud": "authenticated", "iss": self.s.supabase_issuer, "iat": now, "exp": now + 7200},
                           self.s.supabase_jwt_secret, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    async def user(self, role, email):
        uid = uuid.uuid4()
        async with self.db.system_tx() as conn:
            await conn.execute("INSERT INTO auth.users (id, email, raw_user_meta_data) VALUES ($1, $2, $3)", uid, email, {"role": role})
        return uid

    async def call(self, method, path, uid, **kw):
        r = await self.client.request(method, path, headers=self.headers(uid), **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None


def pdf_bytes(text: str) -> bytes:
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(40, 40, 570, 820), text, fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


async def load(loader: Loader, candidates):
    rec = await loader.user("RECRUITER", "eval.recruiter@example.test")
    company = await loader.call("POST", "/api/companies", rec, json={"company_name": "Eval Co", "first_name": "Eval", "last_name": "Recruiter"})
    skills = {s["skill_name"]: s["skill_id"] for s in (await loader.call("GET", "/api/skills", rec))["items"]}
    jobs = {}
    for key, (title, req, pref, min_exp, desc) in ds.JOBS.items():
        body = {"title": title, "description": desc, "status": "OPEN", "min_experience": min_exp,
                "skills": [{"skill_id": skills[n]} for n in req] + [{"skill_id": skills[n], "is_required": False} for n in pref]}
        jobs[key] = (await loader.call("POST", "/api/recruiter/jobs", rec, json=body))["job_id"]

    applicant_of, apps, failures = {}, {}, []
    for i, c in enumerate(candidates):
        uid = await loader.user("APPLICANT", f"cand{i}@example.test")
        first, last = c.name.split(" ", 1)
        prof = await loader.call("POST", "/api/applicant/profile", uid, json={
            "first_name": first, "last_name": last, "headline": c.profile.headline, "location": c.city,
            "is_discoverable": c.discoverable})
        await loader.call("POST", "/api/resumes", uid, files={"file": (f"cv{i}.pdf", pdf_bytes(c.resume_text()), "application/pdf")})
        status = (await loader.call("GET", "/api/resumes", uid))["items"][0]
        if status["processing_status"] != "COMPLETED":
            failures.append({"ref": c.ref, "status": status["processing_status"], "error": status["processing_error"]})
            continue
        applicant_of[c.ref] = prof["applicant_id"]
        targets = list(ds.JOBS) if c.base is None else [ds.FAIRNESS_BASES[c.base]]
        for job_key in targets:
            apps[(c.ref, job_key)] = (await loader.call("POST", f"/api/jobs/{jobs[job_key]}/apply", uid))["application_id"]
    return rec, company["company_id"], jobs, applicant_of, apps, failures


# ---------------------------------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------------------------------
async def candidate_facts(conn, applicant_ids):
    rows = await conn.fetch(
        """SELECT a.applicant_id, a.location, a.notice_period_days, calculate_candidate_experience(a.applicant_id) AS years,
                  ARRAY(SELECT s.skill_name FROM candidate_skills cs JOIN skills s ON s.skill_id = cs.skill_id
                        WHERE cs.applicant_id = a.applicant_id ORDER BY 1) AS skills
           FROM applicants a WHERE a.applicant_id = ANY($1::bigint[])""", list(applicant_ids))
    return {r["applicant_id"]: dict(r) for r in rows}


def explain_miss(ref, label, mode, k, stage1, stage2, final, facts, spec, semantic_rank, scores):
    f = facts
    if ref not in stage1:
        return "retrieval", (f"not retrieved: semantic rank {semantic_rank.get(ref, '?')} of {len(semantic_rank)}, "
                             f"stage 1 keeps {len(stage1)}")
    if mode == "talent" and ref not in stage2:
        reasons = []
        if spec.min_experience and float(f["years"] or 0) < spec.min_experience:
            reasons.append(f"experience read as {float(f['years'] or 0):g} yrs < required {spec.min_experience:g}")
        return "filter", "filtered out: " + ("; ".join(reasons) or "metadata filter")
    pos = final.index(ref) + 1 if ref in final else None
    sc = scores.get(ref, {})
    missing = sc.get("missing_skills") or []
    detail = f"ranked #{pos} (score {sc.get('overall_score')}, cut-off #{k})"
    if missing:
        detail += f"; skills not recognised: {', '.join(missing)}"
    if sc.get("experience_score") is not None and sc["experience_score"] < 100:
        detail += f"; experience read as {float(f['years'] or 0):g} yrs"
    return "ranking", detail


async def evaluate(args, db, ranking, recruiter_mod, rec_uid, company_id, jobs, applicant_of, apps, candidates):
    ref_of = {v: k for k, v in applicant_of.items()}
    uniques = [c for c in candidates if c.base is None and c.ref in applicant_of]
    metric_rows, findings, per_job = [], [], {}

    def add(job, mode, metric, value, variant="pipeline"):
        metric_rows.append((job, mode, variant, metric, None if value is None else round(float(value), 4)))

    async with db.user_tx(rec_uid) as conn:
        facts_by_id = await candidate_facts(conn, applicant_of.values())
        facts = {ref_of[k]: v for k, v in facts_by_id.items()}

        for job_key, job_id in jobs.items():
            labels = {c.ref: c.labels.get(job_key, 0) for c in uniques}
            relevant = {r for r, l in labels.items() if l >= 2}
            spec = await recruiter_mod._spec_for_job(conn, job_id, company_id)
            unique_apps = [apps[(c.ref, job_key)] for c in uniques if (c.ref, job_key) in apps]
            out = per_job[job_key] = {"relevant": sorted(relevant), "modes": {}}

            # --- ablation over the whole pool (no stage-1 cut, no LLM) --------------------------------
            ranking.STAGE1_LIMIT, ranking.STAGE2_LIMIT = 10_000, args.stage2
            trace = {}
            scored, _ = await ranking.rank(conn, spec, pool="applications", use_llm=False, only_application_ids=unique_apps, trace=trace)
            sem = sorted(trace["stage1"], key=lambda r: r["semantic"], reverse=True)
            semantic_rank = {ref_of[r["applicant_id"]]: i + 1 for i, r in enumerate(sem)}
            orders = {
                "vector_only": [ref_of[r["applicant_id"]] for r in sem],
                "hybrid": [ref_of[r["applicant_id"]] for r in sorted(trace["stage1"], key=lambda r: 0.75 * r["semantic"] + 0.25 * r["keyword"], reverse=True)],
                "full_scorer": [ref_of[c["applicant_id"]] for c in scored],
            }
            out["ablation"] = {}
            for variant, order in orders.items():
                v = M.ndcg_at(order, labels, args.k)
                add(job_key, "ablation", f"ndcg@{args.k}", v, variant)
                out["ablation"][variant] = v

            # --- the real funnel, in both modes -------------------------------------------------------
            for mode in ("applications", "talent"):
                ranking.STAGE1_LIMIT, ranking.STAGE2_LIMIT = args.stage1, args.stage2
                trace = {}
                if mode == "applications":
                    ranked, llm_used = await ranking.rank(conn, spec, pool="applications", use_llm=args.llm,
                                                          only_application_ids=unique_apps, trace=trace)
                else:
                    talent_spec = ranking.SearchSpec(title=spec.title, description=spec.description, skills=spec.skills,
                                                     min_experience=spec.min_experience)
                    ranked, llm_used = await ranking.rank(conn, talent_spec, pool="talent", use_llm=args.llm, trace=trace)
                stage1 = [ref_of[r["applicant_id"]] for r in trace["stage1"] if r["applicant_id"] in ref_of]
                stage2 = [ref_of[a] for a in trace["stage2"] if a in ref_of]
                final = [ref_of[c["applicant_id"]] for c in ranked if c["applicant_id"] in ref_of]
                scores = {ref_of[c["applicant_id"]]: c for c in ranked if c["applicant_id"] in ref_of}
                m = {
                    f"stage1_recall@{args.stage1}": M.recall_at(stage1, relevant),
                    "stage2_recall": M.recall_at(stage2, relevant),
                    f"precision@{args.k}": M.precision_at(final, relevant, args.k),
                    f"recall@{args.k}": M.recall_at(final, relevant, args.k),
                    f"ndcg@{args.k}": M.ndcg_at(final, labels, args.k),
                    "mrr": M.mrr(final, relevant),
                    "llm_used": float(llm_used),
                }
                for name, val in m.items():
                    add(job_key, mode, name, val)
                misses = []
                for ref in sorted(relevant - set(final[:args.k]), key=lambda r: -labels[r]):
                    stage, reason = explain_miss(ref, labels[ref], mode, args.k, stage1, stage2, final, facts[ref], spec, semantic_rank, scores)
                    misses.append({"candidate": ref, "label": labels[ref], "stage": stage, "reason": reason})
                    findings.append(("FALSE_NEGATIVE", "warn" if labels[ref] == 3 else "info", job_key, ref,
                                     {"mode": mode, "label": labels[ref], "stage": stage, "reason": reason}))
                out["modes"][mode] = {"metrics": m, "top": [(r, labels.get(r, 0), scores[r]["overall_score"]) for r in final[:args.k]],
                                      "false_negatives": misses}

        # --- counterfactual fairness ---------------------------------------------------------------------
        ranking.STAGE1_LIMIT, ranking.STAGE2_LIMIT = 10_000, args.stage2
        twins_by_base = defaultdict(list)
        for c in candidates:
            if c.base and c.ref in applicant_of:
                twins_by_base[c.base].append(c)
        deltas = defaultdict(list)
        twin_rows, selections = [], defaultdict(lambda: defaultdict(list))
        for base, job_key in ds.FAIRNESS_BASES.items():
            if base not in applicant_of:
                continue
            group = [base] + [t.ref for t in twins_by_base[base]]
            spec = await recruiter_mod._spec_for_job(conn, jobs[job_key], company_id)
            ranked, _ = await ranking.rank(conn, spec, pool="applications", use_llm=args.llm,
                                           only_application_ids=[apps[(r, job_key)] for r in group if (r, job_key) in apps])
            by_ref = {ref_of[c["applicant_id"]]: c for c in ranked}
            ctl = by_ref.get(base)
            if not ctl:
                continue
            # Rank by score with ties sharing a rank, so identical scores never show as a rank change.
            all_scores = [c["overall_score"] for c in ranked]
            rank_of = lambda score: 1 + sum(1 for x in all_scores if x > score)  # noqa: E731
            levels = ds.control_levels()
            for attr, lvl in levels.items():
                selections[attr][lvl].append(ctl["overall_score"] >= SELECT_THRESHOLD)
            for t in twins_by_base[base]:
                c = by_ref.get(t.ref)
                if not c:
                    continue
                d = round(c["overall_score"] - ctl["overall_score"], 2)
                deltas[t.attribute].append(d)
                selections[t.attribute][t.level].append(c["overall_score"] >= SELECT_THRESHOLD)
                row = {"base": base, "job": job_key, "attribute": t.attribute, "level": t.level, "control_score": ctl["overall_score"],
                       "twin_score": c["overall_score"], "delta": d,
                       "delta_semantic": round(c["semantic_score"] - ctl["semantic_score"], 2),
                       "delta_experience_years": round(c["experience_years"] - ctl["experience_years"], 2),
                       "delta_skill": round((c["skill_score"] or 0) - (ctl["skill_score"] or 0), 2),
                       "rank_change": rank_of(c["overall_score"]) - rank_of(ctl["overall_score"])}
                twin_rows.append(row)
                if abs(d) > args.tolerance:
                    sev = "fail" if t.attribute in PROTECTED_LIKE else "warn"
                    findings.append(("COUNTERFACTUAL", sev, job_key, f"{base} / {t.attribute}={t.level}", row))

    fairness = {"attributes": {}, "twins": twin_rows}
    for attr, vals in deltas.items():
        rates = {lvl: (sum(v) / len(v) if v else None) for lvl, v in selections[attr].items()}
        air = M.adverse_impact_ratio(rates)
        fairness["attributes"][attr] = {**M.summarize(vals), "selection_rates": rates, "adverse_impact_ratio": air,
                                        "max_rank_change": max((abs(r["rank_change"]) for r in twin_rows if r["attribute"] == attr), default=0)}
        metric_rows.append(("all", "applications", f"cf_{attr}", "max_abs_delta", fairness["attributes"][attr]["max_abs"]))
        metric_rows.append(("all", "applications", f"cf_{attr}", "adverse_impact_ratio", air))
        if air is not None and air < 0.8:
            findings.append(("ADVERSE_IMPACT", "fail" if attr in PROTECTED_LIKE else "warn", None, attr,
                             {"selection_rates": rates, "adverse_impact_ratio": round(air, 3)}))

    # Macro averages
    for mode in ("applications", "talent"):
        for name in (f"stage1_recall@{args.stage1}", "stage2_recall", f"precision@{args.k}", f"recall@{args.k}", f"ndcg@{args.k}", "mrr"):
            vals = [r[4] for r in metric_rows if r[1] == mode and r[3] == name and r[0] != "all" and r[4] is not None]
            if vals:
                add("all", mode, name, sum(vals) / len(vals))
    for variant in ("vector_only", "hybrid", "full_scorer"):
        vals = [r[4] for r in metric_rows if r[1] == "ablation" and r[2] == variant and r[4] is not None]
        if vals:
            add("all", "ablation", f"ndcg@{args.k}", sum(vals) / len(vals), variant)
    return metric_rows, findings, per_job, fairness


# ---------------------------------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------------------------------
def fmt(v, pct=False):
    if v is None:
        return "-"
    return f"{v * 100:.0f}%" if pct else f"{v:.3f}" if isinstance(v, float) else str(v)


def markdown_report(args, started, git, metric_rows, findings, per_job, fairness, load_failures, passed):
    k = args.k
    get = {(r[0], r[1], r[2], r[3]): r[4] for r in metric_rows}
    L = [f"# Screening evaluation{(' - ' + args.label) if args.label else ''}", "",
         f"Run {started:%Y-%m-%d %H:%M UTC}, commit `{git or 'unknown'}`, dataset `{ds.DATASET_VERSION}`. "
         f"Embeddings: `{args.embeddings}`. LLM stages: {'on' if args.llm else 'off'}. "
         f"Stage-1 cut {args.stage1}, LLM review cut {args.stage2}, metrics at k={k}. **Result: {'PASS' if passed else 'FAIL'}**", ""]
    if args.embeddings == "hash":
        L += ["> Offline run with the hash stand-in for embeddings: retrieval numbers are not representative of the real model. "
              "Fairness and pipeline checks still apply.", ""]
    if load_failures:
        L += [f"{len(load_failures)} resume(s) failed to process: " + ", ".join(f"{f['ref']} ({f['error']})" for f in load_failures), ""]

    L += ["## Ranking quality (macro average over 4 jobs)", "",
          "| Mode | Stage-1 recall | Stage-2 recall | Precision@%d | Recall@%d | nDCG@%d | MRR |" % (k, k, k), "|---|---|---|---|---|---|---|"]
    for mode in ("applications", "talent"):
        L.append(f"| {'Screening applicants' if mode == 'applications' else 'Talent search'} | "
                 + " | ".join(fmt(get.get(("all", mode, "pipeline", m)), pct=True) for m in
                              (f"stage1_recall@{args.stage1}", "stage2_recall", f"precision@{k}", f"recall@{k}"))
                 + f" | {fmt(get.get(('all', mode, 'pipeline', f'ndcg@{k}')))} | {fmt(get.get(('all', mode, 'pipeline', 'mrr')))} |")
    L += ["", "### Per job", "", "| Job | Mode | Relevant | nDCG@%d | Recall@%d | Top %d (label, score) |" % (k, k, k), "|---|---|---|---|---|---|"]
    for job, info in per_job.items():
        for mode, d in info["modes"].items():
            top = ", ".join(f"{r} ({lab}, {s:.0f})" for r, lab, s in d["top"])
            L.append(f"| {job} | {mode} | {len(info['relevant'])} | {fmt(d['metrics'][f'ndcg@{k}'])} | "
                     f"{fmt(d['metrics'][f'recall@{k}'], pct=True)} | {top} |")

    L += ["", "## What each stage contributes (nDCG@%d, whole pool, no LLM)" % k, "", "| Job | Vector only | + keyword (hybrid) | + skills and experience (full scorer) |", "|---|---|---|---|"]
    for job, info in list(per_job.items()) + [("all", None)]:
        vals = [get.get((job, "ablation", v, f"ndcg@{k}")) for v in ("vector_only", "hybrid", "full_scorer")]
        L.append(f"| {'**macro**' if job == 'all' else job} | " + " | ".join(fmt(v) for v in vals) + " |")

    fns = [f for f in findings if f[0] == "FALSE_NEGATIVE"]
    L += ["", f"## False negatives ({len(fns)})", "",
          "Relevant candidates (label 2-3) missing from the top %d, and why." % k, ""]
    if fns:
        L += ["| Job | Mode | Candidate | Label | Lost at | Reason |", "|---|---|---|---|---|---|"]
        for _, sev, job, ref, d in fns:
            L.append(f"| {job} | {d['mode']} | {ref} | {d['label']} | {d['stage']} | {d['reason']} |")
    else:
        L.append("None.")

    L += ["", "## Counterfactual fairness", "",
          f"Each twin is the control resume with one attribute changed. Tolerance: {args.tolerance:g} point(s). "
          f"Selected = score of {SELECT_THRESHOLD:g} or more.", "",
          "| Attribute | Twins | Mean change | Max abs change | Max rank change | Selection rate by level | Adverse impact ratio |",
          "|---|---|---|---|---|---|---|"]
    for attr, a in sorted(fairness["attributes"].items()):
        rates = ", ".join(f"{lvl} {fmt(r, pct=True)}" for lvl, r in a["selection_rates"].items())
        air = a["adverse_impact_ratio"]
        L.append(f"| {attr} | {a['n']} | {a['mean']:+.2f} | {a['max_abs']:.2f} | {a['max_rank_change']} | {rates} | "
                 f"{fmt(air) if air is not None else '-'}{' (below 0.8)' if air is not None and air < 0.8 else ''} |")
    flagged = [f for f in findings if f[0] == "COUNTERFACTUAL"]
    if flagged:
        L += ["", "Twins outside tolerance:", "", "| Base | Job | Change | Score change | Semantic change | Experience change (yrs) | Skill change |",
              "|---|---|---|---|---|---|---|"]
        for _, sev, job, subj, r in flagged:
            L.append(f"| {r['base']} | {job} | {r['attribute']}={r['level']} | {r['delta']:+.2f} | {r['delta_semantic']:+.2f} | "
                     f"{r['delta_experience_years']:+.2f} | {r['delta_skill']:+.2f} |")
    L += ["", "## How to read this", "",
          "- Synthetic, hand-labelled data (18 resumes, 40 twins): it shows *sensitivity* of the pipeline, not real-world outcomes.",
          "- Selection rates use 5 twins per level, so the adverse impact ratio is indicative only; score changes on identical "
          "resumes are the stronger signal.",
          "- Name, city and career-break changes must not move the score; college tier and phrasing changes are reported as warnings.",
          f"- Stage cut-offs are scaled to the pool ({args.stage1} of 18 vs 100 of the full database in production)."]
    return "\n".join(L) + "\n"


async def store(db, args, started, git, metric_rows, findings, summary, passed):
    async with db.system_tx() as conn:
        run_id = await conn.fetchval(
            """INSERT INTO eval_runs (started_at, finished_at, dataset_version, git_commit, config, summary, passed)
               VALUES ($1, now(), $2, $3, $4, $5, $6) RETURNING run_id""",
            started, ds.DATASET_VERSION, git,
            {"embeddings": args.embeddings, "llm": args.llm, "stage1": args.stage1, "stage2": args.stage2, "k": args.k,
             "tolerance": args.tolerance, "label": args.label}, summary, passed)
        await conn.executemany("INSERT INTO eval_metrics (run_id, job_key, mode, variant, metric, value) VALUES ($1, $2, $3, $4, $5, $6)",
                               [(run_id, *r) for r in metric_rows])
        await conn.executemany(
            "INSERT INTO eval_findings (run_id, kind, severity, job_key, subject, details) VALUES ($1, $2, $3, $4, $5, $6)",
            [(run_id, kind, sev, job, subj[:120], details) for kind, sev, job, subj, details in findings])
    return run_id


async def main(args) -> int:
    configure_environment(args)
    import httpx
    from app import db
    from app.config import get_settings
    from app.main import app
    from app.routers import recruiter as recruiter_mod
    from app.security import limiter
    from app.services import ranking, storage

    blobs = {}

    async def put(key, data, ft):
        blobs[key] = data

    async def get_blob(key):
        return blobs[key]

    async def delete(key):
        blobs.pop(key, None)

    storage.put_resume, storage.get_resume, storage.delete_resume = put, get_blob, delete
    limiter.enabled = False
    started = datetime.now(timezone.utc)
    try:
        git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        git = None

    await db.open_pool()
    try:
        async with db.system_tx() as conn:
            if await conn.fetchval("SELECT count(*) FROM applicants") > 0:
                sys.exit("This database already has applicants. Use an empty throwaway database so results are comparable.")
        candidates = ds.build()
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1))
        async with httpx.AsyncClient(transport=transport, base_url="http://eval") as client:
            loader = Loader(client, db, get_settings())
            print(f"loading {len(candidates)} resumes ...", flush=True)
            rec, company_id, jobs, applicant_of, apps, failures = await load(loader, candidates)
        print("evaluating ...", flush=True)
        metric_rows, findings, per_job, fairness = await evaluate(args, db, ranking, recruiter_mod, rec, company_id, jobs,
                                                                 applicant_of, apps, candidates)
        passed = not any(f[1] == "fail" for f in findings) and not failures
        macro = next((r[4] for r in metric_rows if r[:4] == ("all", "applications", "pipeline", f"ndcg@{args.k}")), None)
        if args.min_ndcg is not None and (macro is None or macro < args.min_ndcg):
            passed = False
            findings.append(("FALSE_NEGATIVE", "fail", None, "quality gate", {"mode": "applications", "label": "-", "stage": "gate",
                             "reason": f"macro nDCG@{args.k} {macro} is below the floor {args.min_ndcg}"}))
        summary = {r[3] if r[2] == "pipeline" else f"{r[2]}:{r[3]}": r[4] for r in metric_rows if r[0] == "all"}
        summary["findings"] = {kind: sum(1 for f in findings if f[0] == kind) for kind in ("FALSE_NEGATIVE", "COUNTERFACTUAL", "ADVERSE_IMPACT")}
        run_id = await store(db, args, started, git, metric_rows, findings, summary, passed)

        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        stem = out / f"eval-{started:%Y%m%d-%H%M%S}"
        report = markdown_report(args, started, git, metric_rows, findings, per_job, fairness, failures, passed)
        stem.with_suffix(".md").write_text(report)
        stem.with_suffix(".json").write_text(json.dumps({"run_id": run_id, "passed": passed, "config": vars(args), "summary": summary,
                                                         "per_job": per_job, "fairness": fairness,
                                                         "findings": [dict(zip(("kind", "severity", "job", "subject", "details"), f)) for f in findings],
                                                         "load_failures": failures}, indent=2, default=str))
        print(report)
        print(f"run {run_id} {'PASSED' if passed else 'FAILED'}; report: {stem.with_suffix('.md')}")
        return 0 if (passed or not args.strict) else 1
    finally:
        await db.close_pool()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
