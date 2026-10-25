// Applicant portal views.
import { del, get, patch, post, put, upload } from "./api.js";
import { startApp } from "./shell.js";
import { notificationsPage } from "./views-common.js";
import {
  REVIEW_FIELD, REVIEW_OUTCOME, badge, busy, chips, clear, confirmDialog, empty, errorText, extLink, field, fmtAgo, fmtDate, fmtDateTime, fmtMoney,
  fmtSalaryRange, fmtYears, h, humanize, icon, input, loading, modal, pageHead, pager, panel, processing, readForm, score, select,
  reviewBadge, showFormError, table, tabs, textarea, toast,
} from "./ui.js";

const NAV = [
  { group: "Job search" },
  { key: "dashboard", path: "", label: "Dashboard", icon: "home" },
  { key: "jobs", path: "jobs", label: "Find jobs", icon: "search" },
  { key: "applications", path: "applications", label: "Applications", icon: "layers",
    children: [{ key: "reviews", path: "reviews", label: "Review requests", icon: "compare" }] },
  { key: "interviews", path: "interviews", label: "Interviews", icon: "calendar" },
  { group: "You" },
  { key: "profile", path: "profile", label: "Profile", icon: "user" },
  { key: "resume", path: "resume", label: "Resume", icon: "file" },
  { key: "notifications", path: "notifications", label: "Notifications", icon: "bell" },
];

const OPEN_STAGES = ["APPLIED", "SCREENING", "SHORTLISTED", "INTERVIEW", "OFFER"];

// =============================================================================================
// Onboarding
// =============================================================================================
function onboarding(el, me, { done }) {
  const form = h("form", { class: "form", id: "onboard-form", novalidate: true },
    h("div", { class: "form-row" },
      field("First name", input("first_name", { required: true, maxlength: 100, autocomplete: "given-name" })),
      field("Last name", input("last_name", { required: true, maxlength: 100, autocomplete: "family-name" }))),
    field("Headline", input("headline", { maxlength: 160, placeholder: "Backend engineer, 3 years in fintech" }),
      { hint: "One line recruiters see next to your name." }),
    h("div", { class: "form-row" },
      field("City", input("location", { maxlength: 150, autocomplete: "address-level2" })),
      field("Phone", input("phone", { type: "tel", maxlength: 20, autocomplete: "tel" }))),
    h("label", { class: "check" }, h("input", { type: "checkbox", name: "is_discoverable", checked: true }),
      "Let recruiters find my profile in talent search"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showFormError(form, null);
    await busy(e.submitter, async () => {
      try {
        await post("/api/applicant/profile", readForm(form));
        done("#/resume?setup=1");
      } catch (err) { showFormError(form, err); }
    });
  });
  clear(el, pageHead("Set up your profile", `Signed in as ${me.email}. Next you'll upload your resume.`),
    h("div", { class: "narrow" }, panel("Your details", form, {
      actions: h("span", { class: "step-meta" }, "Step 1 of 2"),
      footer: h("button", { class: "btn btn-primary", type: "submit", form: "onboard-form" }, "Continue"),
    })));
}

// =============================================================================================
// Dashboard
// =============================================================================================
async function dashboard(ctx) {
  const [d, profile, rec] = await Promise.all([
    get("/api/applicant/dashboard"), get("/api/applicant/profile"), get("/api/jobs/recommended").catch(() => ({ items: [] })),
  ]);
  if (!ctx.alive()) return;
  const counts = d.applications;
  const active = OPEN_STAGES.reduce((n, s) => n + (counts[s] || 0), 0);
  const first = profile.profile.first_name;

  const resumePanel = d.resume
    ? panel("Resume", h("div", { class: "stack-sm" },
        h("div", { class: "row-between" }, h("span", { class: "title-cell" }, d.resume.file_name), badge(d.resume.processing_status)),
        h("span", { class: "muted small" }, `Version ${d.resume.version}, uploaded ${fmtAgo(d.resume.uploaded_at)}`),
        d.resume.processing_status === "COMPLETED" ? h("span", { class: "small" }, `${d.resume.skills_found} skills recognised`) : null,
        d.resume.processing_error ? h("div", { class: "callout warn" }, icon("alert"), d.resume.processing_error) : null,
        h("a", { class: "btn btn-sm", href: "#/resume", style: { alignSelf: "flex-start" } }, "Manage resume")))
    : panel("Resume", empty("No resume yet", "Upload one to apply and to get matched to roles.",
        h("a", { class: "btn btn-primary", href: "#/resume" }, icon("upload"), "Upload resume")));

  const missing = profile.completeness.missing;
  const checklist = panel(`Profile ${profile.completeness.percent}% complete`,
    missing.length ? h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px", color: "var(--text-muted)" } },
      missing.map((m) => h("li", {}, m)))
      : h("p", { class: "muted small" }, "Everything recruiters look for is filled in."),
    { actions: missing.length ? h("a", { class: "btn btn-sm", href: "#/profile" }, "Edit profile") : null });

  const recTable = table([
    { label: "Role", render: (j) => h("div", {}, h("div", { class: "title-cell" }, j.title), h("div", { class: "sub" }, j.company_name)) },
    { label: "Match", render: (j) => score(j.match) },
    { label: "Missing", render: (j) => chips(j.missing_skills.slice(0, 3), "missing") },
  ], rec.items.slice(0, 5), {
    onRowClick: (j) => { window.location.hash = `#/jobs/${j.job_id}`; },
    emptyState: empty("No matches yet", d.resume ? "New roles that fit your resume will appear here." : "Upload your resume to see roles that fit."),
  });

  const interviews = d.upcoming_interviews.length
    ? h("div", { class: "list" }, d.upcoming_interviews.map((i) => h("div", { class: "list-item" },
        h("div", { class: "grow" }, h("div", { class: "title-cell" }, i.job_title), h("div", { class: "sub" }, `${i.company_name}, ${humanize(i.interview_type)}`)),
        h("div", { class: "small nowrap" }, fmtDateTime(i.scheduled_at)))))
    : h("p", { class: "muted small panel-body" }, "Nothing scheduled.");

  clear(ctx.el,
    pageHead(`Welcome back, ${first}`, "Your applications, interviews and the roles that fit you best."),
    h("div", { class: "stack" },
      h("div", { class: "kpis" },
        kpi("Active applications", active), kpi("Interviews ahead", d.upcoming_interviews.length),
        kpi("Offers to answer", d.pending_offers), kpi("Hired", counts.HIRED || 0)),
      h("div", { class: "split" },
        h("div", { class: "stack" },
          panel("Recommended for you", recTable, { flush: true, actions: h("a", { class: "btn btn-sm", href: "#/jobs" }, "All jobs") }),
          panel("Upcoming interviews", interviews, { flush: true })),
        h("div", { class: "stack" }, resumePanel, checklist))));
}

