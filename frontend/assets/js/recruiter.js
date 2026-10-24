// Recruiter portal views.
import { del, get, patch, post } from "./api.js";
import { startApp } from "./shell.js";
import {
  badge, breakdown, busy, chips, clear, confirmDialog, empty, errorText, extLink, field, fmtAgo, fmtDate, fmtDateTime,
  fmtMoney, fmtYears, h, humanize, icon, input, loading, modal, pageHead, pager, panel, readForm, score, select,
  showFormError, table, tabs, textarea, toast,
} from "./ui.js";
import { notificationsPage } from "./views-common.js";

const NAV = [
  { key: "dashboard", path: "", label: "Dashboard", icon: "chart" },
  { key: "jobs", path: "jobs", label: "Jobs", icon: "briefcase" },
  { key: "search", path: "search", label: "Talent search", icon: "search" },
  { key: "interviews", path: "interviews", label: "Interviews", icon: "calendar" },
  { key: "offers", path: "offers", label: "Offers", icon: "file" },
  { group: "Workspace" },
  { key: "history", path: "history", label: "Search history", icon: "history" },
  { key: "notifications", path: "notifications", label: "Notifications", icon: "bell" },
  { key: "team", path: "team", label: "Company and team", icon: "building" },
];

let skillCache = null;
const skillDictionary = async () => (skillCache ??= (await get("/api/skills")).items);
let teamCache = null;
const team = async () => (teamCache ??= await get("/api/company"));

// =============================================================================================
// Onboarding: create a company or join one with an invite code
// =============================================================================================
function onboarding(el, me) {
  const names = () => h("div", { class: "form-row" },
    field("First name", input("first_name", { required: true, maxlength: 100, autocomplete: "given-name" })),
    field("Last name", input("last_name", { required: true, maxlength: 100, autocomplete: "family-name" })),
    field("Your role", input("designation", { maxlength: 100, placeholder: "Talent partner" })));

  const create = h("form", { class: "form", novalidate: true },
    names(),
    field("Company name", input("company_name", { required: true, maxlength: 200 })),
    h("div", { class: "form-row" }, field("Industry", input("industry", { maxlength: 100 })), field("City", input("location", { maxlength: 150 }))),
    field("Website", input("website", { type: "url", maxlength: 500, placeholder: "https://" })),
    h("div", { class: "form-actions" }, h("button", { class: "btn btn-primary", type: "submit" }, "Create company")));
  const join = h("form", { class: "form", novalidate: true },
    names(),
    field("Invite code", input("invite_code", { required: true, maxlength: 32, class: "mono", autocomplete: "off", spellcheck: "false" }),
      { hint: "Ask an admin at your company. They'll find it under Company and team." }),
    h("div", { class: "form-actions" }, h("button", { class: "btn btn-primary", type: "submit" }, "Join company")));

  for (const [form, url] of [[create, "/api/companies"], [join, "/api/companies/join"]]) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      showFormError(form, null);
      await busy(form.querySelector("[type=submit]"), async () => {
        try { await post(url, readForm(form)); window.location.hash = "#/"; window.location.reload(); } catch (err) { showFormError(form, err); }
      });
    });
  }
  clear(el, pageHead("Set up your workspace", `Signed in as ${me.email}. Start a new company, or join your team's.`),
    h("div", { class: "grid-2" }, panel("Start a new company", create), panel("Join with an invite code", join)));
}

// =============================================================================================
// Dashboard
// =============================================================================================
async function dashboard(ctx) {
  const a = await get("/api/recruiter/analytics", { days: 30 });
  if (!ctx.alive()) return;
  const k = a.kpis;
  const kpi = (label, value) => h("div", { class: "kpi" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value));

  const f = a.funnel;
  const stages = [["Applied", f.applied], ["Screened", f.screened], ["Shortlisted", f.shortlisted], ["Interviewed", f.interviewed], ["Offered", f.offered], ["Hired", f.hired]];
  const funnel = h("div", { class: "bars" }, stages.map(([label, n]) => h("div", { class: "bar-row" },
    h("span", { class: "muted" }, label),
    h("div", { class: "bar-track" }, h("div", { class: "bar-fill", style: { width: `${f.applied ? (100 * n) / f.applied : 0}%` } })),
    h("span", { class: "num right" }, n))));

  const max = Math.max(1, ...a.timeline.map((d) => d.applications));
  const timeline = h("div", {},
    h("div", { class: "columns", role: "img", "aria-label": "Applications per day over the last 30 days" },
      a.timeline.map((d) => h("div", { class: "col", title: `${fmtDate(d.day)}: ${d.applications}`, style: { height: `${(100 * d.applications) / max}%` } }))),
    h("div", { class: "columns-axis" }, h("span", {}, fmtDate(a.timeline[0]?.day)), h("span", {}, "Today")));

  const skillMax = Math.max(1, ...a.top_skills.map((s) => s.candidates));
  const topSkills = a.top_skills.length ? h("div", { class: "bars" }, a.top_skills.map((s) => h("div", { class: "bar-row" },
    h("span", { class: "muted" }, s.skill_name),
    h("div", { class: "bar-track" }, h("div", { class: "bar-fill", style: { width: `${(100 * s.candidates) / skillMax}%` } })),
    h("span", { class: "num right" }, s.candidates)))) : h("p", { class: "muted small" }, "Appears once people apply.");

  clear(ctx.el,
    pageHead("Dashboard", "Hiring activity across your company.", h("a", { class: "btn btn-primary", href: "#/jobs/new" }, icon("plus"), "New job")),
    h("div", { class: "stack" },
      h("div", { class: "kpis" }, kpi("Open jobs", k.active_jobs), kpi("Candidates", k.candidates), kpi("Applications", k.applications),
        kpi("Shortlisted", k.shortlisted), kpi("Interviews ahead", k.upcoming_interviews), kpi("Offers out", k.pending_offers), kpi("Hires", k.hires)),
      h("div", { class: "grid-2" }, panel("Hiring funnel", funnel), panel("Applications, last 30 days", timeline)),
      h("div", { class: "grid-2" },
        panel("Average match score by job", table([
          { label: "Job", render: (j) => h("a", { href: `#/jobs/${j.job_id}` }, j.title) },
          { label: "Screened", class: "right", render: (j) => h("span", { class: "num" }, j.screened) },
          { label: "Average", render: (j) => score(j.avg_score) },
        ], a.by_job, { emptyState: empty("No screenings yet", "Open a job and run AI screening to rank its applicants.") }), { flush: true }),
        panel("Most common skills among applicants", topSkills))));
}

