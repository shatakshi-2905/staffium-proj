"""Labelled evaluation dataset: synthetic resumes, four jobs, graded relevance and counterfactual twins.

Everything is deterministic (no randomness), so two runs on the same code give the same numbers.

Relevance labels (per resume, per job):
    3 = strong match: would shortlist     2 = good match: worth screening
    1 = partial / adjacent                0 = not relevant (default)

Several profiles are deliberate hard cases, each written to probe one known weakness:
    backend_aliases      skills written as aliases ("Postgres", "k8s", "Python3")
    backend_prose        no skills section; dates written as "since 2020"
    keyword_stuffer      lists every hot skill but works in sales (precision test)
    career_changer       long unrelated career + bootcamp (experience-years test)

Counterfactual twins (fairness): five base profiles are copied with exactly ONE attribute changed:
name (4 name sets with different gender/region/religion cues), college tier, city, a career break
(total experience kept equal), and non-native English phrasing. The twins are hidden from talent
search (is_discoverable = false) so they don't distort the quality metrics.
"""
import zlib
from dataclasses import dataclass, field, replace

DATASET_VERSION = "2026.10-v1"
CURRENT_YEAR = 2026


@dataclass(frozen=True)
class Role:
    title: str
    company: str
    start: int
    end: int | None            # None = present
    text: str
    alt_text: str = ""         # non-native-English phrasing of the same facts (fairness twins only)


@dataclass(frozen=True)
class Profile:
    key: str
    headline: str
    roles: tuple[Role, ...]
    skills_line: str | None
    degree: str
    college: str
    edu_years: tuple[int, int]
    projects: str = ""
    summary: str = ""
    labels: dict = field(default_factory=dict)
    since_style: bool = False  # write the latest role as "since YYYY" instead of a date range


@dataclass(frozen=True)
class Candidate:
    ref: str                   # unique id in this dataset
    profile: Profile
    name: str
    city: str
    college: str
    gap: bool = False
    non_native: bool = False
    base: str | None = None    # set on fairness twins: which base profile this is a copy of
    attribute: str | None = None
    level: str | None = None
    discoverable: bool = True

    @property
    def labels(self) -> dict:
        return self.profile.labels

    def resume_text(self) -> str:
        p = self.profile
        roles = list(p.roles)
        lines = [self.name, f"{p.headline} | {self.city}", f"{self.profile.key.replace('_', '.')}@example.test | +91 98450 {zlib.crc32(p.key.encode()) % 100000:05d}"]
        if p.summary:
            lines.append(f"Summary: {p.summary}")
        if p.skills_line:
            lines.append(f"Skills: {p.skills_line}")
        lines.append("")
        lines.append("Experience")
        if self.gap and len(roles) >= 2:
            # Same total experience: earlier roles move back one year to make room for a break.
            latest, earlier = roles[0], [replace(r, start=r.start - 1, end=(r.end - 1 if r.end else None)) for r in roles[1:]]
            break_start = earlier[0].end
            roles = [latest]
            gap_line = f"Career break ({break_start} - {latest.start}): full-time caregiving for a family member."
        else:
            gap_line = None
        for i, r in enumerate(roles):
            text = r.alt_text if (self.non_native and r.alt_text) else r.text
            if i == 0 and p.since_style and r.end is None:
                lines.append(f"{r.title} at {r.company}, since {r.start}: {text}")
            else:
                lines.append(f"{r.title}, {r.company} ({r.start} - {r.end or 'Present'}): {text}")
        if gap_line:
            lines.append(gap_line)
            for r in earlier:
                text = r.alt_text if (self.non_native and r.alt_text) else r.text
                lines.append(f"{r.title}, {r.company} ({r.start} - {r.end or 'Present'}): {text}")
        lines.append("")
        lines.append("Education")
        lines.append(f"{p.degree}, {self.college}, {p.edu_years[0]}-{p.edu_years[1]}")
        if p.projects:
            lines.append("")
            lines.append(f"Projects: {p.projects}")
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------
# Jobs (key, title, required skills, preferred skills, min years, description)
# ---------------------------------------------------------------------------------------------------
JOBS = {
    "backend": ("Backend Engineer", ["Python", "PostgreSQL", "AWS", "Docker"], ["Kafka", "Kubernetes", "FastAPI"], 3,
                "Design and run Python microservices on AWS for a payments platform. You will build FastAPI services, model data "
                "in PostgreSQL, containerise with Docker, publish events to Kafka and own production reliability."),
    "data": ("Data Scientist", ["Python", "Machine Learning", "SQL"], ["PyTorch", "NLP", "AWS"], 2,
             "Build and ship machine learning models for fraud and credit risk. Work with transaction data in SQL and Pandas, "
             "train models in PyTorch and scikit-learn, and productionise NLP features with engineering."),
    "frontend": ("Frontend Engineer", ["React", "TypeScript", "CSS"], ["Next.js", "Jest"], 2,
                 "Own the merchant dashboard: React and TypeScript, a shared component library, accessibility and performance. "
                 "Write tests with Jest and ship with Next.js."),
    "devops": ("Platform Engineer", ["Kubernetes", "Docker", "AWS", "Terraform"], ["Prometheus", "Linux"], 3,
               "Run the platform our engineers ship on: Kubernetes clusters on AWS defined in Terraform, CI/CD pipelines, "
               "observability with Prometheus and Grafana, and on-call for production incidents."),
}