const kpi = (label, value) => h("div", { class: "kpi" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value));

// =============================================================================================
// Profile
// =============================================================================================
const SECTION_FORMS = {
  experience: {
    title: "Experience", idKey: "experience_id", add: "Add experience",
    fields: () => [
      h("div", { class: "form-row" }, field("Company", input("company_name", { required: true, maxlength: 200 })),
        field("Title", input("designation", { required: true, maxlength: 150 }))),
      h("div", { class: "form-row" }, field("Start date", input("start_date", { type: "date", required: true })),
        field("End date", input("end_date", { type: "date" }))),
      h("label", { class: "check" }, h("input", { type: "checkbox", name: "is_current" }), "I currently work here"),
      field("What you did", textarea("description", { maxlength: 4000 })),
    ],
    render: (x) => [h("div", { class: "entry-title" }, `${x.designation}, ${x.company_name}`),
      h("div", { class: "sub" }, `${fmtDate(x.start_date)} - ${x.is_current ? "Present" : fmtDate(x.end_date)}`)],
  },
  education: {
    title: "Education", idKey: "education_id", add: "Add education",
    fields: () => [
      field("Institution", input("institution", { required: true, maxlength: 200 })),
      h("div", { class: "form-row" }, field("Degree", input("degree", { required: true, maxlength: 120, placeholder: "B.Tech" })),
        field("Field of study", input("field_of_study", { maxlength: 120 }))),
      h("div", { class: "form-row" }, field("Start year", input("start_year", { type: "number", min: 1950, max: 2100 })),
        field("End year", input("end_year", { type: "number", min: 1950, max: 2100 })),
        field("CGPA", input("cgpa", { type: "number", min: 0, max: 10, step: "0.01" }))),
    ],
    render: (x) => [h("div", { class: "entry-title" }, `${x.degree}${x.field_of_study ? `, ${x.field_of_study}` : ""}`),
      h("div", { class: "sub" }, [x.institution, [x.start_year, x.end_year].filter(Boolean).join(" - "), x.cgpa ? `CGPA ${x.cgpa}` : null].filter(Boolean).join(", "))],
  },
  projects: {
    title: "Projects", idKey: "project_id", add: "Add project",
    fields: () => [
      field("Title", input("title", { required: true, maxlength: 200 })),
      field("Technologies", input("technologies", { maxlength: 600, placeholder: "Python, FastAPI, PostgreSQL" }), { hint: "Separate with commas." }),
      field("Link", input("project_url", { type: "url", maxlength: 500, placeholder: "https://" })),
      field("Description", textarea("description", { maxlength: 4000 })),
    ],
    transform: (d) => ({ ...d, technologies: (d.technologies || "").split(",").map((t) => t.trim()).filter(Boolean).slice(0, 15) }),
    render: (x) => [h("div", { class: "entry-title" }, x.title), x.technologies?.length ? h("div", { class: "sub" }, x.technologies.join(", ")) : null,
      x.project_url ? h("div", { class: "small" }, extLink(x.project_url)) : null],
  },
  certifications: {
    title: "Certifications", idKey: "certification_id", add: "Add certification",
    fields: () => [
      field("Name", input("name", { required: true, maxlength: 200 })),
      h("div", { class: "form-row" }, field("Issuer", input("issuer", { maxlength: 200 })), field("Issued", input("issue_date", { type: "date" }))),
      field("Credential link", input("credential_url", { type: "url", maxlength: 500, placeholder: "https://" })),
    ],
    render: (x) => [h("div", { class: "entry-title" }, x.name), h("div", { class: "sub" }, [x.issuer, x.issue_date ? fmtDate(x.issue_date) : null].filter(Boolean).join(", ")),
      x.credential_url ? h("div", { class: "small" }, extLink(x.credential_url, "View credential")) : null],
  },
};

// Each profile section names its id field explicitly. If the API ever renames it, fall back to the
// first "<something>_id" key (and say so in the console) instead of deleting the wrong thing.
function entryId(entry, idKey) {
  if (entry[idKey] !== undefined) return entry[idKey];
  const guess = Object.keys(entry).find((k) => k.endsWith("_id") && k !== "applicant_id");
  console.warn(`Expected "${idKey}" on a profile entry; using "${guess}" instead.`);
  return entry[guess];
}

