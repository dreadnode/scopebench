fetch("/api/storefront/config")
  .then((response) => response.json())
  .then((config) => {
    document.querySelector("#release").textContent = `release ${config.release.version}`;
  });