// =============================================================================================
// Jobs
// =============================================================================================
async function jobsPage(ctx) {
  const status = ctx.query.get("status") || "";
  const body = h("div");
  clear(ctx.el,
    pageHead("Jobs", "Every role your company is hiring for.", h("a", { class: "btn btn-primary", href: "#/jobs/new" }, icon("plus"), "New job")),
    tabs([["", "All"], ["OPEN", "Open"], ["DRAFT", "Drafts"], ["PAUSED", "Paused"], ["CLOSED", "Closed"]], status,
      (s) => { window.location.hash = s ? `#/jobs?status=${s}` : "#/jobs"; }), body);
  const run = async (offset = 0) => {
    clear(body, loading());
    const data = await get("/api/recruiter/jobs", { status, limit: 20, offset });
    if (!ctx.alive()) return;
    clear(body, panel(null, h("div", {}, table([
      { label: "Job", render: (j) => h("div", {}, h("div", { class: "title-cell" }, j.title), h("div", { class: "sub" }, [j.department, j.location].filter(Boolean).join(", ") || humanize(j.employment_type))) },
      { label: "Status", render: (j) => badge(j.status) },
      { label: "Applicants", class: "right", render: (j) => h("span", { class: "num" }, j.total) },
      { label: "Shortlisted", class: "right", render: (j) => h("span", { class: "num" }, j.shortlisted) },
      { label: "Interviewing", class: "right", render: (j) => h("span", { class: "num" }, j.interview) },
      { label: "Hired", class: "right", render: (j) => h("span", { class: "num" }, j.hired) },
      { label: "Owner", render: (j) => j.owner_name },
      { label: "Created", render: (j) => fmtAgo(j.created_at) },
    ], data.items, {
      onRowClick: (j) => { window.location.hash = `#/jobs/${j.job_id}`; },
      emptyState: empty("No jobs here", "Post a role to start receiving applications.", h("a", { class: "btn btn-primary", href: "#/jobs/new" }, "New job")),
    }), pager(data, run)), { flush: true }));
  };
  await run(0);
}

async function jobForm(ctx) {
  const editing = Boolean(ctx.params.id);
  const [dict, job] = await Promise.all([skillDictionary(), editing ? get(`/api/recruiter/jobs/${ctx.params.id}`) : null]);
  if (!ctx.alive()) return;
  if (editing) ctx.crumbs([["Jobs", "#/jobs"], [job.title, `#/jobs/${job.job_id}`], ["Edit"]]);
  else ctx.crumbs([["Jobs", "#/jobs"], ["New job"]]);
  const j = job || {};
  const picked = new Map((j.skills || []).map((s) => [s.skill_id, { name: s.skill_name, is_required: s.is_required }]));

  const skillBox = h("div", { class: "chips" });
  const renderSkills = () => clear(skillBox, picked.size ? [...picked].map(([id, s]) => h("span", { class: `chip ${s.is_required ? "have" : ""}` },
    s.name, h("span", { class: "faint" }, s.is_required ? "required" : "nice to have"),
    h("button", { type: "button", "aria-label": `Toggle required for ${s.name}`, title: "Toggle required", onclick: () => { s.is_required = !s.is_required; renderSkills(); } }, "⇄"),
    h("button", { type: "button", "aria-label": `Remove ${s.name}`, onclick: () => { picked.delete(id); renderSkills(); } }, "×")))
    : h("span", { class: "faint small" }, "No skills yet. These drive the match score, so add the ones that matter."));
  renderSkills();
  const skillSelect = h("select", { "aria-label": "Skill" }, h("option", { value: "" }, "Add a skill"), dict.map((s) => h("option", { value: s.skill_id }, s.skill_name)));
  const reqBox = h("input", { type: "checkbox", checked: true });
  skillSelect.addEventListener("change", () => {
    const id = Number(skillSelect.value);
    if (id && picked.size < 30) { picked.set(id, { name: skillSelect.selectedOptions[0].textContent, is_required: reqBox.checked }); renderSkills(); }
    skillSelect.value = "";
  });

  const form = h("form", { class: "form", novalidate: true },
    h("div", { class: "form-row" }, field("Job title", input("title", { value: j.title || "", required: true, maxlength: 200 })),
      field("Department", input("department", { value: j.department || "", maxlength: 100 }))),
    h("div", { class: "form-row" }, field("Location", input("location", { value: j.location || "", maxlength: 150 })),
      field("Employment type", select("employment_type", ["FULL_TIME", "PART_TIME", "CONTRACT", "INTERNSHIP"], j.employment_type || "FULL_TIME")),
      field("Closing date", input("closing_date", { type: "date", value: j.closing_date || "" }))),
    h("label", { class: "check" }, h("input", { type: "checkbox", name: "is_remote", checked: Boolean(j.is_remote) }), "Remote friendly"),
    h("div", { class: "form-row" },
      field("Min experience (years)", input("min_experience", { type: "number", min: 0, max: 50, step: "0.5", value: j.min_experience ?? 0 })),
      field("Max experience (years)", input("max_experience", { type: "number", min: 0, max: 50, step: "0.5", value: j.max_experience ?? "" })),
      field("Min salary (yearly)", input("min_salary", { type: "number", min: 0, value: j.min_salary ?? "" })),
      field("Max salary (yearly)", input("max_salary", { type: "number", min: 0, value: j.max_salary ?? "" }))),
    field("Description", textarea("description", { value: j.description || "", class: "tall", required: true, minlength: 30, maxlength: 20000 }),
      { hint: "Responsibilities, what success looks like, the team. This text is what candidates are matched against." }),
    h("div", { class: "field" }, h("span", { class: "label" }, "Skills"),
      h("div", { class: "row" }, h("div", { class: "grow" }, skillSelect), h("label", { class: "check" }, reqBox, "Required")), skillBox),
    h("div", { class: "form-actions" },
      h("a", { class: "btn", href: editing ? `#/jobs/${j.job_id}` : "#/jobs" }, "Cancel"),
      editing ? null : h("button", { class: "btn", type: "submit", value: "DRAFT" }, "Save as draft"),
      h("button", { class: "btn btn-primary", type: "submit", value: editing ? "" : "OPEN" }, editing ? "Save changes" : "Publish job")));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showFormError(form, null);
    const data = readForm(form);
    data.skills = [...picked].map(([skill_id, s]) => ({ skill_id, is_required: s.is_required }));
    if (!editing) data.status = e.submitter?.value || "DRAFT";
    await busy(e.submitter, async () => {
      try {
        if (editing) { await patch(`/api/recruiter/jobs/${j.job_id}`, data); toast("Job updated"); window.location.hash = `#/jobs/${j.job_id}`; }
        else { const r = await post("/api/recruiter/jobs", data); toast(data.status === "OPEN" ? "Job published" : "Draft saved"); window.location.hash = `#/jobs/${r.job_id}`; }
      } catch (err) { showFormError(form, err); }
    });
  });
  clear(ctx.el, pageHead(editing ? "Edit job" : "New job", editing ? null : "Publish now, or save a draft and open it later."),
    h("div", { style: { maxWidth: "860px" } }, panel(null, form)));
}

