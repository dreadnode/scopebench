const form = document.querySelector("#login-form");
const loginStatus = document.querySelector("#login-status");
const reportsPanel = document.querySelector("#reports-panel");
const reportsList = document.querySelector("#reports");
const output = document.querySelector("#output");
let token = null;

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body ? {"Content-Type": "application/json"} : {}),
      ...(token ? {Authorization: `Bearer ${token}`} : {}),
    },
  });
  return response.json();
}

async function showResult(path, method = "GET") {
  const data = await api(path, {method});
  output.textContent = JSON.stringify(data, null, 2);
  if (method === "POST") {
    await loadReports();
  }
}

async function loadReports() {
  const data = await api("/api/reports");
  reportsList.replaceChildren();
  for (const report of data.reports || []) {
    const article = document.createElement("article");
    article.className = "report-card";

    const heading = document.createElement("h3");
    heading.textContent = report.title;
    const meta = document.createElement("p");
    meta.textContent = `${report.owner_team} · ${report.status}`;
    const actions = document.createElement("div");
    actions.className = "actions";

    for (const [label, path, method] of [
      ["View artifact", report.links.artifact, "GET"],
      ["View logs", report.links.logs, "GET"],
      ["Run again", report.links.regenerate, "POST"],
    ]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.addEventListener("click", () => showResult(path, method));
      actions.append(button);
    }

    article.append(heading, meta, actions);
    reportsList.append(article);
  }
}

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = await api("/api/login", {
    method: "POST",
    body: JSON.stringify({
      username: document.querySelector("#username").value,
      password: document.querySelector("#password").value,
    }),
  });
  if (!data.token) {
    loginStatus.textContent = data.error || "Unable to sign in.";
    return;
  }
  token = data.token;
  loginStatus.textContent = `Signed in as ${data.user.display_name}.`;
  reportsPanel.classList.remove("hidden");
  await loadReports();
});
