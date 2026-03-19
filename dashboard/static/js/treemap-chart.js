(function () {
  const getThemePalette = () => {
    const rootStyle = getComputedStyle(document.documentElement);
    const primary = rootStyle.getPropertyValue("--value-strong").trim() || "#f3ede2";
    const secondary = rootStyle.getPropertyValue("--label-strong").trim() || "#dad1c7";
    const tertiary = rootStyle.getPropertyValue("--label-soft").trim() || "#a89d91";
    const accent = rootStyle.getPropertyValue("--accent").trim() || "#d9d1c5";
    return [primary, secondary, tertiary, accent, primary, secondary];
  };

  const getThemeValue = (name, fallback) => (
    getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
  );

  const formatCurrency = (value) => new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 2,
  }).format(Number(value) || 0);

  const createTooltip = (container) => {
    container.querySelectorAll(".chart-tooltip").forEach((tooltipNode) => tooltipNode.remove());
    const tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    container.appendChild(tooltip);
    return tooltip;
  };

  const initTreemap = async (root) => {
    const d3 = window.d3;
    const chartRoot = root.querySelector(".treemap-root");
    const container = root.querySelector(".treemap-canvas");
    if (!d3 || !chartRoot || !container) return;

    const items = JSON.parse(chartRoot.dataset.items || "[]")
      .map((item) => ({ ...item, value: Number(item.value) || 0 }))
      .filter((item) => item.value > 0)
      .sort((a, b) => b.value - a.value);
    if (!items.length) return;

    if (root.__treemapChartState) {
      root.__treemapChartState.draw();
      return;
    }

    const tooltip = createTooltip(container.parentElement);

    const draw = () => {
      const palette = getThemePalette();
      const inactiveTick = getThemeValue("--line", "rgba(255, 255, 255, 0.12)");
      const inactiveStroke = getThemeValue("--table-border-softer", "rgba(255, 255, 255, 0.08)");
      container.innerHTML = "";

      const rect = container.getBoundingClientRect();
      const width = Math.max(rect.width, 320);
      const height = Math.max(rect.height, 280);
      const margin = { top: 14, right: 14, bottom: 14, left: 14 };
      const chartWidth = Math.max(width - margin.left - margin.right, 180);
      const chartHeight = Math.max(height - margin.top - margin.bottom, 180);
      const radius = Math.min(chartWidth, chartHeight) / 2;
      const outerRadius = Math.max(radius - 8, 40);
      const innerRadius = outerRadius * 0.62;
      const totalValue = items.reduce((sum, item) => sum + item.value, 0);
      const displayedItems = items.slice(0, 6);
      const displayedValue = displayedItems.reduce((sum, item) => sum + item.value, 0);
      const remainderValue = Math.max(totalValue - displayedValue, 0);
      const ringItems = remainderValue > 0
        ? [...displayedItems, { label: "Other", value: remainderValue, isOther: true }]
        : displayedItems;

      const pie = d3.pie()
        .sort(null)
        .value((item) => item.value);

      const pieData = pie(ringItems.map((item, index) => ({
        ...item,
        index,
      })));

      const arc = d3.arc()
        .innerRadius(innerRadius)
        .outerRadius(outerRadius)
        .cornerRadius(6)
        .padAngle(0.024);

      const hoverArc = d3.arc()
        .innerRadius(innerRadius - 2)
        .outerRadius(outerRadius + 6)
        .cornerRadius(6)
        .padAngle(0.024);

      const svg = d3.select(container)
        .append("svg")
        .attr("class", "treemap-svg")
        .attr("viewBox", `0 0 ${width} ${height}`)
        .attr("preserveAspectRatio", "none");

      const chart = svg.append("g")
        .attr("transform", `translate(${margin.left + (chartWidth / 2)},${margin.top + (chartHeight / 2)})`);

      const slices = chart.selectAll(".donut-slice")
        .data(pieData)
        .enter()
        .append("path")
        .attr("class", "donut-slice")
        .attr("d", arc)
        .attr("fill", (slice) => slice.data.isOther ? inactiveTick : palette[slice.data.index % palette.length])
        .attr("stroke", (slice) => slice.data.isOther ? inactiveStroke : "transparent")
        .attr("stroke-width", 0)
        .style("cursor", "pointer");

      chart.append("text")
        .attr("class", "donut-center-label")
        .attr("text-anchor", "middle")
        .attr("y", -6)
        .text("TOTAL");

      chart.append("text")
        .attr("class", "donut-center-value")
        .attr("text-anchor", "middle")
        .attr("y", 18)
        .text(formatCurrency(totalValue));

      root.querySelectorAll(".treemap-legend-item").forEach((itemNode) => {
        const index = Number(itemNode.dataset.legendIndex || 0);
        itemNode.style.setProperty("--legend-swatch", palette[index % palette.length]);
      });

      const setGroupHover = (groupIndex, hovered) => {
        slices
          .filter((candidate) => candidate.data.index === groupIndex && !candidate.data.isOther)
          .transition()
          .duration(140)
          .attr("d", hovered ? hoverArc : arc);
      };

      const showTooltip = (event, item) => {
        const [x, y] = d3.pointer(event, container);
        const share = totalValue > 0 ? item.value / totalValue : 0;
        tooltip.innerHTML = `<strong>${item.label}</strong><br>${formatCurrency(item.value)} (${(share * 100).toFixed(2)}%)`;
        tooltip.style.left = `${x}px`;
        tooltip.style.top = `${y}px`;
        tooltip.classList.add("is-visible");
      };

      const hideTooltip = () => {
        tooltip.classList.remove("is-visible");
      };

      slices
        .on("mouseenter", function (event, slice) {
          if (slice.data.isOther) return;
          setGroupHover(slice.data.index, true);
          showTooltip(event, slice.data);
        })
        .on("mouseleave", function (event, slice) {
          if (slice.data.isOther) return;
          setGroupHover(slice.data.index, false);
          hideTooltip();
        })
        .on("mousemove", function (event, slice) {
          if (slice.data.isOther) return;
          showTooltip(event, slice.data);
        });

      root.querySelectorAll(".treemap-legend-item").forEach((itemNode) => {
        const index = Number(itemNode.dataset.legendIndex || 0);
        const item = displayedItems[index];
        itemNode.onmouseenter = (event) => {
          if (!item) return;
          setGroupHover(index, true);
          showTooltip(event, item);
        };
        itemNode.onmousemove = (event) => {
          if (!item) return;
          showTooltip(event, item);
        };
        itemNode.onmouseleave = () => {
          setGroupHover(index, false);
          hideTooltip();
        };
      });
    };

    root.__treemapChartState = { draw };
    draw();

    if (window.ResizeObserver) {
      root.__treemapResizeObserver?.disconnect?.();
      root.__treemapResizeObserver = new ResizeObserver(() => draw());
      root.__treemapResizeObserver.observe(container);
    }
  };

  window.DashboardCharts = window.DashboardCharts || {};
  window.DashboardCharts.initTreemap = initTreemap;
})();
