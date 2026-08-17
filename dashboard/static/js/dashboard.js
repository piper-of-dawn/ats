(function () {
  const THEME_STORAGE_KEY = "dashboard-theme";
  const DEFAULT_TABLE_PAGE_SIZE = 25;

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
      if (slot.dataset.componentKind === "portfolio-history") {
        await window.DashboardCharts?.initPortfolioHistoryChart?.(slot);
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

  const rankingAttributeName = (strategy) => `data-rank-${strategy.replaceAll("_", "-")}`;

  const selectedSortOption = (columnSelect) => columnSelect.options[columnSelect.selectedIndex];

  const isStrategyOption = (option) => option?.dataset.sortKind === "strategy";

  const strategyOptionFor = (columnSelect, strategy) => Array.from(columnSelect.options)
    .find((option) => option.dataset.strategy === strategy);

  const updateRankingPresentation = (card, columnSelect) => {
    const selectedOption = selectedSortOption(columnSelect);
    const strategy = isStrategyOption(selectedOption)
      ? selectedOption.dataset.strategy
      : card.dataset.defaultRankingStrategy;
    if (!strategy) return;

    const rankAttribute = rankingAttributeName(strategy);
    card.querySelectorAll("[data-original-index]").forEach((row) => {
      const rank = row.getAttribute(rankAttribute) || "—";
      row.querySelectorAll("[data-momentum-rank-value]").forEach((value) => {
        value.textContent = rank;
      });
    });

    const helper = card.querySelector("[data-table-ranking-helper]");
    const strategyOption = strategyOptionFor(columnSelect, strategy);
    if (helper && strategyOption?.dataset.description) {
      helper.textContent = strategyOption.dataset.description;
    }
  };

  const updateOrderLabels = (columnSelect, directionSelect) => {
    const strategySelected = isStrategyOption(selectedSortOption(columnSelect));
    const ascendingOption = directionSelect.querySelector('option[value="asc"]');
    const descendingOption = directionSelect.querySelector('option[value="desc"]');
    if (ascendingOption) ascendingOption.textContent = strategySelected ? "Best first" : "Asc";
    if (descendingOption) descendingOption.textContent = strategySelected ? "Worst first" : "Desc";
    directionSelect.setAttribute(
      "aria-label",
      strategySelected ? "Ranking order" : "Sort direction",
    );
  };

  const sortTableCard = (card) => {
    const columnSelect = card.querySelector("[data-table-sort-column]");
    const directionSelect = card.querySelector("[data-table-sort-direction]");
    if (!columnSelect || !directionSelect) return;

    const option = selectedSortOption(columnSelect);
    const strategy = isStrategyOption(option) ? option.dataset.strategy : null;
    const columnIndex = strategy ? null : Number(columnSelect.value);
    const direction = directionSelect.value === "desc" ? "desc" : "asc";
    const desktopBody = card.querySelector(".desktop-table tbody");
    const mobileTable = card.querySelector(".mobile-table");

    updateRankingPresentation(card, columnSelect);

    if (desktopBody) {
      const sortedRows = sortElements(
        Array.from(desktopBody.querySelectorAll("tr[data-original-index]")),
        (row) => strategy
          ? row.getAttribute(rankingAttributeName(strategy)) || ""
          : row.children[columnIndex]?.textContent || "",
        direction,
      );
      sortedRows.forEach((row) => desktopBody.appendChild(row));
    }

    if (mobileTable) {
      const columnName = option?.dataset.columnName || "";
      const escapedColumnName = window.CSS?.escape ? CSS.escape(columnName) : columnName.replace(/"/g, '\\"');
      const sortedRows = sortElements(
        Array.from(mobileTable.querySelectorAll(".mobile-row[data-original-index]")),
        (row) => strategy
          ? row.getAttribute(rankingAttributeName(strategy)) || ""
          : row.querySelector(`[data-column-name="${escapedColumnName}"] .mobile-cell-value, [data-column-name="${escapedColumnName}"] .mobile-detail-value`)?.textContent || "",
        direction,
      );
      sortedRows.forEach((row) => mobileTable.appendChild(row));
    }

    renderTablePage(card, 1);
  };

  const getTableRows = (card, selector) => Array.from(card.querySelectorAll(selector));

  const getPageSize = (card) => {
    const pageSizeSelect = card.querySelector("[data-table-page-size]");
    const pageSize = Number(pageSizeSelect?.value || DEFAULT_TABLE_PAGE_SIZE);
    return Number.isFinite(pageSize) && pageSize > 0 ? pageSize : DEFAULT_TABLE_PAGE_SIZE;
  };

  const getCurrentPage = (card) => {
    const currentPage = Number(card.dataset.tableCurrentPage || 1);
    return Number.isFinite(currentPage) && currentPage > 0 ? currentPage : 1;
  };

  const setRowsForPage = (rows, currentPage, pageSize) => {
    const startIndex = (currentPage - 1) * pageSize;
    const endIndex = startIndex + pageSize;
    rows.forEach((row, index) => {
      row.hidden = index < startIndex || index >= endIndex;
    });
  };

  const renderTablePage = (card, requestedPage = getCurrentPage(card)) => {
    const pagination = card.querySelector("[data-table-pagination]");
    if (!pagination) return;

    const desktopRows = getTableRows(card, ".desktop-table tbody tr[data-original-index]");
    const mobileRows = getTableRows(card, ".mobile-table .mobile-row[data-original-index]");
    const totalRows = Math.max(desktopRows.length, mobileRows.length);
    const pageSize = getPageSize(card);
    const totalPages = Math.max(1, Math.ceil(totalRows / pageSize));
    const currentPage = Math.min(Math.max(1, requestedPage), totalPages);
    const firstVisibleRow = totalRows === 0 ? 0 : (currentPage - 1) * pageSize + 1;
    const lastVisibleRow = totalRows === 0 ? 0 : Math.min(currentPage * pageSize, totalRows);

    card.dataset.tableCurrentPage = String(currentPage);
    setRowsForPage(desktopRows, currentPage, pageSize);
    setRowsForPage(mobileRows, currentPage, pageSize);

    const status = pagination.querySelector("[data-table-pagination-status]");
    const previousButton = pagination.querySelector("[data-table-page-prev]");
    const nextButton = pagination.querySelector("[data-table-page-next]");

    if (status) {
      status.textContent = totalRows === 0
        ? "0 rows"
        : `${firstVisibleRow}-${lastVisibleRow} of ${totalRows}`;
    }
    if (previousButton) {
      previousButton.disabled = currentPage <= 1;
    }
    if (nextButton) {
      nextButton.disabled = currentPage >= totalPages;
    }
  };

  const initTablePagination = (root = document) => {
    root.querySelectorAll("[data-table-pagination]").forEach((pagination) => {
      if (pagination.dataset.initialized === "true") return;
      pagination.dataset.initialized = "true";

      const card = pagination.closest(".card");
      if (!card) return;

      const pageSizeSelect = pagination.querySelector("[data-table-page-size]");
      const previousButton = pagination.querySelector("[data-table-page-prev]");
      const nextButton = pagination.querySelector("[data-table-page-next]");

      pageSizeSelect?.addEventListener("change", () => renderTablePage(card, 1));
      previousButton?.addEventListener("click", () => renderTablePage(card, getCurrentPage(card) - 1));
      nextButton?.addEventListener("click", () => renderTablePage(card, getCurrentPage(card) + 1));
      renderTablePage(card, 1);
    });
  };

  const initTableSortControls = (root = document) => {
    root.querySelectorAll("[data-table-sort-controls]").forEach((controls) => {
      if (controls.dataset.initialized === "true") return;
      controls.dataset.initialized = "true";

      const card = controls.closest(".card");
      const columnSelect = controls.querySelector("[data-table-sort-column]");
      const directionSelect = controls.querySelector("[data-table-sort-direction]");
      if (!card || !columnSelect || !directionSelect) return;

      columnSelect.addEventListener("change", () => {
        directionSelect.value = isStrategyOption(selectedSortOption(columnSelect)) ? "asc" : "desc";
        updateOrderLabels(columnSelect, directionSelect);
        sortTableCard(card);
      });
      directionSelect.addEventListener("change", () => sortTableCard(card));
      updateOrderLabels(columnSelect, directionSelect);
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
        initTablePagination(slot);
        initTableSortControls(slot);
      }
      if (slot.dataset.componentKind === "nav") {
        await window.DashboardCharts?.initNavChart?.(slot);
      }
      if (slot.dataset.componentKind === "treemap") {
        await window.DashboardCharts?.initTreemap?.(slot);
      }
      if (slot.dataset.componentKind === "portfolio-history") {
        await window.DashboardCharts?.initPortfolioHistoryChart?.(slot);
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
    initTablePagination();
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