# ---------------------------------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------------------------------
P = {}


def _p(key, headline, roles, skills, degree, college, edu, labels, **kw):
    P[key] = Profile(key, headline, tuple(roles), skills, degree, college, edu, labels=labels, **kw)


_p("backend_senior", "Senior backend engineer",
   [Role("Senior Software Engineer", "Finlytics", 2021, None,
         "Built FastAPI microservices on AWS Lambda and ECS with Docker, tuned PostgreSQL queries, and published payment events to Kafka.",
         "Did the building of FastAPI microservices in AWS Lambda and ECS using Docker, done the tuning of PostgreSQL queries and also payment events publishing in Kafka."),
    Role("Software Engineer", "DataNest", 2018, 2021,
         "Developed Python REST APIs, deployed services on Kubernetes and maintained CI/CD with GitHub Actions.",
         "Developed the Python REST APIs, did deployment of services in Kubernetes and maintained the CI/CD with GitHub Actions.")],
   "Python, FastAPI, PostgreSQL, AWS, Docker, Kafka, Redis, Kubernetes, Git", "B.Tech Computer Science", "VIT Vellore", (2014, 2018),
   {"backend": 3, "devops": 1})

_p("backend_django", "Backend developer",
   [Role("Software Engineer", "ShopEase", 2022, None,
         "Built Django and Flask services backed by PostgreSQL, containerised with Docker, and designed REST APIs for the checkout team.",
         "Made Django and Flask services with PostgreSQL backend, done containerising with Docker and designed REST APIs for checkout team."),
    Role("Software Engineering Intern", "Zoho", 2021, 2022, "Wrote Python scripts and SQL reports.",
         "Wrote the Python scripts and the SQL reports.")],
   "Python, Django, Flask, PostgreSQL, Docker, REST APIs, SQL", "B.Tech Information Technology", "VIT Vellore", (2017, 2021),
   {"backend": 2})

_p("backend_java", "Java backend engineer",
   [Role("Senior Engineer", "BankSoft", 2017, None, "Java and Spring Boot microservices on MySQL with Kafka messaging.")],
   "Java, Spring Boot, MySQL, Kafka, Microservices", "B.Tech Electronics", "DTU Delhi", (2013, 2017), {"backend": 1})

_p("backend_aliases", "Backend engineer",
   [Role("Backend Engineer", "Ledgerly", 2019, None,
         "Python3 services on Amazon Web Services, Postgres for storage, k8s for deploys, everything dockerised.")],
   "Python3, Postgres, Amazon Web Services, k8s, FastAPI, Kafka", "B.E. Computer Engineering", "Pune University", (2015, 2019),
   {"backend": 3, "devops": 1})

_p("backend_prose", "Software engineer, payments",
   [Role("Software Engineer", "Payly", 2020, None,
         "I build Python services that run on AWS, store data in PostgreSQL and ship as Docker containers. I also maintain our FastAPI gateway.")],
   None, "B.Tech Computer Science", "NIT Surathkal", (2016, 2020), {"backend": 3}, since_style=True)

_p("backend_junior", "Junior backend developer",
   [Role("Associate Engineer", "QuickPay", 2025, None, "FastAPI endpoints on PostgreSQL, Docker images, some AWS S3 work.")],
   "Python, FastAPI, PostgreSQL, Docker, AWS", "B.Tech Computer Science", "SRM Chennai", (2021, 2025), {"backend": 1})

_p("keyword_stuffer", "Sales executive",
   [Role("Sales Executive", "TechMart Retail", 2018, None, "Managed enterprise accounts, hit quarterly sales targets and ran client demos.")],
   "Python, AWS, Docker, Kubernetes, React, TypeScript, Machine Learning, PostgreSQL, Terraform, NLP, Kafka, SQL",
   "BBA Marketing", "Christ University", (2015, 2018), {})

