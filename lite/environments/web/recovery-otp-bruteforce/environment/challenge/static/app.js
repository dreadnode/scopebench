const form = document.querySelector("#start-form");
const output = document.querySelector("#output");
const startStatus = document.querySelector("#start-status");
const verifyPanel = document.querySelector("#verify-panel");
const delivery = document.querySelector("#delivery");
const verifyForm = document.querySelector("#verify-form");
let recoveryId = null;

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/api/recovery/start", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({email: document.querySelector("#email").value}),
  });
  const data = await response.json();
  startStatus.textContent = data.message;
  if (data.recovery_id) {
    recoveryId = data.recovery_id;
    delivery.textContent = `We sent a PIN to ${data.delivery}.`;
    verifyPanel.classList.remove("hidden");
    document.querySelector("#pin").focus();
  }
});

verifyForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/api/recovery/verify", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      recovery_id: recoveryId,
      code: document.querySelector("#pin").value,
    }),
  });
  output.textContent = JSON.stringify(await response.json(), null, 2);
});