async function profilePage(ctx) {
  const [data, skillDict] = await Promise.all([get("/api/applicant/profile"), get("/api/skills")]);
  if (!ctx.alive()) return;
  const p = data.profile;

  const form = h("form", { class: "form", id: "profile-form", novalidate: true },
    h("div", { class: "form-row" }, field("First name", input("first_name", { value: p.first_name, required: true, maxlength: 100 })),
      field("Last name", input("last_name", { value: p.last_name, required: true, maxlength: 100 }))),
    field("Headline", input("headline", { value: p.headline || "", maxlength: 160 })),
    h("div", { class: "form-row" }, field("City", input("location", { value: p.location || "", maxlength: 150 })),
      field("Phone", input("phone", { value: p.phone || "", type: "tel", maxlength: 20 })),
      field("Notice period (days)", input("notice_period_days", { value: p.notice_period_days ?? "", type: "number", min: 0, max: 365 }))),
    h("div", { class: "form-row" }, field("LinkedIn", input("linkedin_url", { value: p.linkedin_url || "", type: "url", placeholder: "https://" })),
      field("GitHub", input("github_url", { value: p.github_url || "", type: "url", placeholder: "https://" })),
      field("Portfolio", input("portfolio_url", { value: p.portfolio_url || "", type: "url", placeholder: "https://" }))),
    h("label", { class: "check" }, h("input", { type: "checkbox", name: "is_discoverable", checked: p.is_discoverable }),
      "Let recruiters find my profile in talent search"),
    h("p", { class: "faint xs" }, "When this is off, only companies you apply to can see your profile and resume."));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showFormError(form, null);
    await busy(e.submitter, async () => {
      try { await patch("/api/applicant/profile", readForm(form)); toast("Changes saved"); } catch (err) { showFormError(form, err); }
    });
  });

  // Skills
  const have = new Set(data.skills.map((s) => s.skill_id));
  const skillSelect = h("select", { name: "skill_id", "aria-label": "Skill" }, h("option", { value: "" }, "Choose a skill"));
  const groups = {};
  for (const s of skillDict.items) (groups[s.category || "Other"] ??= []).push(s);
  for (const [cat, list] of Object.entries(groups)) {
    skillSelect.append(h("optgroup", { label: cat }, list.filter((s) => !have.has(s.skill_id)).map((s) => h("option", { value: s.skill_id }, s.skill_name))));
  }
  const levelSelect = select("proficiency_level", [["", "Level (optional)"], "BEGINNER", "INTERMEDIATE", "ADVANCED", "EXPERT"], "", { "aria-label": "Proficiency" });
  const addSkill = h("form", { class: "row", novalidate: true }, h("div", { class: "grow" }, skillSelect), h("div", { style: { width: "170px" } }, levelSelect),
    h("button", { class: "btn", type: "submit" }, icon("plus"), "Add"));
  addSkill.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!skillSelect.value) return;
    await busy(addSkill.querySelector("button"), async () => {
      try {
        await put("/api/applicant/skills", { skill_id: Number(skillSelect.value), proficiency_level: levelSelect.value || null });
        ctx.rerender();
      } catch (err) { toast(errorText(err), "error"); }
    });
  });
  const skillChips = data.skills.length ? h("div", { class: "chips" }, data.skills.map((s) => h("span", { class: "chip", title: s.source === "MANUAL" ? "Added by you" : "Found in your resume" },
    s.skill_name, s.proficiency_level ? h("span", { class: "faint" }, humanize(s.proficiency_level)) : null,
    h("button", { type: "button", "aria-label": `Remove ${s.skill_name}`, onclick: async () => {
      try { await del(`/api/applicant/skills/${s.skill_id}`); ctx.rerender(); } catch (err) { toast(errorText(err), "error"); }
    } }, "×"))))
    : h("p", { class: "muted small" }, "No skills yet. Upload a resume and we'll pick them up, or add them here.");

  const sections = Object.entries(SECTION_FORMS).map(([key, cfg]) => {
    const items = data[key];
    const list = items.length ? h("div", {}, items.map((x) => h("div", { class: "entry row-between" },
      h("div", { class: "grow" }, cfg.render(x), x.source === "RESUME" ? h("span", { class: "faint xs" }, "Imported from your resume") : null),
      h("button", { class: "btn btn-ghost btn-sm", "aria-label": "Delete", onclick: async () => {
        if (!(await confirmDialog("Delete this entry?", "This can't be undone.", "Delete", true))) return;
        try { await del(`/api/applicant/${key}/${entryId(x, cfg.idKey)}`); ctx.rerender(); }
        catch (err) { toast(errorText(err), "error"); }
      } }, icon("trash")))))
      : h("p", { class: "muted small" }, `No ${cfg.title.toLowerCase()} added.`);
    return panel(cfg.title, list, { actions: h("button", { class: "btn btn-sm", onclick: () => openSectionForm(key, cfg, ctx) }, icon("plus"), cfg.add) });
  });

  clear(ctx.el,
    pageHead("Profile", "What recruiters see when they open your application."),
    h("div", { class: "split" },
      h("div", { class: "stack" }, panel("Personal details", form, { footer: h("button", { class: "btn btn-primary", type: "submit", form: "profile-form" }, "Save changes") }), panel("Skills", h("div", { class: "stack" }, skillChips, addSkill)), ...sections),
      h("div", { class: "stack" },
        panel(`${data.completeness.percent}% complete`, data.completeness.missing.length
          ? h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px", color: "var(--text-muted)" } }, data.completeness.missing.map((m) => h("li", {}, m)))
          : h("p", { class: "muted small" }, "Your profile is complete.")),
        panel("Experience", h("div", { class: "stack-sm" }, h("div", { class: "kpi", style: { padding: 0, border: 0 } },
          h("div", { class: "value" }, fmtYears(p.experience_years))),
          h("p", { class: "muted small" }, "Calculated from your roles. Overlapping jobs count once."))))));
}

