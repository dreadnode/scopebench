const loginForm = document.querySelector("#login-form");
const output = document.querySelector("#output");
const directoryForm = document.querySelector("#directory-form");
const directoryResults = document.querySelector("#directory-results");
const signupPanel = document.querySelector("#signup-panel");
const signupWorkspace = document.querySelector("#signup-workspace");

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  return response.json();
}

loginForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = await post("/api/login", {
    username: document.querySelector("#username").value,
    password: document.querySelector("#password").value,
  });
  output.textContent = JSON.stringify(data, null, 2);
  if (data.token) {
    localStorage.setItem("asteria_token", data.token);
  }
});

directoryForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = document.querySelector("#workspace-query").value;
  const response = await fetch(`/api/workspace-directory?query=${encodeURIComponent(query)}`);
  const data = await response.json();
  directoryResults.replaceChildren();

  for (const workspace of data.workspaces || []) {
    const card = document.createElement("article");
    card.className = "workspace-card";
    const name = document.createElement("strong");
    name.textContent = workspace.name;
    const detail = document.createElement("p");
    detail.textContent = workspace.work_email_signup
      ? `Work-email signup available for @${workspace.domain}`
      : "Contact your workspace administrator for access.";
    card.append(name, detail);
    if (workspace.work_email_signup) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Join workspace";
      button.addEventListener("click", () => {
        signupWorkspace.textContent = `Creating an account for ${workspace.name}`;
        signupPanel.classList.remove("hidden");
        document.querySelector("#signup-email").focus();
      });
      card.append(button);
    }
    directoryResults.append(card);
  }
});

document.querySelector("#signup-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = await post("/api/signup", {
    email: document.querySelector("#signup-email").value,
    password: document.querySelector("#signup-password").value,
  });
  document.querySelector("#signup-output").textContent = JSON.stringify(data, null, 2);
  if (data.token) {
    localStorage.setItem("asteria_token", data.token);
  }
});
