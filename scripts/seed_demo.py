"""Load demo data into a running deployment (for the presentation / viva).

Creates confirmed demo accounts through the Supabase Admin API, then drives the real public API
exactly like the frontend would: company and jobs, applicant profiles, resume uploads (real PDFs,
real S3, real Bedrock), applications, AI screening, shortlist/reject, interviews and an offer.

Usage (from the repo root):
    pip install httpx pymupdf
    API_BASE=https://your-api.onrender.com \
    SUPABASE_URL=https://<ref>.supabase.co \
    SUPABASE_SERVICE_ROLE_KEY=<service role key>   # keep this off any server and out of git \
    DEMO_PASSWORD='Demo-Passw0rd!' \
    python scripts/seed_demo.py

Every demo account uses DEMO_PASSWORD, so you can sign in as any of them during the demo:
recruiter priya.demo@shortlist.test, applicants <firstname>.demo@shortlist.test.
"""
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import httpx
import pymupdf

API = os.environ["API_BASE"].rstrip("/")
SUPABASE = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
PASSWORD = os.environ.get("DEMO_PASSWORD", "Demo-Passw0rd!")

PEOPLE = [
    ("Asha", "Verma", "Backend engineer, payments", "Bengaluru", 30, True,
     "Senior Software Engineer, Finlytics (2021-01 to Present): FastAPI microservices on AWS Lambda and Docker, PostgreSQL tuning, "
     "Redis caching, Kafka event pipelines.\nSoftware Engineer, DataNest (2019-06 to 2020-12): Python REST APIs, Kubernetes, CI/CD with GitHub Actions.",
     "Python, FastAPI, PostgreSQL, AWS, Docker, Kubernetes, Redis, Kafka, REST APIs, Git", "B.Tech Computer Science, VIT Vellore, 2015-2019"),
    ("Karan", "Mehta", "Platform engineer", "Pune", 60, True,
     "Platform Engineer, CloudKart (2020-03 to Present): Terraform, Kubernetes, AWS EC2 and Lambda, Python tooling, Prometheus and Grafana.\n"
     "DevOps Engineer, Infosys (2018-07 to 2020-02): Jenkins pipelines, Linux, Docker.",
     "Python, AWS, Docker, Kubernetes, Terraform, Linux, Jenkins, Prometheus, Grafana", "B.E. Information Technology, Pune University, 2014-2018"),
    ("Meera", "Iyer", "Data scientist, NLP", "Chennai", 45, True,
     "Data Scientist, InsightAI (2021-07 to Present): NLP models with PyTorch and Hugging Face, SQL, Pandas, deployed on AWS.\n"
     "ML Intern, TCS (2020-08 to 2021-06): scikit-learn churn models.",
     "Python, PyTorch, Hugging Face, NLP, Machine Learning, SQL, Pandas, AWS, scikit-learn", "M.Tech Data Science, IIT Madras, 2019-2021"),
    ("Rohan", "Das", "Frontend developer", "Kolkata", 30, True,
     "Frontend Developer, PixelWorks (2022-01 to Present): React, TypeScript, Next.js design systems, Jest, Figma handoff.\n"
     "Intern, WebCraft (2021-06 to 2021-12): HTML, CSS, JavaScript.",
     "React, TypeScript, JavaScript, Next.js, HTML, CSS, Jest, Figma", "BCA, Calcutta University, 2018-2021"),
    ("Sana", "Qureshi", "Backend developer", "Hyderabad", 15, True,
     "Software Engineer, ShopEase (2022-07 to Present): Django and Flask services, PostgreSQL, Docker, REST APIs.\n"
     "Intern, Zoho (2021-12 to 2022-06): Java Spring Boot.",
     "Python, Django, Flask, PostgreSQL, Docker, REST APIs, Java, Spring Boot", "B.Tech Computer Science, JNTU Hyderabad, 2018-2022"),
    ("Vikram", "Singh", "Java engineer", "Delhi", 90, False,
     "Senior Engineer, BankSoft (2017-04 to Present): Java, Spring Boot microservices, Oracle Database, Kafka, Kubernetes on Azure.\n"
     "Engineer, HCL (2014-07 to 2017-03): Java, SQL Server.",
     "Java, Spring Boot, Kafka, Kubernetes, Azure, Oracle Database, SQL Server, Microservices", "B.Tech Electronics, DTU Delhi, 2010-2014"),
]

JOBS = [
    ("Senior Backend Engineer", "Engineering", 3, "OPEN", ["Python", "PostgreSQL", "AWS", "Docker"], ["Kafka", "Kubernetes", "FastAPI"],
     "You'll own the services that move money for 40,000 merchants: FastAPI microservices on AWS, PostgreSQL at scale and event "
     "pipelines with Kafka. You'll design APIs, tune queries, keep latency low during festival-season peaks, and mentor two junior engineers."),
    ("Data Scientist, Risk", "Data", 2, "OPEN", ["Python", "Machine Learning", "SQL"], ["PyTorch", "NLP", "AWS"],
     "Build fraud and credit-risk models that decide in milliseconds. Work with transaction data in SQL and Pandas, train models in "
     "PyTorch and scikit-learn, and ship them behind APIs with engineering."),
    ("Frontend Engineer", "Engineering", 2, "OPEN", ["React", "TypeScript", "CSS"], ["Next.js", "Jest"],
     "Own the merchant dashboard: React and TypeScript, a component library shared across three products, and accessibility from the first commit."),
]