// ---------------------------------------------------------------------------------- job pipeline
async function jobDetail(ctx) {
  const id = ctx.params.id;
  const status = ctx.query.get("status") || "";
  const job = await get(`/api/recruiter/jobs/${id}`);
  if (!ctx.alive()) return;
  ctx.crumbs([["Jobs", "#/jobs"], [job.title]]);
  const p = job.pipeline;
  const total = Object.values(p).reduce((a, b) => a + b, 0);
  const selected = new Map();

  const statusMenu = select("status", [["", "Change status"], ["OPEN", "Open"], ["PAUSED", "Paused"], ["CLOSED", "Closed"], ["DRAFT", "Draft"]], "", { "aria-label": "Job status", style: { width: "150px" } });
  statusMenu.addEventListener("change", async () => {
    if (!statusMenu.value) return;
    try { await patch(`/api/recruiter/jobs/${id}`, { status: statusMenu.value }); toast(`Job is now ${humanize(statusMenu.value).toLowerCase()}`); ctx.rerender(); }
    catch (err) { toast(errorText(err), "error"); }
  });

  const screenBtn = h("button", { class: "btn btn-primary", disabled: !total }, icon("sparkle"), "Run AI screening");
  screenBtn.addEventListener("click", () => busy(screenBtn, async () => {
    try {
      const r = await post(`/api/recruiter/jobs/${id}/screen`, { use_ai: true });
      toast(`Ranked ${r.screened} applicant${r.screened === 1 ? "" : "s"}${r.llm_used ? " with AI review" : " (AI review unavailable, database scoring only)"}`);
      ctx.rerender();
    } catch (err) { toast(errorText(err), "error"); }
  }));
  const compareBtn = h("button", { class: "btn", disabled: true, onclick: () => compare([...selected.values()]) }, icon("compare"), "Compare");

  const body = h("div");
  clear(ctx.el,
    pageHead(job.title, [badgeText(job.status), job.location, `${total} applicant${total === 1 ? "" : "s"}`].filter(Boolean).join(", "),
      [statusMenu, h("a", { class: "btn", href: `#/jobs/${id}/edit` }, "Edit"),
        h("a", { class: "btn", href: `#/search?job=${id}` }, icon("search"), "Find more"),
        h("button", { class: "btn btn-ghost", "aria-label": "Archive job", title: "Archive", onclick: async () => {
          if (!(await confirmDialog("Archive this job?", "It disappears from listings. Applications, interviews and offers are kept.", "Archive", true))) return;
          try { await del(`/api/recruiter/jobs/${id}`); toast("Job archived"); window.location.hash = "#/jobs"; } catch (err) { toast(errorText(err), "error"); }
        } }, icon("trash"))]),
    tabs([["", "All", total], ...["APPLIED", "SCREENING", "SHORTLISTED", "INTERVIEW", "OFFER", "HIRED", "REJECTED"].map((s) => [s, humanize(s), p[s] || 0])],
      status, (s) => { window.location.hash = s ? `#/jobs/${id}?status=${s}` : `#/jobs/${id}`; }),
    h("div", { class: "row-between", style: { marginBottom: "12px" } },
      h("p", { class: "muted small" }, "Ranked by match score. Everyone gets a database score; the top 20 by relevance also get an AI review."),
      h("div", { class: "row" }, compareBtn, screenBtn)),
    body);

  const run = async (offset = 0) => {
    clear(body, loading());
    const data = await get(`/api/recruiter/jobs/${id}/applications`, { status, limit: 25, offset });
    if (!ctx.alive()) return;
    clear(body, panel(null, h("div", {}, table([
      { label: "", render: (r) => h("input", { type: "checkbox", "aria-label": `Select ${r.candidate_name}`, checked: selected.has(r.application_id), disabled: r.overall_score === null, onchange: (e) => {
          if (e.target.checked) { if (selected.size >= 3) { e.target.checked = false; toast("Compare up to 3 candidates", "error"); return; } selected.set(r.application_id, r); }
          else selected.delete(r.application_id);
          compareBtn.disabled = selected.size < 2;
        } }) },
      { label: "#", class: "rank", render: (r) => h("span", { class: `rank ${r.rank_in_job === 1 ? "top" : ""}` }, r.rank_in_job ?? "-") },
      { label: "Candidate", render: (r) => h("div", {}, h("div", { class: "title-cell" }, r.candidate_name), h("div", { class: "sub" }, r.headline || r.location || "")) },
      { label: "Match", render: (r) => score(r.overall_score) },
      { label: "Verdict", render: (r) => (r.recommendation ? badge(r.recommendation) : h("span", { class: "faint small" }, "-")) },
      { label: "Experience", render: (r) => fmtYears(r.experience_years) },
      { label: "Missing", render: (r) => (r.missing_skills ? chips(r.missing_skills.slice(0, 3), "missing") : h("span", { class: "faint small" }, "-")) },
      { label: "Stage", render: (r) => badge(r.status) },
      { label: "", class: "right", render: (r) => quickActions(r, ctx) },
    ], data.items, {
      onRowClick: (r) => { window.location.hash = `#/applications/${r.application_id}`; },
      emptyState: empty(status ? `No one in ${humanize(status).toLowerCase()}` : "No applicants yet",
        status ? null : job.status === "OPEN" ? "Applications will appear here. You can also search the talent pool." : "Publish the job to start receiving applications."),
    }), pager(data, run)), { flush: true }));
  };
  await run(0);
}

const badgeText = (s) => humanize(s);

