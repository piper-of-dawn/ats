(function () {
  const rangeDayMap = { "1D": 1, "5D": 5, "1M": 30, "6M": 183, "1Y": 365, "5Y": 1825 };

  const getThemeColor = (name, fallback) => {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  };

  const formatCurrency = (value) => new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 2,
  }).format(Number(value) || 0);

  const formatPercent = (value) => `${value >= 0 ? "+" : "-"}${(Math.abs(value) * 100).toFixed(2)}%`;

  const computeAverage = (points) => (
    points.reduce((sum, point) => sum + point.y, 0) / Math.max(points.length, 1)
  );

  const computeMaxDrawdown = (points) => {
    if (!points.length) return 0;
    let peak = points[0].y;
    let maxDrawdown = 0;
    points.forEach((point) => {
      peak = Math.max(peak, point.y);
      if (peak) {
        maxDrawdown = Math.min(maxDrawdown, (point.y / peak) - 1);
      }
    });
    return maxDrawdown;
  };

  const computeMaxUpside = (points) => {
    if (!points.length) return 0;
    let trough = points[0].y;
    let maxUpside = 0;
    points.forEach((point) => {
      trough = Math.min(trough, point.y);
      if (trough) {
        maxUpside = Math.max(maxUpside, (point.y / trough) - 1);
      }
    });
    return maxUpside;
  };

  const computeReturn = (points) => {
    if (points.length < 2) return 0;
    const first = points[0].y;
    const last = points[points.length - 1].y;
    return first ? (last / first) - 1 : 0;
  };

  const computeDailyReturns = (points) => (
    points.slice(1).map((point, index) => {
      const previous = points[index].y;
      return previous ? (point.y / previous) - 1 : 0;
    })
  );

  const computeVolatility = (points) => {
    const returns = computeDailyReturns(points);
    if (!returns.length) return 0;
    const mean = returns.reduce((sum, value) => sum + value, 0) / returns.length;
    const variance = returns.reduce((sum, value) => sum + ((value - mean) ** 2), 0) / returns.length;
    return Math.sqrt(variance) * Math.sqrt(252);
  };

  const computeHitRatio = (points) => {
    const returns = computeDailyReturns(points);
    if (!returns.length) return 0;
    return returns.filter((value) => value > 0).length / returns.length;
  };

  const computeCurrentDrawdown = (points) => {
    if (!points.length) return 0;
    const peak = points.reduce((max, point) => Math.max(max, point.y), points[0].y);
    const current = points[points.length - 1].y;
    return peak ? (current / peak) - 1 : 0;
  };

  const setMetricValue = (node, value) => {
    if (!node) return;
    node.textContent = value;
    node.classList.remove("positive", "negative");
    if (value === "--") return;
    const isPositive = String(value).trim().startsWith("+");
    const isNegative = String(value).trim().startsWith("-");
    if (isPositive) node.classList.add("positive");
    if (isNegative) node.classList.add("negative");
  };

  const updateMetricCards = (root, points, range) => {
    const returnNode = root.querySelector("#nav-metric-return");
    const returnDetailNode = root.querySelector("#nav-metric-return-detail");
    const volNode = root.querySelector("#nav-metric-vol");
    const hitNode = root.querySelector("#nav-metric-hit");
    const hitDetailNode = root.querySelector("#nav-metric-hit-detail");
    const ddNode = root.querySelector("#nav-metric-dd");

    setMetricValue(returnNode, formatPercent(computeReturn(points)));
    setMetricValue(volNode, formatPercent(computeVolatility(points)));
    setMetricValue(hitNode, formatPercent(computeHitRatio(points)));
    setMetricValue(ddNode, formatPercent(computeCurrentDrawdown(points)));

    if (returnDetailNode) {
      returnDetailNode.textContent = `${range} selected range`;
    }
    if (hitDetailNode) {
      hitDetailNode.textContent = `${Math.max(points.length - 1, 0)} observed periods`;
    }
  };

  const computeVisibleTickLabels = (points) => {
    const count = points.length;
    if (count <= 5) {
      return new Set(points.map((point) => point.label));
    }
    const indices = new Set([0, count - 1, Math.floor(count / 4), Math.floor(count / 2), Math.floor((count * 3) / 4)]);
    return new Set(Array.from(indices).map((index) => points[index].label));
  };

  const filterPointsByRange = (allPoints, range) => {
    if (!allPoints.length || range === "MAX") return allPoints;

    const latest = new Date(`${allPoints[allPoints.length - 1].raw_date}T00:00:00`);
    if (Number.isNaN(latest.getTime())) return allPoints;

    if (range === "YTD") {
      const start = `${latest.getFullYear()}-01-01`;
      return allPoints.filter((point) => point.raw_date >= start);
    }

    const days = rangeDayMap[range];
    if (!days) return allPoints;

    const cutoff = new Date(latest);
    cutoff.setDate(cutoff.getDate() - days);
    const cutoffText = cutoff.toISOString().slice(0, 10);
    const filtered = allPoints.filter((point) => point.raw_date >= cutoffText);
    return filtered.length >= 2 ? filtered : allPoints.slice(Math.max(0, allPoints.length - 2));
  };

  const createTooltip = (container) => {
    container.querySelectorAll(".chart-tooltip").forEach((tooltipNode) => tooltipNode.remove());
    const tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    container.appendChild(tooltip);
    return tooltip;
  };

  const updateStats = (root, points) => {
    const maxDrawdownNode = root.querySelector("#nav-max-drawdown");
    const maxUpsideNode = root.querySelector("#nav-max-upside");
    if (maxDrawdownNode) {
      maxDrawdownNode.textContent = formatPercent(computeMaxDrawdown(points));
    }
    if (maxUpsideNode) {
      maxUpsideNode.textContent = formatPercent(computeMaxUpside(points));
    }
  };

  const renderNavChart = (container, points, average, visibleTickLabels, tooltip) => {
    const d3 = window.d3;
    if (!d3) return;

    const positiveColor = getThemeColor("--nav-positive", "#f3ede2");
    const negativeColor = getThemeColor("--danger", "#b42318");
    const positiveAreaStrong = getThemeColor("--nav-fill-positive-strong", "rgba(255, 255, 255, 0.08)");
    const positiveAreaSoft = getThemeColor("--nav-fill-positive-soft", "rgba(255, 255, 255, 0.025)");
    const negativeAreaSoft = getThemeColor("--nav-fill-negative-soft", "rgba(180, 35, 24, 0.035)");
    const negativeAreaStrong = getThemeColor("--nav-fill-negative-strong", "rgba(180, 35, 24, 0.08)");

    container.innerHTML = "";
    if (!points.length) return;

    const rect = container.getBoundingClientRect();
    const width = Math.max(rect.width, 320);
    const height = Math.max(rect.height, 240);
    const margin = { top: 18, right: 18, bottom: 44, left: 56 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;

    const parsedPoints = points.map((point) => ({
      ...point,
      date: new Date(`${point.raw_date}T00:00:00`),
      y: Number(point.y) || 0,
    }));

    const yExtent = d3.extent(parsedPoints, (point) => point.y);
    const spread = Math.max(yExtent[1] - yExtent[0], 0);
    const yPadding = Math.max(spread * 0.025, spread === 0 ? Math.max(yExtent[1] * 0.008, 0.03) : 0.03);

    const x = d3.scaleTime()
      .domain(d3.extent(parsedPoints, (point) => point.date))
      .range([0, innerWidth]);

    const y = d3.scaleLinear()
      .domain([yExtent[0] - yPadding, yExtent[1] + yPadding])
      .range([innerHeight, 0]);

    const svg = d3.select(container)
      .append("svg")
      .attr("class", "nav-chart-svg")
      .attr("viewBox", `0 0 ${width} ${height}`)
      .attr("preserveAspectRatio", "none");

    const averageY = y(average);
    const defs = svg.append("defs");
    const plotClipId = `nav-plot-clip-${Math.random().toString(36).slice(2)}`;
    const positiveClipId = `nav-positive-clip-${Math.random().toString(36).slice(2)}`;
    const negativeClipId = `nav-negative-clip-${Math.random().toString(36).slice(2)}`;
    defs.append("clipPath")
      .attr("id", plotClipId)
      .append("rect")
      .attr("x", 0)
      .attr("y", 0)
      .attr("width", innerWidth)
      .attr("height", innerHeight);

    defs.append("clipPath")
      .attr("id", positiveClipId)
      .append("rect")
      .attr("x", 0)
      .attr("y", 0)
      .attr("width", innerWidth)
      .attr("height", Math.max(averageY, 0));

    defs.append("clipPath")
      .attr("id", negativeClipId)
      .append("rect")
      .attr("x", 0)
      .attr("y", Math.min(Math.max(averageY, 0), innerHeight))
      .attr("width", innerWidth)
      .attr("height", Math.max(innerHeight - averageY, 0));

    const plot = svg.append("g")
      .attr("transform", `translate(${margin.left},${margin.top})`);

    plot.append("line")
      .attr("class", "nav-average-line")
      .attr("x1", 0)
      .attr("x2", innerWidth)
      .attr("y1", averageY)
      .attr("y2", averageY);

    const averageLabel = plot.append("g")
      .attr("transform", `translate(${Math.max(innerWidth - 142, 8)},${Math.max(averageY - 14, 8)})`);
    averageLabel.append("rect")
      .attr("class", "nav-average-label-bg")
      .attr("width", 132)
      .attr("height", 28)
      .attr("rx", 14);
    averageLabel.append("text")
      .attr("class", "nav-average-label-text")
      .attr("x", 10)
      .attr("y", 17)
      .text(`Average: ${formatCurrency(average)}`);

    const areaGradientId = `nav-area-gradient-${Math.random().toString(36).slice(2)}`;
    const averageStop = Math.min(100, Math.max(0, (averageY / innerHeight) * 100));
    const areaGradient = defs.append("linearGradient")
      .attr("id", areaGradientId)
      .attr("gradientUnits", "userSpaceOnUse")
      .attr("x1", 0)
      .attr("x2", 0)
      .attr("y1", margin.top)
      .attr("y2", margin.top + innerHeight);
    areaGradient.append("stop").attr("offset", "0%").attr("stop-color", positiveAreaStrong);
    areaGradient.append("stop").attr("offset", `${averageStop}%`).attr("stop-color", positiveAreaSoft);
    areaGradient.append("stop").attr("offset", `${averageStop}%`).attr("stop-color", negativeAreaSoft);
    areaGradient.append("stop").attr("offset", "100%").attr("stop-color", negativeAreaStrong);

    const curve = d3.curveBasis;

    const line = d3.line()
      .x((point) => x(point.date))
      .y((point) => y(point.y))
      .curve(curve);

    const area = d3.area()
      .x((point) => x(point.date))
      .y0(averageY)
      .y1((point) => y(point.y))
      .curve(curve);

    plot.append("path")
      .datum(parsedPoints)
      .attr("d", area)
      .attr("fill", `url(#${areaGradientId})`)
      .attr("clip-path", `url(#${plotClipId})`);

    plot.append("path")
      .datum(parsedPoints)
      .attr("d", line)
      .attr("fill", "none")
      .attr("stroke", positiveColor)
      .attr("stroke-width", 2.25)
      .attr("stroke-linecap", "round")
      .attr("stroke-linejoin", "round")
      .attr("clip-path", `url(#${positiveClipId})`);

    plot.append("path")
      .datum(parsedPoints)
      .attr("d", line)
      .attr("fill", "none")
      .attr("stroke", negativeColor)
      .attr("stroke-width", 2.25)
      .attr("stroke-linecap", "round")
      .attr("stroke-linejoin", "round")
      .attr("clip-path", `url(#${negativeClipId})`);

    const tickDates = parsedPoints
      .filter((point) => visibleTickLabels.has(point.label))
      .map((point) => point.date);

    plot.append("g")
      .attr("class", "nav-axis nav-axis-x")
      .attr("transform", `translate(0,${innerHeight})`)
      .call(d3.axisBottom(x).tickValues(tickDates).tickFormat((value) => {
        const match = parsedPoints.find((point) => point.date.getTime() === value.getTime());
        return match ? d3.timeFormat("%b-%y")(match.date) : "";
      }))
      .call((axis) => axis.selectAll("text")
        .attr("dy", "1.4em"));

    const yTickValues = d3.range(4).map((index) => {
      const ratio = index / 3;
      return y.domain()[0] + ((y.domain()[1] - y.domain()[0]) * ratio);
    }).reverse();
    const yAxis = d3.axisLeft(y)
      .tickValues(yTickValues)
      .tickFormat((value) => Number(value).toFixed(2));

    plot.append("g")
      .attr("class", "nav-grid")
      .call(
        yAxis
          .tickSize(-innerWidth)
          .tickFormat(""),
      )
      .call((grid) => grid.select(".domain").remove());

    plot.append("g")
      .attr("class", "nav-axis nav-axis-y")
      .call(yAxis)
      .call((axis) => {
        axis.selectAll("text").remove();
        axis.selectAll("line").remove();
      });

    plot.append("g")
      .attr("class", "nav-y-labels")
      .selectAll("text")
      .data(yTickValues)
      .enter()
      .append("text")
      .attr("class", "nav-y-label")
      .attr("x", -10)
      .attr("y", (value) => y(value))
      .attr("dy", "0.32em")
      .attr("text-anchor", "end")
      .text((value) => Number(value).toFixed(2));

    const focus = plot.append("g").style("display", "none");
    focus.append("line")
      .attr("class", "nav-hover-line")
      .attr("y1", 0)
      .attr("y2", innerHeight);
    focus.append("circle")
      .attr("class", "nav-focus-dot")
      .attr("r", 4)
      .attr("fill", positiveColor);

    const bisectDate = d3.bisector((point) => point.date).center;

    plot.append("rect")
      .attr("width", innerWidth)
      .attr("height", innerHeight)
      .attr("fill", "transparent")
      .on("mouseenter", () => {
        focus.style("display", null);
        tooltip.classList.add("is-visible");
      })
      .on("mouseleave", () => {
        focus.style("display", "none");
        tooltip.classList.remove("is-visible");
      })
      .on("mousemove", function (event) {
        const [mouseX] = d3.pointer(event, this);
        const date = x.invert(mouseX);
        const index = bisectDate(parsedPoints, date);
        const point = parsedPoints[index];
        if (!point) return;

        const xPos = x(point.date);
        const yPos = y(point.y);
        focus.select(".nav-hover-line").attr("x1", xPos).attr("x2", xPos);
        focus.select(".nav-focus-dot")
          .attr("cx", xPos)
          .attr("cy", yPos)
          .attr("fill", point.y >= average ? positiveColor : negativeColor);

        tooltip.innerHTML = `<strong>${point.label}</strong><br>NAV: ${formatCurrency(point.y)}`;
        tooltip.style.left = `${margin.left + xPos}px`;
        tooltip.style.top = `${margin.top + yPos}px`;
      });
  };

  const initNavChart = async (root) => {
    const d3 = window.d3;
    const container = root.querySelector("#nav-chart");
    const rangeButton = root.querySelector("#nav-range-button");
    const rangeLabel = root.querySelector("#nav-range-label");
    const rangeMenu = root.querySelector("#nav-range-menu");

    if (!d3 || !container) return;

    const allPoints = JSON.parse(container.dataset.points || "[]").map((point) => ({
      ...point,
      y: Number(point.y) || 0,
    }));
    if (!allPoints.length) return;

    if (root.__navChartState) {
      root.__navChartState.draw();
      return;
    }

    const tooltip = createTooltip(container.parentElement);
    let currentRange = "YTD";

    const draw = () => {
      const points = filterPointsByRange(allPoints, currentRange);
      const average = computeAverage(points);
      const visibleTickLabels = computeVisibleTickLabels(points);
      updateStats(root, points);
      updateMetricCards(root, points, currentRange);
      renderNavChart(container, points, average, visibleTickLabels, tooltip);
      if (rangeLabel) {
        rangeLabel.textContent = currentRange;
      }
    };

    root.__navChartState = { draw };
    draw();

    if (rangeButton && rangeMenu) {
      rangeButton.onclick = () => {
        const expanded = rangeButton.getAttribute("aria-expanded") === "true";
        rangeButton.setAttribute("aria-expanded", String(!expanded));
        rangeMenu.hidden = expanded;
      };

      rangeMenu.querySelectorAll("button[data-range]").forEach((button) => {
        button.onclick = () => {
          currentRange = button.dataset.range || "YTD";
          draw();
          rangeButton.setAttribute("aria-expanded", "false");
          rangeMenu.hidden = true;
        };
      });

      document.addEventListener("click", (event) => {
        if (!rangeButton.contains(event.target) && !rangeMenu.contains(event.target)) {
          rangeButton.setAttribute("aria-expanded", "false");
          rangeMenu.hidden = true;
        }
      }, { passive: true });
    }

    if (window.ResizeObserver) {
      root.__navResizeObserver?.disconnect?.();
      root.__navResizeObserver = new ResizeObserver(() => draw());
      root.__navResizeObserver.observe(container);
    }
  };

  window.DashboardCharts = window.DashboardCharts || {};
  window.DashboardCharts.initNavChart = initNavChart;
})();
