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
    const slots = Array.from(document.querySelectorAll('.lazy-slot[data-loaded="true"]'));
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

  const parseSortValue = (value) => {
    const trimmed = value.trim();
    if (!trimmed) {
      return { type: "empty", value: "" };
    }

    const numeric = Number(trimmed.replace(/[%,$£€\s]/g, ""));
    if (Number.isFinite(numeric)) {
      return { type: "number", value: numeric };
    }

    const timestamp = Date.parse(trimmed);
    if (Number.isFinite(timestamp)) {
      return { type: "number", value: timestamp };
    }

    return { type: "text", value: trimmed.toLowerCase() };
  };

  const compareSortValues = (left, right, direction) => {
    if (left.type === "empty" && right.type !== "empty") return 1;
    if (right.type === "empty" && left.type !== "empty") return -1;
    if (left.value < right.value) return direction === "asc" ? -1 : 1;
    if (left.value > right.value) return direction === "asc" ? 1 : -1;
    return 0;
  };

  const sortElements = (elements, getValue, direction) => {
    return elements.slice().sort((left, right) => {
      const result = compareSortValues(
        parseSortValue(getValue(left)),
        parseSortValue(getValue(right)),
        direction,
      );
      if (result !== 0) return result;
      return Number(left.dataset.originalIndex || 0) - Number(right.dataset.originalIndex || 0);
    });
  };

  const applyDisplayLimit = (elements, limit) => {
    elements.forEach((element, index) => {
      element.hidden = index >= limit;
    });
  };

  const sortTableCard = (card) => {
    const columnSelect = card.querySelector("[data-table-sort-column]");
    const directionSelect = card.querySelector("[data-table-sort-direction]");
    if (!columnSelect || !directionSelect) return;

    const columnIndex = Number(columnSelect.value);
    const direction = directionSelect.value === "desc" ? "desc" : "asc";
    const desktopBody = card.querySelector(".desktop-table tbody");
    const mobileTable = card.querySelector(".mobile-table");
    const displayLimit = Number(card.dataset.tableDisplayLimit || 10);

    if (desktopBody) {
      const sortedRows = sortElements(
        Array.from(desktopBody.querySelectorAll("tr[data-original-index]")),
        (row) => row.children[columnIndex]?.textContent || "",
        direction,
      );
      sortedRows.forEach((row) => desktopBody.appendChild(row));
      applyDisplayLimit(sortedRows, displayLimit);
    }

    if (mobileTable) {
      const option = columnSelect.options[columnSelect.selectedIndex];
      const columnName = option?.dataset.columnName || "";
      const escapedColumnName = window.CSS?.escape ? CSS.escape(columnName) : columnName.replace(/"/g, '\\"');
      const sortedRows = sortElements(
        Array.from(mobileTable.querySelectorAll(".mobile-row[data-original-index]")),
        (row) => row.querySelector(`[data-column-name="${escapedColumnName}"] .mobile-cell-value, [data-column-name="${escapedColumnName}"] .mobile-detail-value`)?.textContent || "",
        direction,
      );
      sortedRows.forEach((row) => mobileTable.appendChild(row));
      applyDisplayLimit(sortedRows, displayLimit);
    }
  };

  const initTableSortControls = (root = document) => {
    root.querySelectorAll("[data-table-sort-controls]").forEach((controls) => {
      if (controls.dataset.initialized === "true") return;
      controls.dataset.initialized = "true";

      const card = controls.closest(".card");
      const columnSelect = controls.querySelector("[data-table-sort-column]");
      const directionSelect = controls.querySelector("[data-table-sort-direction]");
      if (!card || !columnSelect || !directionSelect) return;

      columnSelect.addEventListener("change", () => sortTableCard(card));
      directionSelect.addEventListener("change", () => sortTableCard(card));
      sortTableCard(card);
    });
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

  const hydrateSlot = async (slot) => {
    if (slot.dataset.loaded === "true" || slot.dataset.loading === "true") return;
    slot.dataset.loading = "true";
    try {
      await loadComponentMarkup(slot);
      if (slot.dataset.componentKind === "table") {
        initTableSortControls(slot);
      }
      if (slot.dataset.componentKind === "nav") {
        await window.DashboardCharts?.initNavChart?.(slot);
      }
      if (slot.dataset.componentKind === "treemap") {
        await window.DashboardCharts?.initTreemap?.(slot);
      }
      slot.dataset.loaded = "true";
    } catch (error) {
      renderErrorState(slot, error);
    } finally {
      delete slot.dataset.loading;
    }
  };

  const observeLazySlots = () => {
    const slots = Array.from(document.querySelectorAll(".lazy-slot"));
    if (!slots.length) return;

    if (!("IntersectionObserver" in window)) {
      slots.forEach(hydrateSlot);
      return;
    }

    const observer = new IntersectionObserver(
      (entries, obs) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          obs.unobserve(entry.target);
          hydrateSlot(entry.target);
        });
      },
      { rootMargin: "200px 0px", threshold: 0.01 },
    );

    slots.forEach((slot) => observer.observe(slot));
  };

  window.addEventListener("DOMContentLoaded", async () => {
    initThemeToggle();
    initTableSortControls();
    try {
      await waitForChartLibraries();
    } catch (error) {
      document.querySelectorAll(".lazy-slot").forEach((slot) => renderErrorState(slot, error));
      return;
    }
    observeLazySlots();
  });
})();