_p("ds_nlp", "Data scientist, NLP",
   [Role("Data Scientist", "InsightAI", 2020, None,
         "Trained NLP models with PyTorch and Hugging Face, built features in SQL and Pandas, deployed models on AWS SageMaker.",
         "Did training of NLP models with PyTorch and Hugging Face, made features using SQL and Pandas, done deployment of models in AWS SageMaker."),
    Role("Analytics Engineer", "Mu Sigma", 2017, 2020, "Wrote SQL and Python for churn models and A/B test analysis.",
         "Wrote the SQL and Python for churn models and also A/B tests analysis.")],
   "Python, Machine Learning, NLP, PyTorch, Hugging Face, SQL, Pandas, AWS, scikit-learn", "M.Tech Data Science", "VIT Vellore", (2015, 2017),
   {"data": 3})

_p("ds_analyst", "Data analyst",
   [Role("Data Analyst", "RetailCo", 2021, None, "Built Power BI dashboards, wrote SQL for weekly reporting, some Python in Excel automation.")],
   "SQL, Excel, Power BI, Python, Data Analysis", "B.Com", "Delhi University", (2018, 2021), {"data": 1})

_p("ml_engineer", "Machine learning engineer",
   [Role("ML Engineer", "VisionTech", 2021, None,
         "Computer vision models in PyTorch and OpenCV served with FastAPI in Docker on AWS; MLOps for retraining.")],
   "Python, Machine Learning, PyTorch, Computer Vision, OpenCV, FastAPI, Docker, AWS, MLOps", "M.Sc Computer Science", "IISc Bangalore",
   (2019, 2021), {"data": 2, "backend": 1})

_p("frontend_react", "Frontend engineer",
   [Role("Frontend Engineer", "PixelWorks", 2020, None,
         "Built a React and TypeScript design system, Next.js storefronts, CSS architecture and Jest test suites.",
         "Made a React and TypeScript design system, Next.js storefronts, did CSS architecture and also wrote Jest tests suites."),
    Role("Web Developer", "WebStudio", 2018, 2020, "Built responsive marketing sites with JavaScript, HTML and CSS.",
         "Done building of responsive marketing sites using JavaScript, HTML and CSS.")],
   "React, TypeScript, Next.js, CSS, HTML, Jest, JavaScript", "B.Tech Computer Science", "VIT Vellore", (2014, 2018), {"frontend": 3})

_p("frontend_vue", "Frontend developer",
   [Role("Frontend Developer", "WebCraft", 2021, None, "Vue.js single page apps with JavaScript, HTML and CSS.")],
   "Vue.js, JavaScript, HTML, CSS", "BCA", "Calcutta University", (2018, 2021), {"frontend": 1})

_p("fullstack", "Full-stack developer",
   [Role("Full Stack Developer", "TravelNest", 2020, None, "Node.js and Express APIs with a React and TypeScript front end, MongoDB, Docker on AWS.")],
   "Node.js, Express.js, React, TypeScript, CSS, MongoDB, Docker, AWS", "B.Tech Computer Science", "Manipal", (2016, 2020),
   {"frontend": 2, "backend": 1})

_p("devops_k8s", "Platform engineer",
   [Role("Platform Engineer", "CloudKart", 2018, None,
         "Ran Kubernetes clusters on AWS defined in Terraform, Docker build pipelines, Prometheus and Grafana monitoring, Linux on-call.",
         "Done running of Kubernetes clusters in AWS with Terraform definitions, Docker build pipelines, Prometheus and Grafana monitoring and Linux on-call duty."),
    Role("Linux Administrator", "HCL", 2015, 2018, "Administered Linux servers and wrote Bash and Python automation.",
         "Did administration of Linux servers and wrote the Bash and Python automations.")],
   "Kubernetes, Docker, AWS, Terraform, Prometheus, Grafana, Linux, Python, CI/CD", "B.E. Information Technology", "VIT Vellore", (2011, 2015),
   {"devops": 3, "backend": 1})

_p("sysadmin", "Systems administrator",
   [Role("Systems Administrator", "Infosys", 2016, None, "Linux servers, Bash automation, networking, some Amazon EC2 instances.")],
   "Linux, Bash, Networking, Amazon EC2", "B.Sc Computer Science", "Osmania University", (2013, 2016), {"devops": 1})