function openSectionForm(key, cfg, ctx) {
  const form = h("form", { class: "form", novalidate: true }, cfg.fields());
  const submit = h("button", { class: "btn btn-primary", type: "button" }, "Save");
  const { close } = modal({ title: cfg.add, body: form, footer: [h("button", { class: "btn", type: "button", onclick: () => close() }, "Cancel"), submit] });
  const go = async () => {
    showFormError(form, null);
    let data = readForm(form);
    if (cfg.transform) data = cfg.transform(data);
    await busy(submit, async () => {
      try { await post(`/api/applicant/${key}`, data); close(); ctx.rerender(); } catch (err) { showFormError(form, err); }
    });
  };
  submit.addEventListener("click", go);
  form.addEventListener("submit", (e) => { e.preventDefault(); go(); });
}

// =============================================================================================
// Resume
// =============================================================================================
async function resumePage(ctx) {
  const list = await get("/api/resumes");
  if (!ctx.alive()) return;
  const active = list.items.find((r) => r.is_active);
  const analysis = active?.processing_status === "COMPLETED" ? await get(`/api/resumes/${active.resume_id}`) : null;
  if (!ctx.alive()) return;

  const setup = ctx.query.get("setup") === "1";   // arrived from onboarding
  const fileInput = h("input", { type: "file", accept: ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document", "aria-label": "Choose resume file" });
  const drop = h("label", { class: "drop" }, fileInput, icon("upload"),
    h("strong", {}, "Click or drag your resume here"),
    h("span", { class: "small" }, "PDF or Word (.docx), up to 5 MB. Text-based files only; scanned images can't be read."));
  const uploadBody = h("div", { class: "stack" }, drop);
  const send = async (file) => {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) { toast("That file is larger than 5 MB", "error"); return; }
    const fd = new FormData();
    fd.append("file", file);
    clear(uploadBody, processing("Uploading your resume", "Sending the file. Keep this page open."));
    try {
      const res = await upload("/api/resumes", fd);
      if (ctx.alive()) clear(uploadBody, processing("Reading your resume", "We're picking out your roles, education and skills. This usually takes a few seconds."));
      await waitForProcessing(res.resume_id, ctx);
      if (ctx.alive()) ctx.rerender();
    } catch (err) { if (ctx.alive()) clear(uploadBody, drop, h("div", { class: "form-error", role: "alert" }, errorText(err))); }
  };
  // Reset after reading so picking the same file again (e.g. after a failed upload) still fires "change".
  fileInput.addEventListener("change", () => { const f = fileInput.files[0]; fileInput.value = ""; send(f); });
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); send(e.dataTransfer.files[0]); });

  const versions = table([
    { label: "Version", render: (r) => h("span", { class: "num" }, `v${r.version}`) },
    { label: "File", render: (r) => h("div", {}, h("div", { class: "title-cell" }, r.file_name), r.is_active ? h("div", { class: "sub" }, "Current") : null) },
    { label: "Status", render: (r) => h("div", {}, badge(r.processing_status), r.processing_error ? h("div", { class: "sub" }, r.processing_error) : null) },
    { label: "Uploaded", render: (r) => fmtAgo(r.uploaded_at) },
    { label: "", class: "right", render: (r) => h("button", { class: "btn btn-sm", onclick: () => download(r.resume_id) }, icon("download"), "Download") },
  ], list.items, { emptyState: empty("No uploads yet", "Your versions will be listed here.") });

  let analysisPanel = null;
  if (analysis) {
    const a = analysis.analysis;
    analysisPanel = panel("What we found", h("div", { class: "stack" },
      a.summary ? h("p", { class: "quote" }, a.summary) : null,
      h("dl", { class: "dl" },
        h("dt", {}, "Experience"), h("dd", {}, fmtYears(a.experience_years)),
        h("dt", {}, "Read by"), h("dd", {}, a.extracted_by === "llm" ? "AI extraction" : "Rule-based parser"),
        h("dt", {}, "Roles"), h("dd", {}, a.experience.length ? a.experience.map((x) => `${x.title}, ${x.company}`).join("; ") : "None found"),
        h("dt", {}, "Education"), h("dd", {}, a.education.length ? a.education.map((x) => [x.degree, x.institution].filter(Boolean).join(", ")).join("; ") : "None found")),
      h("div", { class: "stack-sm" }, h("span", { class: "label" }, `Skills recognised (${a.skills.length})`), chips(a.skills, "have")),
      a.unrecognised_skills.length ? h("div", { class: "stack-sm" }, h("span", { class: "label" }, "Mentioned but not in our skill list"), chips(a.unrecognised_skills)) : null,
      h("p", { class: "faint xs" }, "Recognised skills were added to your profile. Anything you added by hand stays as you set it.")));
  }

  const setupNote = setup ? h("div", { class: "callout info" }, icon("info"), active
    ? h("div", { class: "row grow" }, h("span", { class: "grow" }, "Your resume is added and your profile is ready."), h("a", { class: "btn btn-primary btn-sm", href: "#/" }, "Go to dashboard"))
    : h("span", {}, "Your profile is saved. Upload your resume so we can match you to roles. ", h("a", { href: "#/" }, "Skip for now"))) : null;

  clear(ctx.el,
    pageHead("Resume", "Your resume is read once when you upload it. Every application uses the version that's current when you apply."),
    h("div", { class: "stack", style: { maxWidth: "900px" } }, setupNote,
      panel(active ? "Upload a new version" : "Upload your resume", uploadBody, { actions: setup ? h("span", { class: "step-meta" }, "Step 2 of 2") : null }),
      analysisPanel,
      panel("Versions", versions, { flush: true })));
}

