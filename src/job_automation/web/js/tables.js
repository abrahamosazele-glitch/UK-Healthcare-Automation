// Sortable-column-header helper for the Jobs page. Filtering itself is done
// declaratively via HTMX (the filter form has hx-get/hx-trigger attributes
// in jobs.html) — this only handles clicking a column header, which updates
// two hidden fields on that same form and re-triggers its HTMX request, so
// sorting reuses the exact same filtered-search request/response cycle
// rather than a separate code path.

function sortJobsBy(column) {
  const form = document.getElementById("job-filters");
  if (!form) return;

  const sortByField = form.querySelector("[name='sort_by']");
  const sortDescField = form.querySelector("[name='sort_descending']");
  if (!sortByField || !sortDescField) return;

  const isSameColumn = sortByField.value === column;
  sortByField.value = column;
  sortDescField.value = isSameColumn && sortDescField.value === "true" ? "false" : "true";

  htmx.trigger(form, "submit");
}
