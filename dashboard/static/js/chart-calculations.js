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

  window.ChartCalculations = window.ChartCalculations || {};
  Object.assign(window.ChartCalculations, {
    rangeDayMap,
    getThemeColor,
    formatCurrency,
    formatPercent,
    computeAverage,
    computeMaxDrawdown,
    computeMaxUpside,
    computeReturn,
    computeDailyReturns,
    computeVolatility,
    computeHitRatio,
    computeCurrentDrawdown,
    computeVisibleTickLabels,
    filterPointsByRange,
  });
})();
