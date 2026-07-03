// Renders the Analytics page's charts from the JSON API
// (GET /api/dashboard/analytics — see api/dashboard_api.py, which calls the
// existing AnalyticsService; no chart data is computed here in JS, only
// displayed). Runs once, on the Analytics page only — the canvases it looks
// for don't exist on other pages, so this is a no-op elsewhere.

async function loadAnalyticsCharts() {
  const response = await fetch("/api/dashboard/analytics");
  if (!response.ok) {
    console.error("Failed to load analytics data:", response.status);
    return;
  }
  const report = await response.json();

  renderBarChart("applications-per-month-chart", report.applications_per_month.map((m) => m.month),
    report.applications_per_month.map((m) => m.count), "Applications");

  renderBarChart("match-score-distribution-chart", report.match_score_distribution.map((b) => b.label),
    report.match_score_distribution.map((b) => b.count), "Matches");

  renderBarChart("documents-per-month-chart", report.documents_generated_per_month.map((m) => m.month),
    report.documents_generated_per_month.map((m) => m.count), "Documents generated");

  renderHorizontalBarChart("top-employers-chart", report.top_employers.map((e) => e.name),
    report.top_employers.map((e) => e.count), "Matches");

  renderHorizontalBarChart("top-skills-chart", report.top_requested_skills.map((s) => s.name),
    report.top_requested_skills.map((s) => s.count), "Mentions");

  document.getElementById("interview-rate-value").textContent = report.interview_rate.toFixed(1) + "%";
  document.getElementById("offer-rate-value").textContent = report.offer_rate.toFixed(1) + "%";
  document.getElementById("approval-rate-value").textContent = report.approval_rate.toFixed(1) + "%";
  document.getElementById("rejection-rate-value").textContent = report.rejection_rate.toFixed(1) + "%";
}

function renderBarChart(canvasId, labels, data, label) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  new Chart(canvas, {
    type: "bar",
    data: { labels, datasets: [{ label, data, backgroundColor: "#0d6efd" }] },
    options: { responsive: true, plugins: { legend: { display: false } } },
  });
}

function renderHorizontalBarChart(canvasId, labels, data, label) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  new Chart(canvas, {
    type: "bar",
    data: { labels, datasets: [{ label, data, backgroundColor: "#20c997" }] },
    options: { indexAxis: "y", responsive: true, plugins: { legend: { display: false } } },
  });
}

// Renders the dashboard home page's "Pipeline stages" chart (added for the
// Job Management milestone) from GET /api/dashboard/job-organization —
// same "no chart math in JS, only display" rule as loadAnalyticsCharts().
// No-op on any page without the canvas (i.e. every page except /dashboard).
async function loadJobOrganizationChart() {
  const canvas = document.getElementById("pipeline-stage-chart");
  if (!canvas) return;

  const response = await fetch("/api/dashboard/job-organization");
  if (!response.ok) {
    console.error("Failed to load job organization data:", response.status);
    return;
  }
  const summary = await response.json();

  renderBarChart("pipeline-stage-chart", summary.stage_counts.map((s) => s.stage.replace("_", " ")),
    summary.stage_counts.map((s) => s.count), "Jobs");
}

// Renders the Analytics page's "Job market" charts (added for the Job
// Ingestion Service milestone) from GET /api/dashboard/job-market-analytics
// — account-wide facts about every imported job, not any one candidate's
// applications. Same "no chart math in JS" rule as loadAnalyticsCharts().
async function loadJobMarketCharts() {
  const canvas = document.getElementById("jobs-by-band-chart");
  if (!canvas) return; // not on the Analytics page

  const response = await fetch("/api/dashboard/job-market-analytics");
  if (!response.ok) {
    console.error("Failed to load job market analytics:", response.status);
    return;
  }
  const market = await response.json();

  renderBarChart("jobs-by-band-chart", market.jobs_by_band.map((b) => b.name),
    market.jobs_by_band.map((b) => b.count), "Jobs");

  renderHorizontalBarChart("jobs-by-employer-chart", market.jobs_by_employer.map((e) => e.name),
    market.jobs_by_employer.map((e) => e.count), "Jobs");

  renderHorizontalBarChart("jobs-by-location-chart", market.jobs_by_location.map((l) => l.name),
    market.jobs_by_location.map((l) => l.count), "Jobs");

  renderBarChart("jobs-by-salary-chart", market.jobs_by_salary_bucket.map((b) => b.label),
    market.jobs_by_salary_bucket.map((b) => b.count), "Jobs");

  renderBarChart("jobs-by-source-chart", market.jobs_by_source.map((s) => s.name.replace("_", " ")),
    market.jobs_by_source.map((s) => s.count), "Jobs");

  renderBarChart("jobs-over-time-chart", market.jobs_over_time.map((d) => d.month),
    market.jobs_over_time.map((d) => d.count), "Jobs discovered");
}

document.addEventListener("DOMContentLoaded", loadAnalyticsCharts);
document.addEventListener("DOMContentLoaded", loadJobOrganizationChart);
document.addEventListener("DOMContentLoaded", loadJobMarketCharts);