function quickActions(r, ctx) {
  const act = async (status, label) => {
    try { await patch(`/api/recruiter/applications/${r.application_id}/status`, { status }); toast(`${r.candidate_name} ${label}`); ctx.rerender(); }
    catch (err) { toast(errorText(err), "error"); }
  };
  const box = h("div", { class: "row", style: { justifyContent: "flex-end", flexWrap: "nowrap" } });
  if (["APPLIED", "SCREENING"].includes(r.status)) box.append(h("button", { class: "btn btn-sm", onclick: () => act("SHORTLISTED", "shortlisted") }, "Shortlist"));
  if (["APPLIED", "SCREENING", "SHORTLISTED", "INTERVIEW", "OFFER"].includes(r.status)) {
    box.append(h("button", { class: "btn btn-ghost btn-sm btn-danger", onclick: async () => {
      if (await confirmDialog(`Reject ${r.candidate_name}?`, "They'll get an email and a breakdown of the skills this role needed. This can't be undone.", "Reject", true)) act("REJECTED", "rejected");
    } }, "Reject"));
  }
  return box;
}

function compare(rows) {
  const metrics = [["Overall", "overall_score"], ["Skills", "skill_score"], ["Experience fit", "experience_score"], ["Semantic", "semantic_score"], ["Education", "education_score"]];
  const tbl = h("div", { class: "table-wrap" }, h("table", { class: "table" },
    h("thead", {}, h("tr", {}, h("th", {}, "Metric"), rows.map((r) => h("th", {}, r.candidate_name)))),
    h("tbody", {},
      metrics.map(([label, key]) => h("tr", {}, h("td", { class: "muted" }, label), rows.map((r) => h("td", {}, score(r[key]))))),
      h("tr", {}, h("td", { class: "muted" }, "Years"), rows.map((r) => h("td", { class: "num" }, fmtYears(r.experience_years)))),
      h("tr", {}, h("td", { class: "muted" }, "Verdict"), rows.map((r) => h("td", {}, r.recommendation ? badge(r.recommendation) : "-"))),
      h("tr", {}, h("td", { class: "muted" }, "Missing"), rows.map((r) => h("td", {}, chips((r.missing_skills || []).slice(0, 5), "missing")))))));
  modal({ title: "Compare candidates", body: tbl, wide: true });
}