async function waitForProcessing(resumeId, ctx) {
  for (let i = 0; i < 40 && ctx.alive(); i++) {
    await new Promise((r) => setTimeout(r, 1500));
    const list = await get("/api/resumes");
    const r = list.items.find((x) => x.resume_id === resumeId);
    if (!r || ["COMPLETED", "FAILED"].includes(r.processing_status)) {
      if (r?.processing_status === "COMPLETED") toast("Resume processed");
      return;
    }
  }
}

async function download(resumeId) {
  try {
    const { url } = await get(`/api/resumes/${resumeId}/download`);
    if (/^https:\/\//.test(url) || window.location.hostname === "localhost") window.location.assign(url);
  } catch (err) { toast(errorText(err), "error"); }
}

// =============================================================================================
// Jobs
// =============================================================================================
async function jobsPage(ctx) {
  const tab = ctx.query.get("tab") || "recommended";
  const body = h("div");
  clear(ctx.el, pageHead("Find jobs", "Open roles, ranked against your resume when you're on Recommended."),
    tabs([["recommended", "Recommended"], ["all", "All jobs"]], tab, (t) => { window.location.hash = `#/jobs?tab=${t}`; }), body);

  if (tab === "recommended") {
    clear(body, loading());
    const rec = await get("/api/jobs/recommended");
    if (!ctx.alive()) return;
    clear(body, panel(null, table([
      { label: "Role", render: (j) => h("div", {}, h("div", { class: "title-cell" }, j.title), h("div", { class: "sub" }, [j.company_name, j.location, j.is_remote ? "Remote" : null].filter(Boolean).join(", "))) },
      { label: "Match", render: (j) => score(j.match) },
      { label: "You have", render: (j) => chips(j.matched_skills.slice(0, 4), "have") },
      { label: "Missing", render: (j) => chips(j.missing_skills.slice(0, 4), "missing") },
      { label: "Experience", render: (j) => h("span", { class: j.meets_experience ? "" : "faint" }, `${Number(j.min_experience)}+ yrs`) },
    ], rec.items, {
      onRowClick: (j) => { window.location.hash = `#/jobs/${j.job_id}`; },
      emptyState: rec.has_resume ? empty("No open roles to suggest", "Try the full job list.")
        : empty("Upload a resume to get matches", "We compare your resume to every open role.", h("a", { class: "btn btn-primary", href: "#/resume" }, "Upload resume")),
    }), { flush: true }));
    return;
  }

  const filters = h("form", { class: "row", role: "search" },
    h("div", { class: "grow", style: { minWidth: "200px" } }, input("q", { type: "search", placeholder: "Title, skill or keyword", maxlength: 200, "aria-label": "Search jobs", value: ctx.query.get("q") || "" })),
    h("div", { style: { width: "180px" } }, input("location", { placeholder: "City", maxlength: 100, "aria-label": "Location", value: ctx.query.get("location") || "" })),
    h("div", { style: { width: "170px" } }, select("employment_type", [["", "Any type"], "FULL_TIME", "PART_TIME", "CONTRACT", "INTERNSHIP"], ctx.query.get("employment_type"), { "aria-label": "Employment type" })),
    h("button", { class: "btn", type: "submit" }, icon("search"), "Search"));
  const results = h("div");
  body.append(h("div", { class: "stack" }, filters, results));

  const run = async (offset = 0) => {
    clear(results, loading());
    const q = readForm(filters);
    const data = await get("/api/jobs", { ...q, limit: 20, offset });
    if (!ctx.alive()) return;
    clear(results, panel(null, h("div", {}, table([
      { label: "Role", render: (j) => h("div", {}, h("div", { class: "title-cell" }, j.title), h("div", { class: "sub" }, j.company_name)) },
      { label: "Location", render: (j) => [j.location, j.is_remote ? "Remote" : null].filter(Boolean).join(", ") || "-" },
      { label: "Type", render: (j) => humanize(j.employment_type) },
      { label: "Skills", render: (j) => chips(j.skills.slice(0, 4)) },
      { label: "Posted", render: (j) => fmtAgo(j.created_at) },
    ], data.items, { onRowClick: (j) => { window.location.hash = `#/jobs/${j.job_id}`; }, emptyState: empty("No jobs match", "Try fewer words or clear the filters.") }),
    pager(data, run)), { flush: true }));
  };
  filters.addEventListener("submit", (e) => { e.preventDefault(); run(0); });
  await run(0);
}

async function jobDetail(ctx) {
  const [job, resumes] = await Promise.all([get(`/api/jobs/${ctx.params.id}`), get("/api/resumes")]);
  if (!ctx.alive()) return;
  ctx.crumbs([["Find jobs", "#/jobs"], [job.title]]);
  const ready = resumes.items.some((r) => r.is_active && r.processing_status === "COMPLETED");
  const applyBtn = job.my_application
    ? h("span", { class: "row" }, badge(job.my_application.status), h("span", { class: "muted small" }, `Applied ${fmtDate(job.my_application.applied_at)}`))
    : job.status !== "OPEN" ? h("span", { class: "muted" }, "This job is no longer accepting applications")
    : h("button", { class: "btn btn-primary", disabled: !ready, onclick: async (e) => {
        await busy(e.currentTarget, async () => {
          try { await post(`/api/jobs/${job.job_id}/apply`); toast("Application sent"); ctx.rerender(); } catch (err) { toast(errorText(err), "error"); }
        });
      } }, "Apply with current resume");

  const required = job.skills.filter((s) => s.is_required), preferred = job.skills.filter((s) => !s.is_required);
  const skillList = (list) => h("div", { class: "chips" }, list.map((s) => h("span", { class: `chip ${s.i_have ? "have" : "missing"}` }, s.i_have ? icon("check") : null, s.skill_name)));
  clear(ctx.el,
    pageHead(job.title, [job.company_name, job.location, job.is_remote ? "Remote" : null, humanize(job.employment_type)].filter(Boolean).join(", "), applyBtn),
    !ready && !job.my_application ? h("div", { class: "callout info", style: { marginBottom: "16px" } }, icon("info"),
      h("span", {}, "Upload a resume and wait for it to finish processing before applying. ", h("a", { href: "#/resume" }, "Go to resume"))) : null,
    h("div", { class: "split" },
      panel("About the role", h("div", { class: "small", style: { whiteSpace: "pre-wrap", maxWidth: "75ch" } }, job.description)),
      h("div", { class: "stack" },
        panel("Details", h("dl", { class: "dl" },
          h("dt", {}, "Experience"), h("dd", {}, `${Number(job.min_experience)}${job.max_experience ? `-${Number(job.max_experience)}` : "+"} years`),
          h("dt", {}, "Salary"), h("dd", {}, fmtSalaryRange(job.min_salary, job.max_salary) || "Not listed"),
          h("dt", {}, "Closes"), h("dd", {}, job.closing_date ? fmtDate(job.closing_date) : "Open until filled"),
          h("dt", {}, "Company"), h("dd", {}, job.company_name, job.website ? h("div", {}, extLink(job.website)) : null))),
        panel("Skills", h("div", { class: "stack" },
          h("div", { class: "stack-sm" }, h("span", { class: "label" }, "Required"), required.length ? skillList(required) : h("span", { class: "faint small" }, "None listed")),
          preferred.length ? h("div", { class: "stack-sm" }, h("span", { class: "label" }, "Nice to have"), skillList(preferred)) : null,
          h("p", { class: "faint xs" }, "Green means it's on your profile."))))));
}

// =============================================================================================
// Applications
// =============================================================================================
async function applicationsPage(ctx) {
  const data = await get("/api/applications", { limit: 50 });
  if (!ctx.alive()) return;
  const open = data.items.filter((a) => OPEN_STAGES.includes(a.status) || a.status === "HIRED");
  const closed = data.items.filter((a) => ["REJECTED", "WITHDRAWN"].includes(a.status));

  const lanes = [...OPEN_STAGES, "HIRED"].map((stage) => {
    const items = open.filter((a) => a.status === stage);
    return h("div", { class: "lane" },
      h("div", { class: "lane-head" }, h("span", {}, humanize(stage)), h("span", { class: "faint num" }, items.length)),
      h("div", { class: "lane-body" }, items.map((a) => ticket(a, ctx))));
  });

  clear(ctx.el,
    pageHead("Applications", "Where each application stands. Withdraw any time before you're hired."),
    data.items.length ? h("div", { class: "stack" },
      h("div", { class: "board" }, lanes),
      closed.length ? panel("Closed", table([
        { label: "Role", render: (a) => h("div", {}, h("div", { class: "title-cell" }, a.job_title), h("div", { class: "sub" }, a.company_name)) },
        { label: "Status", render: (a) => badge(a.status) },
        { label: "Updated", render: (a) => fmtAgo(a.status_changed_at) },
        { label: "Review", render: (a) => (a.review_status ? reviewBadge(a.review_status) : h("span", { class: "faint small" }, "-")) },
        { label: "", class: "right", render: (a) => h("div", { class: "row", style: { justifyContent: "flex-end", flexWrap: "nowrap" } },
          a.has_feedback ? h("a", { class: "btn btn-sm", href: `#/applications/${a.application_id}/feedback` }, "See why") : null,
          a.status === "REJECTED" && !["OPEN", "IN_REVIEW"].includes(a.review_status)
            ? h("button", { class: "btn btn-sm", onclick: () => reviewForm(a, "HUMAN_REVIEW", ctx) }, "Request review") : null) },
      ], closed), { flush: true }) : null)
    : panel(null, empty("No applications yet", "Find a role that fits and apply with your resume.", h("a", { class: "btn btn-primary", href: "#/jobs" }, "Find jobs"))));
}

function ticket(a, ctx) {
  const pendingOffer = a.status === "OFFER" && a.offer_status === "PENDING";
  return h("div", { class: "ticket" },
    h("a", { class: "t", href: `#/jobs/${a.job_id}`, style: { textDecoration: "none" } }, a.job_title),
    h("span", { class: "faint xs" }, `${a.company_name}, applied ${fmtDate(a.applied_at)}`),
    pendingOffer ? h("div", { class: "stack-sm", style: { marginTop: "6px" } },
      h("span", { class: "small" }, `${fmtMoney(a.salary, a.currency)} a year, joining ${fmtDate(a.joining_date)}`),
      h("span", { class: "faint xs" }, `Answer by ${fmtDate(a.expires_at)}`),
      h("div", { class: "row" },
        h("button", { class: "btn btn-primary btn-sm", onclick: () => respond(a, "ACCEPTED", ctx) }, "Accept"),
        h("button", { class: "btn btn-sm", onclick: () => respond(a, "REJECTED", ctx) }, "Decline"))) : null,
    a.review_status ? h("div", { class: "row", style: { gap: "6px" } }, h("span", { class: "faint xs" }, "Review"), reviewBadge(a.review_status)) : null,
    h("button", { class: "btn btn-ghost btn-sm", style: { alignSelf: "flex-start" }, onclick: () => reviewForm(a, "EXTRACTION_ERROR", ctx) }, "Report wrong info"),
    a.status !== "HIRED" ? h("button", { class: "btn btn-ghost btn-sm", style: { alignSelf: "flex-start", marginTop: "-4px" }, onclick: async () => {
      if (!(await confirmDialog("Withdraw this application?", `${a.company_name} will be told you've withdrawn from ${a.job_title}. You can't reapply to the same job.`, "Withdraw", true))) return;
      try { await post(`/api/applications/${a.application_id}/withdraw`); toast("Application withdrawn"); ctx.rerender(); } catch (err) { toast(errorText(err), "error"); }
    } }, "Withdraw") : null);
}

function reviewForm(a, type, ctx) {
  const extraction = type === "EXTRACTION_ERROR";
  const claimed = h("div", { hidden: !extraction }, field("What it should say", input("claimed_value", { maxlength: 1000, placeholder: "e.g. 4 years, or Python, AWS, Docker" })));
  const typeSel = select("request_type", [["EXTRACTION_ERROR", "Information from my resume is wrong"], ["HUMAN_REVIEW", "I'd like a person to review the decision"]], type);
  const fieldSel = h("div", { hidden: !extraction }, field("What's wrong", select("field", Object.entries(REVIEW_FIELD), "SKILLS")));
  typeSel.addEventListener("change", () => { const ex = typeSel.value === "EXTRACTION_ERROR"; claimed.hidden = !ex; fieldSel.hidden = !ex; });
  const form = h("form", { class: "form", novalidate: true },
    field("Request", typeSel), fieldSel, claimed,
    field("Explain", textarea("reason", { maxlength: 2000, required: true, placeholder: "What's missing or wrong, and where it appears in your resume or profile." }),
      { hint: "At least 10 characters. The recruiter sees exactly what you write." }),
    h("div", { class: "callout" }, icon("info"), h("span", {}, "If your profile is missing something, ",
      h("a", { href: "#/profile" }, "update it first"), ". The reviewer can then re-screen your application with the corrected details.")));
  const submit = h("button", { class: "btn btn-primary", type: "button" }, "Send request");
  const { close } = modal({ title: `Review: ${a.job_title}`, body: form, footer: [h("button", { class: "btn", type: "button", onclick: () => close() }, "Cancel"), submit] });
  submit.addEventListener("click", () => busy(submit, async () => {
    showFormError(form, null);
    const v = readForm(form);
    if (v.request_type === "HUMAN_REVIEW") { delete v.field; delete v.claimed_value; }
    try { await post(`/api/applications/${a.application_id}/reviews`, v); close(); toast("Request sent to the hiring team"); ctx.rerender(); }
    catch (err) { showFormError(form, err); }
  }));
}

async function reviewsPage(ctx) {
  const data = await get("/api/reviews");
  if (!ctx.alive()) return;
  clear(ctx.el,
    pageHead("Review requests", "Reports of wrong information and requests for a human review. Every outcome is recorded."),
    data.items.length ? h("div", { class: "stack" }, data.items.map((r) => panel(null, h("div", { class: "stack-sm" },
      h("div", { class: "row-between" },
        h("div", {}, h("div", { class: "title-cell" }, r.job_title), h("div", { class: "sub" }, `${r.company_name}, sent ${fmtAgo(r.created_at)}`)),
        h("div", { class: "row" }, r.outcome ? badge("COMPLETED", REVIEW_OUTCOME[r.outcome]) : null, reviewBadge(r.status))),
      h("dl", { class: "dl" },
        h("dt", {}, "Request"), h("dd", {}, r.request_type === "HUMAN_REVIEW" ? "Human review of the decision" : `Wrong ${(REVIEW_FIELD[r.field] || "information").toLowerCase()}`),
        r.claimed_value ? [h("dt", {}, "Should say"), h("dd", {}, r.claimed_value)] : null,
        h("dt", {}, "Your reason"), h("dd", {}, r.reason),
        r.resolution_note ? [h("dt", {}, "Reviewer's note"), h("dd", {}, h("span", { class: "quote" }, r.resolution_note))] : null,
        r.score_after !== null && r.score_before !== null && r.status === "RESOLVED"
          ? [h("dt", {}, "Match score"), h("dd", { class: "num" }, `${Math.round(r.score_before)} before, ${Math.round(r.score_after)} after`)] : null),
      ["OPEN", "IN_REVIEW"].includes(r.status) ? h("button", { class: "btn btn-sm", style: { alignSelf: "flex-start" }, onclick: async () => {
        if (!(await confirmDialog("Withdraw this request?", "The hiring team won't review it. You can send a new one later.", "Withdraw request", true))) return;
        try { await post(`/api/reviews/${r.request_id}/withdraw`); toast("Request withdrawn"); ctx.rerender(); } catch (err) { toast(errorText(err), "error"); }
      } }, "Withdraw request") : null))))
    : panel(null, empty("No review requests", "If something about a screening looks wrong, report it from your Applications page.",
        h("a", { class: "btn", href: "#/applications" }, "Go to applications"))));
}

async function respond(a, decision, ctx) {
  const accept = decision === "ACCEPTED";
  if (!(await confirmDialog(accept ? "Accept this offer?" : "Decline this offer?",
    accept ? `You're accepting ${a.job_title} at ${a.company_name}.` : `${a.company_name} will be told you've declined. This closes the application.`,
    accept ? "Accept offer" : "Decline offer", !accept))) return;
  try { await post(`/api/offers/${a.offer_id}/respond`, { decision }); toast(accept ? "Offer accepted" : "Offer declined"); ctx.rerender(); }
  catch (err) { toast(errorText(err), "error"); }
}

async function feedbackPage(ctx) {
  const fb = await get(`/api/applications/${ctx.params.id}/feedback`);
  if (!ctx.alive()) return;
  ctx.crumbs([["Applications", "#/applications"], [`Feedback: ${fb.job_title}`]]);
  const suggestions = fb.suggestions_status === "READY"
    ? h("div", {}, fb.suggested_resources.map((s) => h("div", { class: "entry" },
        h("div", { class: "entry-title" }, s.skill), h("p", { class: "small muted" }, s.suggestion),
        s.search_url ? h("div", { class: "small" }, extLink(s.search_url, "Find courses")) : null)))
    : h("p", { class: "muted small" }, "Suggestions are being prepared. Check back in a minute.");

  clear(ctx.el,
    pageHead(`Why ${fb.company_name} didn't move forward`, `Your application for ${fb.job_title}. This stays here so you can come back to it.`,
      h("button", { class: "btn", onclick: () => reviewForm({ application_id: Number(ctx.params.id), job_title: fb.job_title }, "HUMAN_REVIEW", ctx) }, "Ask for a human review")),
    h("div", { class: "split" },
      h("div", { class: "stack" },
        panel("What the role needed that your profile didn't show", fb.missing_skills.length ? chips(fb.missing_skills, "missing")
          : h("p", { class: "muted small" }, "Your profile covered the listed skills. The decision came down to other factors.")),
        fb.match_summary ? panel("Assessment", h("p", { class: "quote" }, fb.match_summary)) : null,
        panel("How to close the gap", suggestions)),
      panel("Match", h("div", { class: "stack-sm" }, score(fb.match_score, { large: true }),
        h("p", { class: "muted small" }, "How closely your profile matched this role's requirements, out of 100.")))));
}

// =============================================================================================
// Interviews & notifications
// =============================================================================================
async function interviewsPage(ctx) {
  const data = await get("/api/applicant/interviews");
  if (!ctx.alive()) return;
  clear(ctx.el, pageHead("Interviews", "Calendar invites are emailed to you; this is the same schedule."),
    panel(null, table([
      { label: "When", render: (i) => h("div", {}, h("div", { class: "title-cell" }, fmtDateTime(i.scheduled_at)), h("div", { class: "sub" }, `${i.duration_minutes} minutes`)) },
      { label: "Role", render: (i) => h("div", {}, h("div", {}, i.job_title), h("div", { class: "sub" }, i.company_name)) },
      { label: "Type", render: (i) => humanize(i.interview_type) },
      { label: "With", render: (i) => i.interviewer_name || "-" },
      { label: "Status", render: (i) => badge(i.status) },
      { label: "", class: "right", render: (i) => i.status === "SCHEDULED" && i.meeting_link ? extLink(i.meeting_link, "Join link") : null },
    ], data.items, { emptyState: empty("No interviews yet", "When a company schedules one, it shows up here and in your inbox.") }), { flush: true }));
}

startApp({
  portal: "applicant",
  nav: NAV,
  onboarding,
  quick: [
    { label: "Upload a new resume", hash: "#/resume", icon: "upload" },
    { label: "Recommended jobs", hash: "#/jobs?tab=recommended", icon: "sparkle" },
  ],
  routes: [
    { path: "", nav: "dashboard", title: "Dashboard", render: dashboard },
    { path: "profile", nav: "profile", title: "Profile", render: profilePage },
    { path: "resume", nav: "resume", title: "Resume", render: resumePage },
    { path: "jobs", nav: "jobs", title: "Find jobs", render: jobsPage },
    { path: "jobs/:id", nav: "jobs", title: "Job", render: jobDetail },
    { path: "applications", nav: "applications", title: "Applications", render: applicationsPage },
    { path: "applications/:id/feedback", nav: "applications", title: "Feedback", render: feedbackPage },
    { path: "interviews", nav: "interviews", title: "Interviews", render: interviewsPage },
    { path: "reviews", nav: "reviews", title: "Review requests", render: reviewsPage },
    { path: "notifications", nav: "notifications", title: "Notifications", render: notificationsPage },
  ],
});

