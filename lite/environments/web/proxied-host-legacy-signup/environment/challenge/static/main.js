const form = document.querySelector("#login-form");
const output = document.querySelector("#output");

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/api/login", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      email: document.querySelector("#email").value,
      password: document.querySelector("#password").value,
    }),
  });
  const data = await response.json();
  output.textContent = JSON.stringify(data, null, 2);
  if (data.token) {
    localStorage.setItem("harbor_token", data.token);
    const bootstrap = await fetch("/api/organization/bootstrap", {
      headers: {Authorization: `Bearer ${data.token}`},
    });
    output.textContent = JSON.stringify(await bootstrap.json(), null, 2);
  }
});