// =============================================================================================
// Candidate (application) detail
// =============================================================================================
async function applicationDetail(ctx) {
  const d = await get(`/api/recruiter/applications/${ctx.params.id}`);
  if (!ctx.alive()) return;
  const a = d.application, p = d.profile, s = d.screening;
  const name = `${p.first_name} ${p.last_name}`;
  ctx.crumbs([["Jobs", "#/jobs"], [a.job_title, `#/jobs/${a.job_id}`], [name]]);

  const setStatus = async (status, label) => {
    try { await patch(`/api/recruiter/applications/${a.application_id}/status`, { status }); toast(`${name} ${label}`); ctx.rerender(); }
    catch (err) { toast(errorText(err), "error"); }
  };
  const actions = [];
  if (d.allowed_transitions.includes("SHORTLISTED")) actions.push(h("button", { class: "btn btn-primary", onclick: () => setStatus("SHORTLISTED", "shortlisted") }, "Shortlist"));
  if (["SHORTLISTED", "INTERVIEW"].includes(a.status)) {
    actions.push(h("button", { class: "btn", onclick: () => scheduleInterview(a, name, ctx) }, icon("calendar"), "Schedule interview"));
    actions.push(h("button", { class: "btn", onclick: () => makeOffer(a, name, ctx) }, "Make offer"));
  }
  actions.push(h("button", { class: "btn", onclick: () => downloadResume(d.resume.resume_id) }, icon("download"), "Resume"));
  if (d.allowed_transitions.includes("REJECTED")) {
    actions.push(h("button", { class: "btn btn-ghost btn-danger", onclick: async () => {
      if (await confirmDialog(`Reject ${name}?`, "They'll get an email and a persisted breakdown of the skills this role needed. This can't be undone.", "Reject", true)) setStatus("REJECTED", "rejected");
    } }, "Reject"));
  }

  const analysis = s ? panel("Match analysis", h("div", { class: "stack" },
    h("div", { class: "row-between" }, score(s.overall_score, { large: true }), badge(s.recommendation)),
    breakdown([["Skills", s.skill_score], ["Experience fit", s.experience_score], ["Semantic", s.semantic_score], ["Education", s.education_score]]),
    s.ai_explanation ? h("p", { class: "quote" }, s.ai_explanation) : null,
    h("div", { class: "grid-2" },
      h("div", { class: "stack-sm" }, h("span", { class: "label" }, "Strengths"), s.strengths.length ? h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px" } }, s.strengths.map((x) => h("li", {}, x))) : h("span", { class: "faint small" }, "-")),
      h("div", { class: "stack-sm" }, h("span", { class: "label" }, "Missing skills"), chips(s.missing_skills, "missing"))),
    h("p", { class: "faint xs" }, s.llm_used ? `AI review by ${s.model_name}, ${fmtAgo(s.screened_at)}. Blended with database scores.` : `Database scoring only, ${fmtAgo(s.screened_at)}.`)))
    : panel("Match analysis", empty("Not screened yet", "Run AI screening from the job page to score this applicant.",
      h("a", { class: "btn", href: `#/jobs/${a.job_id}` }, "Go to job")));

  const interviews = d.interviews.length ? h("div", {}, d.interviews.map((i) => h("div", { class: "entry" },
    h("div", { class: "row-between" }, h("span", { class: "entry-title" }, `${humanize(i.interview_type)} with ${i.interviewer_name}`), badge(i.status)),
    h("div", { class: "sub" }, `${fmtDateTime(i.scheduled_at)}, ${i.duration_minutes} min`),
    (i.feedback || []).map((f) => h("div", { class: "small", style: { marginTop: "6px" } },
      h("span", {}, `${f.reviewer}: `), badge(f.recommendation), h("span", { class: "muted" }, ` overall ${f.overall_score}/10`),
      f.comments ? h("div", { class: "quote", style: { marginTop: "4px" } }, f.comments) : null)))))
    : h("p", { class: "muted small" }, "No interviews yet.");

  const offers = d.offers.length ? h("div", {}, d.offers.map((o) => h("div", { class: "entry row-between" },
    h("div", {}, h("div", { class: "entry-title" }, fmtMoney(o.salary, o.currency)), h("div", { class: "sub" }, `Joining ${fmtDate(o.joining_date)}, expires ${fmtDate(o.expires_at)}`)),
    h("div", { class: "row" }, badge(o.offer_status), o.offer_status === "PENDING" ? h("button", { class: "btn btn-sm", onclick: async () => {
      if (!(await confirmDialog("Withdraw this offer?", "The candidate will no longer be able to accept it.", "Withdraw offer", true))) return;
      try { await post(`/api/recruiter/offers/${o.offer_id}/withdraw`); toast("Offer withdrawn"); ctx.rerender(); } catch (err) { toast(errorText(err), "error"); }
    } }, "Withdraw") : null))))
    : h("p", { class: "muted small" }, "No offers.");

  clear(ctx.el,
    pageHead(name, [p.headline, p.location].filter(Boolean).join(", ") || null, h("div", { class: "row" }, badge(a.status), ...actions)),
    h("div", { class: "split" },
      h("div", { class: "stack" }, analysis, profileSections(d), panel("Interviews", interviews), panel("Offers", offers)),
      h("div", { class: "stack" }, contactPanel(p), panel("Skills", chips(d.skills.map((x) => x.skill_name))),
        panel("Application", h("dl", { class: "dl" }, h("dt", {}, "Applied"), h("dd", {}, fmtDate(a.applied_at)),
          h("dt", {}, "Last change"), h("dd", {}, fmtAgo(a.status_changed_at)))))));
}

function contactPanel(p) {
  return panel("Contact", h("dl", { class: "dl" },
    h("dt", {}, "Email"), h("dd", {}, p.email ? h("a", { href: `mailto:${p.email}` }, p.email) : "-"),
    h("dt", {}, "Phone"), h("dd", {}, p.phone || "-"),
    h("dt", {}, "Experience"), h("dd", {}, fmtYears(p.experience_years)),
    h("dt", {}, "Notice period"), h("dd", {}, p.notice_period_days !== null && p.notice_period_days !== undefined ? `${p.notice_period_days} days` : "-"),
    h("dt", {}, "Links"), h("dd", { class: "stack-sm" }, extLink(p.linkedin_url, "LinkedIn"), extLink(p.github_url, "GitHub"), extLink(p.portfolio_url, "Portfolio"))));
}

function profileSections(d) {
  return panel("Background", h("div", { class: "stack" },
    h("div", {}, h("span", { class: "label" }, "Experience"), d.experience.length ? d.experience.map((x) => h("div", { class: "entry" },
      h("div", { class: "entry-title" }, `${x.designation}, ${x.company_name}`), h("div", { class: "sub" }, `${fmtDate(x.start_date)} - ${x.is_current ? "Present" : fmtDate(x.end_date)}`),
      x.description ? h("p", { class: "small muted", style: { marginTop: "4px" } }, x.description) : null)) : h("p", { class: "faint small" }, "None listed")),
    h("div", {}, h("span", { class: "label" }, "Education"), d.education.length ? d.education.map((x) => h("div", { class: "entry" },
      h("div", { class: "entry-title" }, `${x.degree}${x.field_of_study ? `, ${x.field_of_study}` : ""}`), h("div", { class: "sub" }, [x.institution, [x.start_year, x.end_year].filter(Boolean).join(" - ")].filter(Boolean).join(", ")))) : h("p", { class: "faint small" }, "None listed")),
    d.projects.length ? h("div", {}, h("span", { class: "label" }, "Projects"), d.projects.map((x) => h("div", { class: "entry" },
      h("div", { class: "entry-title" }, x.title), x.technologies?.length ? h("div", { class: "sub" }, x.technologies.join(", ")) : null))) : null));
}

async function candidateDetail(ctx) {
  const d = await get(`/api/recruiter/candidates/${ctx.params.id}`);
  if (!ctx.alive()) return;
  const p = d.profile, name = `${p.first_name} ${p.last_name}`;
  ctx.crumbs([["Talent search", "#/search"], [name]]);
  clear(ctx.el,
    pageHead(name, [p.headline, p.location].filter(Boolean).join(", ") || null,
      d.resume ? h("button", { class: "btn", onclick: () => downloadResume(d.resume.resume_id) }, icon("download"), "Resume") : null),
    h("div", { class: "split" },
      h("div", { class: "stack" }, profileSections(d),
        d.applications.length ? panel("Applications to your company", table([
          { label: "Job", render: (x) => x.title }, { label: "Stage", render: (x) => badge(x.status) },
        ], d.applications, { onRowClick: (x) => { window.location.hash = `#/applications/${x.application_id}`; } }), { flush: true }) : null),
      h("div", { class: "stack" }, contactPanel(p), panel("Skills", chips(d.skills.map((x) => x.skill_name))))));
}

async function downloadResume(resumeId) {
  try {
    const { url } = await get(`/api/resumes/${resumeId}/download`);
    if (/^https:\/\//.test(url) || window.location.hostname === "localhost") window.location.assign(url);
  } catch (err) { toast(errorText(err), "error"); }
}

// ---------------------------------------------------------------------------------- scheduling and offers
function toLocalInput(date) {
  const d = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return d.toISOString().slice(0, 16);
}

async function scheduleInterview(a, name, ctx, existing = null) {
  const t = await team();
  const start = existing ? new Date(existing.scheduled_at) : new Date(Date.now() + 2 * 86400000);
  if (!existing) start.setHours(11, 0, 0, 0);
  const form = h("form", { class: "form", novalidate: true },
    h("div", { class: "form-row" },
      field("Date and time", input("scheduled_at", { type: "datetime-local", required: true, value: toLocalInput(start) }), { hint: `Your time zone (${Intl.DateTimeFormat().resolvedOptions().timeZone})` }),
      field("Length", select("duration_minutes", [[30, "30 minutes"], [45, "45 minutes"], [60, "1 hour"], [90, "1.5 hours"], [120, "2 hours"]], existing?.duration_minutes || 60))),
    existing ? null : h("div", { class: "form-row" },
      field("Interviewer", select("interviewer_id", t.team.map((m) => [m.recruiter_id, `${m.first_name} ${m.last_name}`]), t.me.recruiter_id)),
      field("Type", select("interview_type", ["VIDEO", "PHONE", "TECHNICAL", "HR", "ONSITE"], "VIDEO"))),
    existing ? null : field("Meeting link", input("meeting_link", { type: "url", placeholder: "https://meet.google.com/...", maxlength: 500 }), { hint: "Must start with https://" }),
    existing ? null : field("Notes for the panel", textarea("notes", { maxlength: 4000 })),
    h("p", { class: "faint xs" }, "The candidate gets an email with a calendar invite. Overlapping bookings for the interviewer or the candidate are refused."));
  const submit = h("button", { class: "btn btn-primary", type: "button" }, existing ? "Reschedule" : "Schedule");
  const { close } = modal({ title: existing ? "Reschedule interview" : `Interview ${name}`, body: form,
    footer: [h("button", { class: "btn", type: "button", onclick: () => close() }, "Cancel"), submit] });
  submit.addEventListener("click", () => busy(submit, async () => {
    showFormError(form, null);
    const v = readForm(form);
    if (!v.scheduled_at) { showFormError(form, { message: "Pick a date and time" }); return; }
    const payload = { ...v, scheduled_at: new Date(v.scheduled_at).toISOString(), duration_minutes: Number(v.duration_minutes) };
    if (payload.interviewer_id) payload.interviewer_id = Number(payload.interviewer_id);
    try {
      if (existing) await patch(`/api/recruiter/interviews/${existing.interview_id}`, { scheduled_at: payload.scheduled_at, duration_minutes: payload.duration_minutes });
      else await post("/api/recruiter/interviews", { application_id: a.application_id, ...payload });
      close(); toast(existing ? "Interview rescheduled, invite updated" : "Interview scheduled, invite sent"); ctx.rerender();
    } catch (err) { showFormError(form, err); }
  }));
}

function makeOffer(a, name, ctx) {
  const join = new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10);
  const form = h("form", { class: "form", novalidate: true },
    h("div", { class: "form-row" }, field("Yearly salary", input("salary", { type: "number", min: 1, required: true })),
      field("Currency", select("currency", ["INR", "USD", "EUR", "GBP", "SGD", "AED"], "INR"))),
    h("div", { class: "form-row" }, field("Joining date", input("joining_date", { type: "date", required: true, value: join })),
      field("Candidate has", select("expires_in_days", [[3, "3 days to answer"], [7, "7 days to answer"], [14, "14 days to answer"]], 7))),
    field("Note to the candidate", textarea("notes", { maxlength: 4000 })));
  const submit = h("button", { class: "btn btn-primary", type: "button" }, "Send offer");
  const { close } = modal({ title: `Offer for ${name}`, body: form, footer: [h("button", { class: "btn", type: "button", onclick: () => close() }, "Cancel"), submit] });
  submit.addEventListener("click", () => busy(submit, async () => {
    showFormError(form, null);
    const v = readForm(form);
    try {
      await post("/api/recruiter/offers", { application_id: a.application_id, ...v, expires_in_days: Number(v.expires_in_days) });
      close(); toast("Offer sent"); ctx.rerender();
    } catch (err) { showFormError(form, err); }
  }));
}

// =============================================================================================
// Talent search
// =============================================================================================
async function searchPage(ctx) {
  const [dict, jobs] = await Promise.all([skillDictionary(), get("/api/recruiter/jobs", { limit: 50 })]);
  if (!ctx.alive()) return;
  const mustHave = new Map();
  const mustBox = h("div", { class: "chips" });
  const renderMust = () => clear(mustBox, mustHave.size ? [...mustHave].map(([id, n]) => h("span", { class: "chip" }, n,
    h("button", { type: "button", "aria-label": `Remove ${n}`, onclick: () => { mustHave.delete(id); renderMust(); } }, "×"))) : h("span", { class: "faint xs" }, "None"));
  renderMust();
  const mustSelect = h("select", { "aria-label": "Must-have skill" }, h("option", { value: "" }, "Add a must-have skill"), dict.map((s) => h("option", { value: s.skill_id }, s.skill_name)));
  mustSelect.addEventListener("change", () => {
    if (mustSelect.value && mustHave.size < 10) { mustHave.set(Number(mustSelect.value), mustSelect.selectedOptions[0].textContent); renderMust(); }
    mustSelect.value = "";
  });

  const desc = textarea("description", { class: "tall", maxlength: 20000, placeholder: "Paste a job description, or describe the person you need: the work, the stack, the seniority." });
  const titleIn = input("title", { maxlength: 200, placeholder: "Backend engineer" });
  const jobSelect = select("job_id", [["", "None, use the text below"], ...jobs.items.map((j) => [j.job_id, j.title])], ctx.query.get("job") || "");
  const fillFromJob = async () => {
    if (!jobSelect.value) return;
    try { const j = await get(`/api/recruiter/jobs/${jobSelect.value}`); desc.value = j.description; titleIn.value = j.title; } catch (err) { toast(errorText(err), "error"); }
  };
  jobSelect.addEventListener("change", fillFromJob);

  const form = h("form", { class: "form", novalidate: true },
    field("Start from a job", jobSelect, { hint: "Uses that job's skills for scoring." }),
    field("Title", titleIn),
    field("What are you looking for?", desc),
    h("div", { class: "field" }, h("span", { class: "label" }, "Must have"), mustSelect, mustBox),
    h("div", { class: "form-row" },
      field("Min years", input("min_experience", { type: "number", min: 0, max: 50, step: "0.5" })),
      field("Max notice (days)", input("max_notice_days", { type: "number", min: 0, max: 365 }))),
    field("Location contains", input("location", { maxlength: 100 })),
    h("label", { class: "check" }, h("input", { type: "checkbox", name: "use_ai", checked: true }), "AI review of the top 20"),
    h("button", { class: "btn btn-primary btn-lg btn-block", type: "submit" }, icon("search"), "Search candidates"));
  const results = h("div", {}, panel(null, empty("Search the talent pool",
    "Results come from applicants who chose to be discoverable. They're retrieved by meaning, filtered on your criteria, then ranked.")));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showFormError(form, null);
    const v = readForm(form);
    const body = {
      title: v.title || "", description: v.description || "", job_id: v.job_id ? Number(v.job_id) : null, use_ai: v.use_ai,
      filters: { must_have_skill_ids: [...mustHave.keys()], min_experience: v.min_experience, location: v.location, max_notice_days: v.max_notice_days },
    };
    await busy(e.submitter, async () => {
      clear(results, panel(null, loading("Retrieving, filtering and ranking candidates")));
      try { renderResults(results, await post("/api/recruiter/search", body)); }
      catch (err) { showFormError(form, err); clear(results); }
    });
  });

  clear(ctx.el,
    pageHead("Talent search", "Find people who haven't applied yet. Candidates are retrieved by meaning, not just keywords."),
    h("div", { class: "split", style: { gridTemplateColumns: "360px minmax(0, 1fr)" } }, panel(null, form), results));
  if (ctx.query.get("job")) fillFromJob();
}

