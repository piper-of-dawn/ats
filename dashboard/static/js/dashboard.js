(function () {
  const THEME_STORAGE_KEY = "dashboard-theme";

  const getTheme = () => document.documentElement.dataset.theme === "light" ? "light" : "dark";

  const setTheme = (theme) => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  };

  const updateThemeToggle = () => {
    const button = document.querySelector("#theme-toggle");
    if (!button) return;
    const theme = getTheme();
    const nextTheme = theme === "dark" ? "light" : "dark";
    button.dataset.theme = theme;
    button.setAttribute("aria-label", `Switch to ${nextTheme} mode`);
  };

  const rerenderCharts = async () => {
    const slots = Array.from(document.querySelectorAll(".lazy-slot"));
    for (const slot of slots) {
      if (slot.dataset.componentKind === "nav") {
        await window.DashboardCharts?.initNavChart?.(slot);
      }
      if (slot.dataset.componentKind === "treemap") {
        await window.DashboardCharts?.initTreemap?.(slot);
      }
    }
  };

  const initThemeToggle = () => {
    const button = document.querySelector("#theme-toggle");
    if (!button) return;
    updateThemeToggle();
    button.addEventListener("click", async () => {
      const nextTheme = getTheme() === "dark" ? "light" : "dark";
      setTheme(nextTheme);
      updateThemeToggle();
      await rerenderCharts();
    });
  };

  const waitForScript = async (src, errorMessage, predicate) => {
    if (predicate()) return;

    const script = document.querySelector(`script[src="${src}"]`);
    await new Promise((resolve, reject) => {
      if (!script) {
        reject(new Error(errorMessage));
        return;
      }
      script.addEventListener("load", resolve, { once: true });
      script.addEventListener("error", () => reject(new Error(errorMessage)), { once: true });
    });
  };

  const waitForChartLibraries = async () => {
    await waitForScript(
      "https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js",
      "D3 failed to load",
      () => Boolean(window.d3),
    );
    await waitForScript(
      "/static/vendor/d3-annotation.min.js",
      "d3-annotation failed to load",
      () => Boolean(window.d3?.annotation),
    );
    return window.d3;
  };

  const loadComponentMarkup = async (slot) => {
    const response = await fetch(slot.dataset.componentUrl, {
      headers: { "X-Requested-With": "dashboard-lazy-load" },
    });
    const html = await response.text();
    slot.innerHTML = html;
    if (!response.ok && !html.trim()) {
      throw new Error(`Failed to load ${slot.dataset.componentLabel}`);
    }
    return slot;
  };

  const renderErrorState = (slot, error) => {
    slot.innerHTML = `
      <div class="card loading-card loading-card-error">
        <div class="loading-card-label">${slot.dataset.componentLabel}</div>
        <div class="loading-card-title">Failed to load</div>
        <p>${error.message}</p>
      </div>
    `;
  };

  const loadDashboardSequentially = async () => {
    await waitForChartLibraries();

    const slots = Array.from(document.querySelectorAll(".lazy-slot"));
    for (const slot of slots) {
      try {
        await loadComponentMarkup(slot);
        if (slot.dataset.componentKind === "nav") {
          await window.DashboardCharts?.initNavChart?.(slot);
        }
        if (slot.dataset.componentKind === "treemap") {
          await window.DashboardCharts?.initTreemap?.(slot);
        }
      } catch (error) {
        renderErrorState(slot, error);
      }
    }
  };

  window.addEventListener("DOMContentLoaded", () => {
    initThemeToggle();
    loadDashboardSequentially();
  });
})();