def pdf(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def admin_user(c: httpx.Client, email: str, role: str) -> None:
    r = c.post(f"{SUPABASE}/auth/v1/admin/users", headers={"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"},
               json={"email": email, "password": PASSWORD, "email_confirm": True, "user_metadata": {"role": role}})
    if r.status_code not in (200, 201) and "already" not in r.text:
        sys.exit(f"could not create {email}: {r.status_code} {r.text}")


def login(c: httpx.Client, email: str, portal: str) -> dict:
    for attempt in range(3):
        r = c.post(f"{API}/api/auth/login", json={"email": email, "password": PASSWORD, "portal": portal})
        if r.status_code == 429:
            time.sleep(15)   # the API's own login rate limit
            continue
        r.raise_for_status()
        return {"Authorization": f"Bearer {r.json()['access_token']}"}
    sys.exit("login rate-limited; wait a minute and re-run")


def call(c, method, path, headers, **kw):
    r = c.request(method, f"{API}{path}", headers=headers, **kw)
    if r.status_code >= 400:
        print(f"  ! {method} {path}: {r.status_code} {r.text[:160]}")
    return r


def main() -> None:
    with httpx.Client(timeout=90) as c:
        print("recruiter + company")
        admin_user(c, "priya.demo@shortlist.test", "RECRUITER")
        rec = login(c, "priya.demo@shortlist.test", "recruiter")
        call(c, "POST", "/api/companies", rec, json={
            "company_name": "Northwind Labs", "industry": "Fintech", "location": "Bengaluru", "website": "https://northwind.example",
            "description": "Payments infrastructure for small businesses across India.",
            "first_name": "Priya", "last_name": "Nair", "designation": "Talent partner"})
        skills = {s["skill_name"]: s["skill_id"] for s in call(c, "GET", "/api/skills", rec).json()["items"]}

        print("jobs")
        jobs = {}
        for title, dept, min_exp, status, req, pref, desc in JOBS:
            body = {"title": title, "department": dept, "description": desc, "location": "Bengaluru", "min_experience": min_exp,
                    "min_salary": 1800000, "max_salary": 3200000, "status": status,
                    "skills": [{"skill_id": skills[s]} for s in req] + [{"skill_id": skills[s], "is_required": False} for s in pref]}
            jobs[title] = call(c, "POST", "/api/recruiter/jobs", rec, json=body).json()["job_id"]

        print("applicants + resumes")
        heads = {}
        for first, last, headline, city, notice, discoverable, exp, skill_line, edu in PEOPLE:
            email = f"{first.lower()}.demo@shortlist.test"
            admin_user(c, email, "APPLICANT")
            h = heads[first] = login(c, email, "applicant")
            call(c, "POST", "/api/applicant/profile", h, json={
                "first_name": first, "last_name": last, "headline": headline, "location": city, "notice_period_days": notice,
                "is_discoverable": discoverable, "linkedin_url": f"https://www.linkedin.com/in/{first.lower()}-{last.lower()}-demo"})
            text = f"{first} {last}\n{headline} | {city}\n{email}\n\nExperience\n{exp}\n\nSkills: {skill_line}\n\nEducation: {edu}\n"
            call(c, "POST", "/api/resumes", h, files={"file": (f"{first}_{last}_CV.pdf", pdf(text), "application/pdf")})

        print("waiting for resume processing")
        for first, h in heads.items():
            for _ in range(30):
                items = call(c, "GET", "/api/resumes", h).json()["items"]
                if items and items[0]["processing_status"] in ("COMPLETED", "FAILED"):
                    break
                time.sleep(2)

        print("applications")
        plan = {"Asha": ["Senior Backend Engineer"], "Karan": ["Senior Backend Engineer"], "Sana": ["Senior Backend Engineer"],
                "Vikram": ["Senior Backend Engineer"], "Rohan": ["Frontend Engineer", "Senior Backend Engineer"], "Meera": ["Data Scientist, Risk"]}
        apps = {}
        for first, titles in plan.items():
            for t in titles:
                r = call(c, "POST", f"/api/jobs/{jobs[t]}/apply", heads[first])
                if r.status_code == 201:
                    apps[(first, t)] = r.json()["application_id"]

        print("AI screening")
        for t in jobs:
            r = call(c, "POST", f"/api/recruiter/jobs/{jobs[t]}/screen", rec, json={"use_ai": True})
            print(f"  {t}: {r.json()}")
            time.sleep(7)   # stay under the search/screening rate limit

        be = "Senior Backend Engineer"
        for first, status in (("Asha", "SHORTLISTED"), ("Karan", "SHORTLISTED"), ("Rohan", "REJECTED"), ("Meera", "SHORTLISTED")):
            key = (first, be if first != "Meera" else "Data Scientist, Risk")
            call(c, "PATCH", f"/api/recruiter/applications/{apps[key]}/status", rec, json={"status": status})

        print("interviews + offer")
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=5, minute=30, second=0, microsecond=0)
        call(c, "POST", "/api/recruiter/interviews", rec, json={"application_id": apps[("Asha", be)], "scheduled_at": tomorrow.isoformat(),
             "duration_minutes": 60, "interview_type": "TECHNICAL", "meeting_link": "https://meet.google.com/abc-defg-hij"})
        call(c, "POST", "/api/recruiter/interviews", rec, json={"application_id": apps[("Meera", "Data Scientist, Risk")],
             "scheduled_at": (tomorrow + timedelta(days=2)).isoformat(), "duration_minutes": 45, "interview_type": "VIDEO"})
        call(c, "POST", "/api/recruiter/offers", rec, json={"application_id": apps[("Karan", be)], "salary": 2850000,
             "joining_date": str(date.today() + timedelta(days=30)), "expires_in_days": 7})
        print("done. Sign in as priya.demo@shortlist.test (recruiter) or asha.demo@shortlist.test (applicant).")


if __name__ == "__main__":
    main()