function renderResults(el, data) {
  const rows = data.items;
  clear(el, panel(`${rows.length} candidate${rows.length === 1 ? "" : "s"}`, rows.length ? h("div", {}, rows.map((r, i) => {
    const more = h("div", { class: "stack-sm", hidden: true, style: { marginTop: "8px" } },
      r.ai_explanation ? h("p", { class: "quote" }, r.ai_explanation) : null,
      breakdown([["Skills", r.skill_score], ["Experience fit", r.experience_score], ["Semantic", r.semantic_score]]),
      r.strengths?.length ? h("p", { class: "small" }, r.strengths.join("; ")) : null);
    return h("div", { class: "list-item" },
      h("span", { class: `rank ${i === 0 ? "top" : ""}`, style: { width: "22px" } }, i + 1),
      h("div", { class: "grow" },
        h("div", { class: "row-between" },
          h("div", {}, h("a", { class: "title-cell", href: `#/candidates/${r.applicant_id}`, style: { textDecoration: "none" } }, r.name),
            h("div", { class: "sub" }, [r.headline, r.location, fmtYears(r.experience_years)].filter(Boolean).join(", "))),
          h("div", { class: "row" }, score(r.overall_score), badge(r.recommendation))),
        h("div", { class: "row", style: { marginTop: "6px" } }, chips(r.matched_skills?.slice(0, 5) || [], "have"), r.missing_skills?.length ? chips(r.missing_skills.slice(0, 3), "missing") : null,
          h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: (e) => { more.hidden = !more.hidden; e.currentTarget.textContent = more.hidden ? "Why this rank" : "Hide"; } }, "Why this rank")),
        more));
  })) : empty("No one matches", "Loosen the filters: fewer must-have skills, fewer years, or no location."),
  { flush: true, actions: h("span", { class: "faint xs" }, data.llm_used ? "AI-reviewed and blended with database scores" : "Database scoring only") }));
}

