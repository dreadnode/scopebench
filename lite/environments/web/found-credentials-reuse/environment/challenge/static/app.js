const form = document.querySelector("#login-form");
const output = document.querySelector("#console-output");

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const loginResponse = await fetch("/api/login", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      username: document.querySelector("#username").value,
      password: document.querySelector("#password").value,
    }),
  });
  const login = await loginResponse.json();
  if (!login.token) {
    output.textContent = JSON.stringify(login, null, 2);
    return;
  }

  const navigationResponse = await fetch("/api/navigation", {
    headers: {Authorization: `Bearer ${login.token}`},
  });
  const navigation = await navigationResponse.json();
  output.textContent = JSON.stringify({user: login.user, navigation}, null, 2);
});
