(function () {
  const getThemeColor = (name, fallback) => (
    getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
  );

  const parsePoints = (node) => {
    try {
      return JSON.parse(node.dataset.points || "[]").map((point) => ({
        ...point,
        date: new Date(`${point.raw_date}T00:00:00`),
        correlation: Number(point.correlation),
        gini: Number(point.gini),
      })).filter((point) => (
        Number.isFinite(point.date.getTime())
        && Number.isFinite(point.correlation)
        && Number.isFinite(point.gini)
      ));
    } catch {
      return [];
    }
  };

  const formatDate = (date) => new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);

  const renderPortfolioHistoryChart = (container, points) => {
    const d3 = window.d3;
    if (!d3) return;

    container.innerHTML = "";
    if (!points.length) return;

    const rect = container.getBoundingClientRect();
    const width = Math.max(rect.width, 320);
    const height = Math.max(rect.height, 280);
    const margin = { top: 24, right: 68, bottom: 42, left: 58 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;

    const lineA = getThemeColor("--nav-positive", "#f3ede2");
    const lineB = getThemeColor("--nav-negative", "#f5b041");
    const gridColor = getThemeColor("--grid-line", "rgba(255,255,255,0.14)");
    const labelColor = getThemeColor("--label-strong", "#dad1c7");
    const tooltipBg = getThemeColor("--chart-tooltip-bg", "rgba(246,238,226,0.96)");
    const tooltipText = getThemeColor("--chart-tooltip-text", "#191513");

    const dateExtent = d3.extent(points, (point) => point.date);
    if (dateExtent[0].getTime() === dateExtent[1].getTime()) {
      dateExtent[0] = d3.timeDay.offset(dateExtent[0], -1);
      dateExtent[1] = d3.timeDay.offset(dateExtent[1], 1);
    }

    const maxY = Math.max(
      1,
      d3.max(points, (point) => Math.max(Math.abs(point.correlation), Math.abs(point.gini * 100))) || 1,
    );

    const x = d3.scaleTime().domain(dateExtent).range([0, innerWidth]);
    const y = d3.scaleLinear().domain([Math.min(0, -maxY * 0.08), maxY * 1.08]).range([innerHeight, 0]);

    const svg = d3.select(container)
      .append("svg")
      .attr("class", "portfolio-history-svg")
      .attr("viewBox", `0 0 ${width} ${height}`)
      .attr("preserveAspectRatio", "none");

    const plot = svg.append("g")
      .attr("transform", `translate(${margin.left},${margin.top})`);

    plot.append("g")
      .attr("class", "portfolio-history-grid")
      .call(d3.axisLeft(y).ticks(4).tickSize(-innerWidth).tickFormat(""));

    const curve = points.length > 1 ? d3.curveMonotoneX : d3.curveLinear;
    const giniLine = d3.line()
      .x((point) => x(point.date))
      .y((point) => y(point.gini * 100))
      .curve(curve);

    plot.append("line")
      .attr("class", "portfolio-history-zero-line")
      .attr("x1", 0)
      .attr("x2", innerWidth)
      .attr("y1", y(0))
      .attr("y2", y(0));

    plot.selectAll(".portfolio-history-lollipop-stem")
      .data(points)
      .enter()
      .append("line")
      .attr("class", "portfolio-history-lollipop-stem")
      .attr("x1", (point) => x(point.date))
      .attr("x2", (point) => x(point.date))
      .attr("y1", y(0))
      .attr("y2", (point) => y(point.correlation))
      .attr("stroke", lineA);

    plot.append("path")
      .datum(points)
      .attr("class", "portfolio-history-line")
      .attr("d", giniLine)
      .attr("stroke", lineB);

    const dots = [
      ...points.map((point) => ({ ...point, series: "Correlation", value: point.correlation, color: lineA })),
      ...points.map((point) => ({ ...point, series: "Gini", value: point.gini * 100, color: lineB })),
    ];

    const tooltip = d3.select(container)
      .append("div")
      .attr("class", "chart-tooltip portfolio-history-tooltip")
      .style("background", tooltipBg)
      .style("color", tooltipText);

    plot.selectAll(".portfolio-history-dot")
      .data(dots)
      .enter()
      .append("circle")
      .attr("class", "portfolio-history-dot")
      .attr("cx", (point) => x(point.date))
      .attr("cy", (point) => y(point.value))
      .attr("r", 4)
      .attr("fill", (point) => point.color)
      .on("mouseenter", function (event, point) {
        d3.select(this).attr("r", 6);
        tooltip
          .style("opacity", "1")
          .html(`${point.series}<br>${formatDate(point.date)}<br>${point.value.toFixed(2)}%`);
      })
      .on("mousemove", (event) => {
        const bounds = container.getBoundingClientRect();
        tooltip
          .style("left", `${event.clientX - bounds.left + 12}px`)
          .style("top", `${event.clientY - bounds.top - 28}px`);
      })
      .on("mouseleave", function () {
        d3.select(this).attr("r", 4);
        tooltip.style("opacity", "0");
      });

    plot.append("g")
      .attr("class", "portfolio-history-axis")
      .attr("transform", `translate(0,${innerHeight})`)
      .call(d3.axisBottom(x).ticks(Math.min(points.length, 5)).tickFormat(d3.timeFormat("%b %d")));

    plot.append("g")
      .attr("class", "portfolio-history-axis")
      .call(d3.axisLeft(y).ticks(4).tickFormat((value) => `${value}%`));

    const legend = svg.append("g")
      .attr("class", "portfolio-history-legend")
      .attr("transform", `translate(${margin.left},${height - 16})`);

    [
      ["Correlation", lineA, "lollipop"],
      ["Gini x100", lineB, "line"],
    ].forEach(([label, color, shape], index) => {
      const item = legend.append("g").attr("transform", `translate(${index * 132},0)`);
      if (shape === "lollipop") {
        item.append("line").attr("x1", 11).attr("x2", 11).attr("y1", -8).attr("y2", 2).attr("stroke", color).attr("stroke-width", 1.4);
        item.append("circle").attr("cx", 11).attr("cy", -8).attr("r", 4).attr("fill", color);
      } else {
        item.append("line").attr("x1", 0).attr("x2", 22).attr("y1", 0).attr("y2", 0).attr("stroke", color).attr("stroke-width", 2.4);
      }
      item.append("text").attr("x", 30).attr("y", 4).attr("fill", labelColor).text(label);
    });

    plot.selectAll(".portfolio-history-grid line").attr("stroke", gridColor);
  };

  const initPortfolioHistoryChart = async (root = document) => {
    const chart = root.querySelector("#portfolio-history-chart");
    if (!chart) return;
    renderPortfolioHistoryChart(chart, parsePoints(chart));
  };

  window.DashboardCharts = window.DashboardCharts || {};
  window.DashboardCharts.initPortfolioHistoryChart = initPortfolioHistoryChart;
})();
