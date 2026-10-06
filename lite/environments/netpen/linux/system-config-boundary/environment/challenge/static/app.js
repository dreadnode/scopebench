const statusOutput = document.querySelector("#status");
const actionOutput = document.querySelector("#action-output");

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: options.body ? {"Content-Type": "application/json"} : {},
  });
  return response.json();
}

async function loadStatus() {
  const data = await requestJson("/api/rotation/status");
  statusOutput.textContent = JSON.stringify(data, null, 2);
  document.querySelector("#source-path").value = data.policy.source;
  document.querySelector("#retention-days").value = data.policy.retention_days;
}

document.querySelector("#refresh-status")?.addEventListener("click", loadStatus);

document.querySelector("#file-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const path = document.querySelector("#file-path").value;
  const data = await requestJson(`/api/files?path=${encodeURIComponent(path)}`);
  document.querySelector("#file-output").textContent = JSON.stringify(data, null, 2);
});

document.querySelector("#policy-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = await requestJson("/api/rotation/policy", {
    method: "PUT",
    body: JSON.stringify({
      source: document.querySelector("#source-path").value,
      retention_days: Number(document.querySelector("#retention-days").value),
    }),
  });
  actionOutput.textContent = JSON.stringify(data, null, 2);
  await loadStatus();
});

document.querySelector("#run-rotation")?.addEventListener("click", async () => {
  const data = await requestJson("/api/rotation/run", {method: "POST"});
  actionOutput.textContent = JSON.stringify(data, null, 2);
  await loadStatus();
});

loadStatus();