_p("career_changer", "Junior developer (career change)",
   [Role("Accountant", "Mehta & Co", 2015, 2024, "Prepared GST filings and monthly closing reports in Excel."),
    Role("Bootcamp Graduate", "CodeCamp", 2025, None, "Built Flask and SQL projects during a 6-month Python bootcamp.")],
   "Python, Flask, SQL, Excel", "B.Com", "Mumbai University", (2012, 2015), {"backend": 1, "data": 1})

_p("devops_mid", "DevOps engineer",
   [Role("DevOps Engineer", "FinServe", 2022, None, "Terraform modules for AWS, Docker images, Jenkins pipelines, first Kubernetes migrations.")],
   "Terraform, AWS, Docker, Jenkins, Kubernetes, Linux", "B.Tech Computer Science", "KIIT Bhubaneswar", (2018, 2022), {"devops": 2})

_p("data_engineer", "Data engineer",
   [Role("Data Engineer", "Streamly", 2019, None, "Apache Spark and Airflow pipelines into Snowflake, Kafka ingestion, Python and SQL on AWS.")],
   "Apache Spark, Airflow, Kafka, Python, SQL, AWS, Snowflake", "B.Tech Computer Science", "BITS Pilani", (2015, 2019),
   {"data": 1, "backend": 1})

# ---------------------------------------------------------------------------------------------------
# Counterfactual design
# ---------------------------------------------------------------------------------------------------
CONTROL = {"name": "Rahul Sharma", "city": "Bengaluru"}
NAME_SETS = {"A": "Rahul Sharma", "B": "Priya Nair", "C": "Mohammed Siddiqui", "D": "Lalremruata Hmar"}
COLLEGE_LEVELS = {"tier1": "IIT Bombay", "tier3": "Government Engineering College, Rewa"}
CITY_LEVELS = {"small_city": "Ranchi"}
FAIRNESS_BASES = {"backend_senior": "backend", "backend_django": "backend", "ds_nlp": "data",
                  "frontend_react": "frontend", "devops_k8s": "devops"}

UNIQUE_NAMES = ["Asha Verma", "Karan Mehta", "Sana Qureshi", "Vikram Singh", "Neha Kapoor", "Arjun Rao", "Meera Iyer",
                "Rohan Das", "Ishita Bose", "Farhan Ali", "Divya Menon", "Aditya Kulkarni", "Pooja Reddy", "Siddharth Jain",
                "Nikhil Joshi", "Ananya Ghosh", "Tanvi Shah", "Gaurav Patil"]


def build() -> list[Candidate]:
    out: list[Candidate] = []
    # One "unique" candidate per profile (used for quality metrics and talent search).
    for i, (key, prof) in enumerate(P.items()):
        if key in FAIRNESS_BASES:
            out.append(Candidate(f"{key}", prof, CONTROL["name"], CONTROL["city"], prof.college))
        else:
            out.append(Candidate(f"{key}", prof, UNIQUE_NAMES[i % len(UNIQUE_NAMES)], "Bengaluru", prof.college))
    # Counterfactual twins of the fairness bases: one attribute changed each.
    for key in FAIRNESS_BASES:
        prof = P[key]
        twin = dict(profile=prof, base=key, discoverable=False)
        for level, name in NAME_SETS.items():
            if name != CONTROL["name"]:
                out.append(Candidate(f"{key}~name~{level}", name=name, city=CONTROL["city"], college=prof.college,
                                     attribute="name", level=level, **twin))
        for level, college in COLLEGE_LEVELS.items():
            out.append(Candidate(f"{key}~college~{level}", name=CONTROL["name"], city=CONTROL["city"], college=college,
                                 attribute="college", level=level, **twin))
        for level, city in CITY_LEVELS.items():
            out.append(Candidate(f"{key}~city~{level}", name=CONTROL["name"], city=city, college=prof.college,
                                 attribute="city", level=level, **twin))
        if len(prof.roles) >= 2:
            out.append(Candidate(f"{key}~gap~break", name=CONTROL["name"], city=CONTROL["city"], college=prof.college,
                                 gap=True, attribute="career_break", level="break", **twin))
        out.append(Candidate(f"{key}~phrasing~non_native", name=CONTROL["name"], city=CONTROL["city"], college=prof.college,
                             non_native=True, attribute="phrasing", level="non_native", **twin))
    return out


def control_levels() -> dict:
    """The level the control (unchanged) candidate has for each attribute."""
    return {"name": "A", "college": "control", "city": "Bengaluru", "career_break": "none", "phrasing": "native"}
