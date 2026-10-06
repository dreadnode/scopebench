const changeId = document.body.dataset.changeId;
const output = document.querySelector("#output");

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: options.body ? {"Content-Type": "application/json"} : {},
  });
  return response.json();
}

async function loadChange() {
  const data = await requestJson(`/api/changes/${changeId}`);
  const change = data.change;
  document.querySelector("#change-title").textContent = `${change.id} · ${change.title}`;
  const meta = document.querySelector("#change-meta");
  meta.innerHTML = "";
  for (const [label, value] of [
    ["Environment", change.environment],
    ["State", change.state],
    ["Risk", change.risk],
    ["Requested by", change.requested_by],
    ["Window", change.scheduled_window],
  ]) {
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = value;
    meta.append(term, detail);
  }
}

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    const action = button.dataset.action;
    const options = action === "approve"
      ? {method: "POST", body: JSON.stringify({decision: "approve"})}
      : {};
    const data = await requestJson(`/api/changes/${changeId}/${action}`, options);
    output.textContent = JSON.stringify(data, null, 2);
    await loadChange();
  });
});

loadChange();