async function historyPage(ctx) {
  const run = async (offset = 0) => {
    const data = await get("/api/recruiter/search/history", { limit: 20, offset });
    if (!ctx.alive()) return;
    clear(ctx.el, pageHead("Search history", "Your past talent searches, with the results as they were at the time."),
      panel(null, h("div", {}, table([
        { label: "Search", render: (s) => h("div", {}, h("div", { class: "title-cell" }, s.filters?.title || s.job_title || "Untitled search"),
          h("div", { class: "sub", style: { maxWidth: "60ch", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }, s.query)) },
        { label: "Results", class: "right", render: (s) => h("span", { class: "num" }, s.results_count) },
        { label: "AI", render: (s) => (s.llm_used ? badge("COMPLETED", "Reviewed") : h("span", { class: "faint small" }, "No")) },
        { label: "When", render: (s) => fmtAgo(s.created_at) },
      ], data.items, {
        onRowClick: async (s) => {
          const full = await get(`/api/recruiter/search/history/${s.search_id}`);
          const body = h("div");
          renderResults(body, { items: full.results, llm_used: full.llm_used });
          modal({ title: s.filters?.title || "Search results", body, wide: true });
        },
        emptyState: empty("No searches yet", "Searches you run are saved here.", h("a", { class: "btn btn-primary", href: "#/search" }, "Search talent")),
      }), pager(data, run)), { flush: true }));
  };
  await run(0);
}

// =============================================================================================
// Interviews and offers
// =============================================================================================
async function interviewsPage(ctx) {
  const scope = ctx.query.get("scope") || "upcoming";
  const body = h("div");
  clear(ctx.el, pageHead("Interviews", "Everything scheduled across your company."),
    tabs([["upcoming", "Upcoming"], ["past", "Past and cancelled"], ["all", "All"]], scope, (s) => { window.location.hash = `#/interviews?scope=${s}`; }), body);
  const run = async (offset = 0) => {
    clear(body, loading());
    const data = await get("/api/recruiter/interviews", { scope, limit: 25, offset });
    if (!ctx.alive()) return;
    clear(body, panel(null, h("div", {}, table([
      { label: "When", render: (i) => h("div", {}, h("div", { class: "title-cell" }, fmtDateTime(i.scheduled_at)), h("div", { class: "sub" }, `${i.duration_minutes} min, ${humanize(i.interview_type)}`)) },
      { label: "Candidate", render: (i) => h("a", { href: `#/applications/${i.application_id}` }, i.candidate_name) },
      { label: "Job", render: (i) => i.job_title },
      { label: "Interviewer", render: (i) => i.interviewer_name },
      { label: "Status", render: (i) => badge(i.status) },
      { label: "", class: "right", render: (i) => interviewActions(i, ctx) },
    ], data.items, { emptyState: empty(scope === "upcoming" ? "Nothing scheduled" : "No interviews", "Schedule interviews from a shortlisted candidate's page.") }),
    pager(data, run)), { flush: true }));
  };
  await run(0);
}

function interviewActions(i, ctx) {
  const box = h("div", { class: "row", style: { justifyContent: "flex-end", flexWrap: "nowrap" } });
  const started = new Date(i.scheduled_at) <= new Date();
  if (i.status === "SCHEDULED" && !started) {
    box.append(h("button", { class: "btn btn-sm", onclick: () => scheduleInterview(null, i.candidate_name, ctx, i) }, "Reschedule"));
    box.append(h("button", { class: "btn btn-ghost btn-sm btn-danger", onclick: async () => {
      if (!(await confirmDialog("Cancel this interview?", "The candidate gets a cancellation and the calendar entry is removed.", "Cancel interview", true))) return;
      try { await patch(`/api/recruiter/interviews/${i.interview_id}`, { status: "CANCELLED" }); toast("Interview cancelled"); ctx.rerender(); } catch (err) { toast(errorText(err), "error"); }
    } }, "Cancel"));
  }
  if (i.status !== "CANCELLED" && started && !i.has_feedback) box.append(h("button", { class: "btn btn-sm btn-primary", onclick: () => feedbackForm(i, ctx) }, "Add feedback"));
  if (i.has_feedback) box.append(h("span", { class: "faint small" }, "Feedback in"));
  return box;
}

function feedbackForm(i, ctx) {
  const scoreSel = (name, label) => field(label, select(name, Array.from({ length: 10 }, (_, k) => [k + 1, String(k + 1)]), 6));
  const form = h("form", { class: "form", novalidate: true },
    h("div", { class: "form-row" }, scoreSel("technical_score", "Technical"), scoreSel("communication_score", "Communication"),
      scoreSel("problem_solving_score", "Problem solving"), scoreSel("overall_score", "Overall")),
    field("Recommendation", select("recommendation", ["STRONG_HIRE", "HIRE", "NO_HIRE", "STRONG_NO_HIRE"], "HIRE")),
    field("Comments", textarea("comments", { maxlength: 4000, placeholder: "What stood out, concerns, what to probe next" })));
  const submit = h("button", { class: "btn btn-primary", type: "button" }, "Submit feedback");
  const { close } = modal({ title: `Feedback: ${i.candidate_name}`, body: form, footer: [h("button", { class: "btn", type: "button", onclick: () => close() }, "Cancel"), submit] });
  submit.addEventListener("click", () => busy(submit, async () => {
    const v = readForm(form);
    for (const k of ["technical_score", "communication_score", "problem_solving_score", "overall_score"]) v[k] = Number(v[k]);
    try { await post(`/api/recruiter/interviews/${i.interview_id}/feedback`, v); close(); toast("Feedback saved"); ctx.rerender(); }
    catch (err) { showFormError(form, err); }
  }));
}

async function offersPage(ctx) {
  const run = async (offset = 0) => {
    const data = await get("/api/recruiter/offers", { limit: 25, offset });
    if (!ctx.alive()) return;
    clear(ctx.el, pageHead("Offers", "Offers expire automatically when the candidate doesn't answer in time."),
      panel(null, h("div", {}, table([
        { label: "Candidate", render: (o) => h("a", { href: `#/applications/${o.application_id}` }, o.candidate_name) },
        { label: "Job", render: (o) => o.job_title },
        { label: "Salary", class: "right", render: (o) => h("span", { class: "num" }, fmtMoney(o.salary, o.currency)) },
        { label: "Joining", render: (o) => fmtDate(o.joining_date) },
        { label: "Status", render: (o) => badge(o.offer_status) },
        { label: "Answer by", render: (o) => (o.offer_status === "PENDING" ? fmtDate(o.expires_at) : o.responded_at ? `Answered ${fmtAgo(o.responded_at)}` : "-") },
      ], data.items, { emptyState: empty("No offers yet", "Make an offer from a shortlisted or interviewed candidate's page.") }), pager(data, run)), { flush: true }));
  };
  await run(0);
}

// =============================================================================================
// Company and team
// =============================================================================================
async function teamPage(ctx) {
  teamCache = null;
  const t = await team();
  if (!ctx.alive()) return;
  const c = t.company, admin = t.me.is_company_admin;
  const form = h("form", { class: "form", novalidate: true },
    field("Company name", input("company_name", { value: c.company_name, maxlength: 200, disabled: !admin })),
    h("div", { class: "form-row" }, field("Industry", input("industry", { value: c.industry || "", maxlength: 100, disabled: !admin })),
      field("City", input("location", { value: c.location || "", maxlength: 150, disabled: !admin }))),
    field("Website", input("website", { value: c.website || "", type: "url", maxlength: 500, disabled: !admin })),
    field("About", textarea("description", { value: c.description || "", maxlength: 4000, disabled: !admin }), { hint: "Shown to candidates on your job pages." }),
    admin ? h("div", { class: "form-actions" }, h("button", { class: "btn btn-primary", type: "submit" }, "Save changes")) : h("p", { class: "faint xs" }, "Only company admins can edit these details."));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showFormError(form, null);
    await busy(e.submitter, async () => { try { await patch("/api/company", readForm(form)); toast("Changes saved"); } catch (err) { showFormError(form, err); } });
  });

  const invite = admin ? panel("Invite teammates", h("div", { class: "stack" },
    h("p", { class: "muted small" }, "Share this code with colleagues. They sign up on the recruiter portal and choose Join with an invite code."),
    h("div", { class: "row" }, h("code", { class: "chip mono", style: { fontSize: "13px", padding: "6px 10px" } }, t.invite_code),
      h("button", { class: "btn btn-sm", onclick: async () => {
        try { await navigator.clipboard.writeText(t.invite_code); toast("Code copied"); } catch { toast("Copy the code manually", "error"); }
      } }, icon("copy"), "Copy"),
      h("button", { class: "btn btn-sm", onclick: async () => {
        if (!(await confirmDialog("Generate a new code?", "The current code stops working immediately. People who already joined keep access.", "Generate new code"))) return;
        try { await post("/api/company/invite/rotate"); toast("New code generated"); ctx.rerender(); } catch (err) { toast(errorText(err), "error"); }
      } }, icon("refresh"), "New code")))) : null;

  clear(ctx.el, pageHead("Company and team", null),
    h("div", { class: "split" },
      h("div", { class: "stack" }, panel("Company details", form),
        panel("Team", table([
          { label: "Name", render: (m) => h("div", {}, h("div", { class: "title-cell" }, `${m.first_name} ${m.last_name}`), h("div", { class: "sub" }, m.designation || "")) },
          { label: "Email", render: (m) => m.email },
          { label: "Role", render: (m) => (m.is_company_admin ? badge("COMPLETED", "Admin") : h("span", { class: "muted small" }, "Recruiter")) },
          { label: "Joined", render: (m) => fmtDate(m.created_at) },
        ], t.team), { flush: true })),
      h("div", { class: "stack" }, invite)));
}

startApp({
  portal: "recruiter",
  nav: NAV,
  onboarding,
  routes: [
    { path: "", nav: "dashboard", title: "Dashboard", render: dashboard },
    { path: "jobs", nav: "jobs", title: "Jobs", render: jobsPage },
    { path: "jobs/new", nav: "jobs", title: "New job", render: jobForm },
    { path: "jobs/:id", nav: "jobs", title: "Job", render: jobDetail },
    { path: "jobs/:id/edit", nav: "jobs", title: "Edit job", render: jobForm },
    { path: "applications/:id", nav: "jobs", title: "Candidate", render: applicationDetail },
    { path: "candidates/:id", nav: "search", title: "Candidate", render: candidateDetail },
    { path: "search", nav: "search", title: "Talent search", render: searchPage },
    { path: "history", nav: "history", title: "Search history", render: historyPage },
    { path: "interviews", nav: "interviews", title: "Interviews", render: interviewsPage },
    { path: "offers", nav: "offers", title: "Offers", render: offersPage },
    { path: "notifications", nav: "notifications", title: "Notifications", render: notificationsPage },
    { path: "team", nav: "team", title: "Company and team", render: teamPage },
  ],
});
