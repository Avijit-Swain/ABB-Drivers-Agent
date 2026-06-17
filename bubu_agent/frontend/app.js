const { useEffect, useMemo, useRef, useState } = React;

const ABB_LOGO = "/assets/abb-logo.svg";

const suggestions = [
  ["chart", "Why did ELSP orders decline in the last 6 months?"],
  ["scale", "Which drivers are selected for ELSP?"],
  ["compare", "Compare ELSP and ELSB performance"],
  ["forecast", "Give me the bear, base, and bull forecast for ELSP"],
  ["scenario", "Simulate ELSP if Data Center growth is 30"],
];

function formatConvTime(iso) {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  const ist = { timeZone: "Asia/Kolkata" };
  const todayIST = new Date().toLocaleDateString("en-CA", ist);
  const dIST = d.toLocaleDateString("en-CA", ist);
  if (dIST === todayIST) {
    return d.toLocaleTimeString("en-IN", { ...ist, hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString("en-IN", { ...ist, day: "2-digit", month: "short", year: "numeric" });
}

const showcaseMessage = {
  role: "assistant",
  visibleText: "",
  plotUrls: [],
  closingText: "",
  showcase: true,
};

function h(type, props, ...children) {
  return React.createElement(type, props, ...children);
}

function friendlyStatus(message = "") {
  const lower = message.toLowerCase();
  if (lower.includes("received user message")) return "Understanding request";
  if (lower.includes("started processing")) return "Planning next step";
  if (lower.includes("selected tool")) {
    if (lower.includes("structured_data_tool") || lower.includes("plot_tool")) return "Generating visual";
    return "Selecting the right tool";
  }
  if (lower.includes("tools returned")) {
    if (lower.includes("structured_data_tool") || lower.includes("plot_tool")) return "Generating visual";
    return "Reading tool results";
  }
  if (lower.includes("tool result trace")) return "Reviewing retrieved data";
  if (lower.includes("summarization")) return "Summarizing results";
  if (lower.includes("prepared final answer") || lower.includes("routing to summarization")) return "Preparing response";
  if (lower.includes("completed request")) return "Finishing up";
  return "Working on it";
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatMusd(value) {
  const numeric = Number(value || 0);
  if (Math.abs(numeric) >= 1000) {
    return `$${(numeric / 1000).toFixed(2)}B`;
  }
  return `$${numeric.toFixed(0)}M`;
}

function formatPct(value) {
  const numeric = Number(value || 0);
  const sign = numeric > 0 ? "+" : "";
  return `${sign}${numeric.toFixed(1)}%`;
}

function formatImpact(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "N/A";
  return formatPct(Number(value));
}

function monthName(monthNumber) {
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return names[(Number(monthNumber) || 1) - 1] || "";
}

function Icon({ name }) {
  const common = { viewBox: "0 0 24 24", "aria-hidden": "true" };
  const paths = {
    chart: [
      h("path", { d: "M4 19V5" }),
      h("path", { d: "M4 19h16" }),
      h("path", { d: "m7 15 3-4 3 2 4-6" }),
    ],
    scale: [
      h("path", { d: "M12 4v16" }),
      h("path", { d: "M5 7h14" }),
      h("path", { d: "m6 7-3 6h6L6 7Z" }),
      h("path", { d: "m18 7-3 6h6l-3-6Z" }),
    ],
    compare: [
      h("path", { d: "M7 7h11" }),
      h("path", { d: "m15 4 3 3-3 3" }),
      h("path", { d: "M17 17H6" }),
      h("path", { d: "m9 14-3 3 3 3" }),
    ],
    forecast: [
      h("path", { d: "M4 18h16" }),
      h("path", { d: "M6 15V9" }),
      h("path", { d: "M11 15V6" }),
      h("path", { d: "M16 15v-4" }),
      h("path", { d: "m17 7 3-3" }),
    ],
    scenario: [
      h("path", { d: "M8 3h8" }),
      h("path", { d: "M10 3v5l-4 8a4 4 0 0 0 3.6 5.8h4.8A4 4 0 0 0 18 16l-4-8V3" }),
      h("path", { d: "M8 14h8" }),
    ],
  };
  return h("svg", common, ...(paths[name] || paths.chart));
}

function RedSparkline({ points = null, tone = "negative" } = {}) {
  const strokeColor = tone === "positive" ? "#13a06f" : "#ff000f";
  const generatedPoints = useMemo(() => {
    if (!points || points.length < 2) {
      return "4,30 14,24 24,29 34,18 44,26 54,20 64,30 74,25 84,36 94,34 104,41 116,39 122,46";
    }
    const width = 118;
    const height = 40;
    const min = Math.min(...points);
    const max = Math.max(...points);
    const range = max - min || 1;
    return points.map((value, index) => {
      const x = 4 + (index * width) / (points.length - 1);
      const y = 10 + height - ((value - min) / range) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
  }, [points]);
  const lastPair = generatedPoints.split(" ").at(-1)?.split(",").map(Number) || [122, 46];

  return h(
    "svg",
    { viewBox: "0 0 126 58", className: `sparkline red-sparkline ${tone}-sparkline`, preserveAspectRatio: "none" },
    h("polyline", {
      points: generatedPoints,
      fill: "none",
      stroke: strokeColor,
      strokeWidth: "2.6",
      strokeLinecap: "round",
      strokeLinejoin: "round",
    }),
    h("circle", { cx: String(lastPair[0]), cy: String(lastPair[1]), r: "3.6", fill: strokeColor })
  );
}

function TrendChart({ data = [] } = {}) {
  const chart = useMemo(() => {
    const rows = data.filter((row) => row.growth_pct !== null && row.growth_pct !== undefined);
    if (rows.length < 2) {
      return {
        points: "28,46 42,45 56,45 70,44 84,45 98,46 112,44 126,43 140,44 154,43 168,44 182,45",
        firstLabel: "Jan",
        midLabel: "Jul",
        lastLabel: "Dec",
        lastX: 182,
        lastY: 45,
        topLabel: "10%",
        midValueLabel: "0%",
        bottomLabel: "-10%",
      };
    }
    const values = rows.map((row) => Number(row.growth_pct));
    const rawMin = Math.min(...values);
    const rawMax = Math.max(...values);
    const rawRange = rawMax - rawMin;
    const padding = Math.max(rawRange * 0.28, 0.5);
    let min = rawMin - padding;
    let max = rawMax + padding;
    if (rawRange === 0) {
      min = rawMin - 1;
      max = rawMax + 1;
    }
    const midValue = (min + max) / 2;
    const range = max - min;
    const xStart = 28;
    const xEnd = 182;
    const yTop = 13;
    const yBottom = 78;
    const points = values.map((value, index) => {
      const x = xStart + (index * (xEnd - xStart)) / (values.length - 1);
      const clampedValue = Math.max(min, Math.min(max, value));
      const y = yBottom - ((clampedValue - min) / range) * (yBottom - yTop);
      return { x, y, value, row: rows[index] };
    });
    return {
      points: points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" "),
      firstLabel: monthName(rows[0].month_num),
      midLabel: monthName(rows[Math.min(6, rows.length - 1)].month_num),
      lastLabel: monthName(rows[rows.length - 1].month_num),
      lastX: points[points.length - 1].x,
      lastY: points[points.length - 1].y,
      topLabel: `${max.toFixed(1)}%`,
      midValueLabel: `${midValue.toFixed(1)}%`,
      bottomLabel: `${min.toFixed(1)}%`,
    };
  }, [data]);

  return h(
    "svg",
    { viewBox: "0 0 190 92", className: "trend-chart", preserveAspectRatio: "none" },
    h("line", { x1: "24", y1: "13", x2: "24", y2: "78", stroke: "#d8dee9", strokeWidth: "1" }),
    h("line", { x1: "24", y1: "45.5", x2: "184", y2: "45.5", stroke: "#d8dee9", strokeWidth: "1" }),
    h("line", { x1: "24", y1: "13", x2: "184", y2: "13", stroke: "#edf1f7", strokeWidth: "1" }),
    h("line", { x1: "24", y1: "78", x2: "184", y2: "78", stroke: "#edf1f7", strokeWidth: "1" }),
    h("text", { x: "5", y: "16", fill: "#7b8496", fontSize: "7", fontWeight: "650" }, chart.topLabel),
    h("text", { x: "5", y: "48", fill: "#7b8496", fontSize: "7", fontWeight: "650" }, chart.midValueLabel),
    h("text", { x: "5", y: "81", fill: "#7b8496", fontSize: "7", fontWeight: "650" }, chart.bottomLabel),
    h("polyline", {
      points: chart.points,
      fill: "none",
      stroke: "#5a7fd9",
      strokeWidth: "2.8",
      strokeLinecap: "round",
      strokeLinejoin: "round",
    }),
    h("circle", { cx: String(chart.lastX), cy: String(chart.lastY), r: "4", fill: "#2463eb" }),
    h("text", { x: "26", y: "89", fill: "#7b8496", fontSize: "7", fontWeight: "650" }, chart.firstLabel),
    h("text", { x: "100", y: "89", fill: "#7b8496", fontSize: "7", fontWeight: "650", textAnchor: "middle" }, chart.midLabel),
    h("text", { x: "181", y: "89", fill: "#7b8496", fontSize: "7", fontWeight: "650", textAnchor: "end" }, chart.lastLabel)
  );
}

function KpiCard({ icon, iconClass = "", cardClass = "", title, subtitle, metric, metricClass = "", detail, pill, link, chart, children }) {
  return h(
    "section",
    { className: `kpi-card ${cardClass}` },
    h(
      "div",
      { className: "kpi-head" },
      h("span", { className: `kpi-icon ${iconClass}` }, icon),
      h("div", null, h("div", { className: "kpi-title" }, title), h("div", { className: "muted" }, subtitle))
    ),
    children || chart || h("div", { className: `kpi-metric ${metricClass}` }, metric),
    detail && h("div", { className: "kpi-detail" }, detail),
    pill && h("div", { className: "kpi-pill" }, pill),
    link && h("button", { className: "kpi-link" }, link)
  );
}

function OrdersGrowthTile({ division, onDivisionChange, tileData, tileError }) {
  const tone = tileData?.direction === "positive" ? "positive" : tileData?.direction === "negative" ? "negative" : "neutral";
  const points = (tileData?.data || []).map((row) => Number(row.orders_received_net_musd || 0));
  const trendArrow = tone === "positive" ? "↑" : "↓";
  const metricText = tileData ? formatPct(tileData.growth_pct) : tileError ? "N/A" : "Loading";
  const pillText = tileData ? `${trendArrow} ${tileData.message}` : tileError ? "Orders data unavailable" : "Fetching SQL data";

  return h(KpiCard, {
    icon: "▦",
    iconClass: tone === "positive" ? "green-icon" : tone === "negative" ? "red-icon" : "blue-icon",
    cardClass: `orders-growth-card ${tone}-card`,
    title: h(
      "span",
      { className: "kpi-title-row" },
      h("span", null, "Orders Growth"),
      h(
        "select",
        {
          className: `division-select ${tone}-select`,
          value: division,
          "aria-label": "Select division for orders growth",
          onChange: (e) => onDivisionChange(e.target.value),
        },
        h("option", { value: "ELSP" }, "ELSP"),
        h("option", { value: "ELSB" }, "ELSB"),
        h("option", { value: "ELDS" }, "ELDS")
      )
    ),
    subtitle: "vs previous 6 months",
    detail: h("span", null, "Total Orders ", h("strong", null, tileData ? formatMusd(tileData.recent_6_sum_musd) : "Loading")),
    pill: pillText,
  },
    h(
      "div",
      { className: "growth-body" },
      h("div", { className: `kpi-metric ${tone}` }, metricText),
      h(RedSparkline, { points, tone })
    )
  );
}

function BinIcon() {
  return h("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" },
    h("path", { d: "M3 6h18" }),
    h("path", { d: "M8 6V4h8v2" }),
    h("path", { d: "M19 6l-1 14H6L5 6" })
  );
}

function DownloadIcon() {
  return h("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" },
    h("path", { d: "M12 3v13" }),
    h("path", { d: "m7 11 5 5 5-5" }),
    h("path", { d: "M5 20h14" })
  );
}

function MailIcon() {
  return h("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" },
    h("rect", { x: "2", y: "4", width: "20", height: "16", rx: "2" }),
    h("path", { d: "m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" })
  );
}

function ConversationActions({ conversationId, conversationTitle, disabled }) {
  const [recipients, setRecipients] = useState([]);
  const [showPopover, setShowPopover] = useState(false);
  const [addingNew, setAddingNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [isDownloading, setIsDownloading] = useState(false);
  const [sendingTo, setSendingTo] = useState(null);
  const [sentTo, setSentTo] = useState(null);
  const [statusMsg, setStatusMsg] = useState("");
  const popoverRef = useRef(null);

  useEffect(() => {
    fetch("/api/recipients")
      .then((r) => r.json())
      .then((d) => setRecipients(d.recipients || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!showPopover) return;
    function handleOutside(e) {
      if (popoverRef.current && !popoverRef.current.contains(e.target)) {
        setShowPopover(false);
        setAddingNew(false);
        setNewName("");
        setNewEmail("");
      }
    }
    document.addEventListener("mousedown", handleOutside);
    return () => document.removeEventListener("mousedown", handleOutside);
  }, [showPopover]);

  async function downloadPdf() {
    if (disabled || isDownloading) return;
    setIsDownloading(true);
    setStatusMsg("");
    try {
      const res = await fetch(`/api/conversations/${conversationId}/pdf`);
      if (!res.ok) {
        let errorMessage = "PDF download failed.";
        try {
          const data = await res.json();
          errorMessage = data.error || errorMessage;
        } catch (_) {}
        throw new Error(errorMessage);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${conversationTitle || "conversation"}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setStatusMsg(`PDF failed: ${err.message}`);
      setShowPopover(true);
    } finally {
      setIsDownloading(false);
    }
  }

  async function addRecipient() {
    const name = newName.trim();
    const email = newEmail.trim();
    if (!name || !email) return;
    try {
      const res = await fetch("/api/recipients", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, email }),
      });
      const data = await res.json();
      setRecipients((prev) => [data, ...prev.filter((r) => r.id !== data.id)]);
      setNewName("");
      setNewEmail("");
      setAddingNew(false);
    } catch (_) {}
  }

  async function sendEmail(recipient) {
    if (disabled || sendingTo) return;
    setSendingTo(recipient.email);
    setSentTo(null);
    setStatusMsg("");
    try {
      const res = await fetch(`/api/conversations/${conversationId}/send-email`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: recipient.email, name: recipient.name || "" }),
      });
      const data = await res.json();
      if (res.ok && data.ok) {
        setSentTo(recipient.email);
        setStatusMsg(`Sent to ${recipient.email}`);
      } else {
        setStatusMsg(data.error ? `Email failed: ${data.error}` : "Email failed. Try again.");
      }
    } catch (err) {
      setStatusMsg(`Email failed: ${err.message}`);
    } finally {
      setSendingTo(null);
      setTimeout(() => {
        setStatusMsg("");
        setSentTo(null);
      }, 3500);
    }
  }

  async function deleteRecipient(id) {
    try {
      await fetch(`/api/recipients/${id}`, { method: "DELETE" });
      setRecipients((prev) => prev.filter((r) => r.id !== id));
    } catch (_) {}
  }

  return h(
    "div",
    { className: "conv-actions" },
    h(
      "button",
      { type: "button", className: "conv-action-btn", disabled: disabled || isDownloading, onClick: downloadPdf, title: disabled ? "Start or open a conversation with messages first" : "Download conversation as PDF" },
      h(DownloadIcon),
      h("span", null, isDownloading ? "..." : "PDF")
    ),
    h(
      "div",
      { className: "email-btn-wrap", ref: popoverRef },
      h(
        "button",
        {
          type: "button",
          className: `conv-action-btn${showPopover ? " active" : ""}`,
          disabled,
          onClick: () => setShowPopover((p) => !p),
          title: disabled ? "Start or open a conversation with messages first" : "Send conversation via email",
        },
        h(MailIcon),
        h("span", null, "Email")
      ),
      showPopover &&
        h(
          "div",
          { className: "email-popover" },
          h("div", { className: "popover-header" }, "Send via email"),
          statusMsg &&
            h(
              "div",
              { className: `popover-status ${statusMsg.startsWith("Sent") ? "success" : "error"}` },
              statusMsg
            ),
          recipients.length === 0 && !addingNew &&
            h("div", { className: "popover-empty" }, "No recipients yet. Add one below."),
          recipients.map((r) =>
            h(
              "div",
              { key: r.id, className: "recipient-row" },
              h(
                "span",
                { className: "recipient-detail" },
                h("strong", null, r.name || "Recipient"),
                h("span", { className: "recipient-email" }, r.email)
              ),
              h(
                "span",
                { className: "recipient-actions" },
                h(
                  "button",
                  {
                    type: "button",
                    className: `recipient-send-btn${sentTo === r.email ? " sent" : ""}`,
                    disabled: sendingTo === r.email,
                    onClick: () => sendEmail(r),
                  },
                  sendingTo === r.email
                    ? h("span", { className: "mini-spinner" })
                    : sentTo === r.email
                      ? "Sent"
                      : "Send"
                ),
                h(
                  "button",
                  {
                    type: "button",
                    className: "recipient-delete-btn",
                    title: "Delete recipient",
                    onClick: () => deleteRecipient(r.id),
                  },
                  h(BinIcon)
                )
              )
            )
          ),
          addingNew
            ? h(
                "div",
                { className: "add-recipient-form" },
                h("input", {
                  type: "text",
                  placeholder: "Recipient name",
                  value: newName,
                  autoFocus: true,
                  onChange: (e) => setNewName(e.target.value),
                  onKeyDown: (e) => {
                    if (e.key === "Enter") addRecipient();
                    if (e.key === "Escape") { setAddingNew(false); setNewName(""); setNewEmail(""); }
                  },
                }),
                h("input", {
                  type: "email",
                  placeholder: "email@example.com",
                  value: newEmail,
                  onChange: (e) => setNewEmail(e.target.value),
                  onKeyDown: (e) => {
                    if (e.key === "Enter") addRecipient();
                    if (e.key === "Escape") { setAddingNew(false); setNewName(""); setNewEmail(""); }
                  },
                }),
                h("button", { type: "button", className: "add-confirm-btn", onClick: addRecipient, disabled: !newName.trim() || !newEmail.trim() }, "Add")
              )
            : h(
                "button",
                { type: "button", className: "add-recipient-btn", onClick: () => setAddingNew(true) },
                h("span", { className: "add-plus" }, "+"),
                "Add recipient"
              )
        )
    )
  );
}

function Sidebar({ disabled, onSuggestion, onNewConversation, conversations, conversationId, onConversationClick, onDeleteConversation }) {
  return h(
    "aside",
    { className: "sidebar" },
    h(
      "div",
      { className: "brand-card" },
      h("img", { src: ABB_LOGO, alt: "ABB" }),
      h("div", { className: "brand-title" }, "Decision Insights Copilot")
    ),
    h(
      "section",
      { className: "side-card suggestions-card" },
      h("div", { className: "side-label" }, h("span", { className: "label-dot" }), "Suggested questions"),
      suggestions.map(([icon, text], index) =>
        h(
          "button",
          {
            key: text,
            className: `suggestion suggestion-${icon}`,
            disabled,
            onClick: () => onSuggestion(text),
          },
          h("span", { className: "suggestion-icon" }, h(Icon, { name: icon })),
          h("span", null, text)
        )
      )
    ),
    h(
      "section",
      { className: "side-card recent-card" },
      h("div", { className: "side-label" }, h("span", { className: "label-dot" }), "Recent conversations"),
      conversations.map((conv) =>
        h(
          "div",
          {
            className: `recent-row ${conv.id === conversationId ? "active-recent" : ""}`,
            key: conv.id,
            onClick: () => !disabled && onConversationClick(conv.id),
          },
          h("span", { className: "recent-title" }, conv.title || "Untitled"),
          h("span", { className: "conv-row-right" },
            h("time", null, formatConvTime(conv.updated_at)),
            h("button", {
              className: "delete-conv-btn",
              title: "Delete conversation",
              onClick: (e) => { e.stopPropagation(); onDeleteConversation(conv.id); },
            }, h(BinIcon))
          )
        )
      )
    ),
    h(
      "button",
      {
        type: "button",
        className: "new-conversation-button",
        disabled,
        onClick: onNewConversation,
      },
      h("span", null, "+"),
      "New Conversation"
    ),
    h(
      "section",
      { className: "profile-card" },
      h("div", { className: "avatar" }, "AM"),
      h("div", null, h("div", { className: "profile-name" }, "Anisha Mahanty"), h("div", { className: "muted" }, "Data Scientist"))
    )
  );
}

function ScenarioForecast({ scenarios = [] } = {}) {
  return h(
    "div",
    { className: "scenario-stack" },
    scenarios.map((scenario) =>
      h(
        "div",
        { key: scenario.name, className: `scenario-row scenario-${String(scenario.name || "").toLowerCase()}` },
        h("span", null, scenario.name),
        h("strong", null, formatImpact(scenario.value))
      )
    )
  );
}

function DashboardCards() {
  const [division, setDivision] = useState("ELSP");
  const [dashboardData, setDashboardData] = useState(null);
  const [dashboardError, setDashboardError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setDashboardData(null);
    setDashboardError("");
    fetch(`/api/dashboard/tiles?division=${encodeURIComponent(division)}`)
      .then((res) => {
        if (!res.ok) throw new Error("Unable to fetch dashboard data");
        return res.json();
      })
      .then((data) => {
        if (!cancelled) setDashboardData(data);
      })
      .catch((err) => {
        if (!cancelled) setDashboardError(err.message || "Unable to fetch dashboard data");
      });
    return () => {
      cancelled = true;
    };
  }, [division]);

  const negativeDriver = dashboardData?.drivers?.top_negative;
  const positiveDriver = dashboardData?.drivers?.top_positive;
  const negativeDriverValue = Number(negativeDriver?.impact_pct_current);
  const negativeDriverClass = negativeDriver && negativeDriverValue >= 0 ? "positive small" : "negative small";
  const positiveDriverValue = Number(positiveDriver?.impact_pct_current);
  const positiveDriverClass = positiveDriver && positiveDriverValue < 0 ? "negative small" : "positive small";
  const forecastScenarios = dashboardData?.forecast?.scenarios || [
    { name: "Bear", value: null },
    { name: "Base", value: null },
    { name: "Bull", value: null },
  ];

  return h(
    "div",
    { className: "kpi-grid" },
    h(OrdersGrowthTile, {
      division,
      onDivisionChange: setDivision,
      tileData: dashboardData?.orders_growth,
      tileError: dashboardError,
    }),
    h(KpiCard, {
      icon: "↗",
      iconClass: "blue-icon",
      title: "Orders Trend",
      subtitle: "Last 12 months",
      chart: h(React.Fragment, null, h(TrendChart, { data: dashboardData?.orders_trend?.data || [] })),
    }),
    h(KpiCard, {
      icon: "▼",
      iconClass: "purple-icon",
      cardClass: "compact-kpi",
      title: "Top Negative Driver",
      subtitle: "Impact %",
      metric: negativeDriver ? formatImpact(negativeDriver.impact_pct_current) : "Loading",
      metricClass: negativeDriver ? negativeDriverClass : "negative small",
      detail: h("strong", null, negativeDriver?.selected_driver_name || "Loading"),
    }),
    h(KpiCard, {
      icon: "▲",
      iconClass: "green-icon",
      cardClass: "compact-kpi",
      title: "Top Positive Driver",
      subtitle: "Impact %",
      metric: positiveDriver ? formatImpact(positiveDriver.impact_pct_current) : "Loading",
      metricClass: positiveDriver ? positiveDriverClass : "positive small",
      detail: h("strong", null, positiveDriver?.selected_driver_name || "Loading"),
    }),
    h(KpiCard, {
      icon: "⚠",
      iconClass: "orange-icon",
      cardClass: "compact-kpi",
      title: "Scenario",
      subtitle: "Point forecast",
      chart: h(ScenarioForecast, { scenarios: forecastScenarios }),
    })
  );
}

function colorizeNumbers(html) {
  // Only colorize inside text nodes (not HTML tag attributes)
  // Replace +X or +X% patterns with green, -X or -X% with red
  return html
    .replace(/(?<![=\w])(\+[\d,.]+%?)/g, '<span class="num-positive">$1</span>')
    .replace(/(?<![=\w-])(-[\d,.]+%?)(?!\w)/g, '<span class="num-negative">$1</span>');
}

function MarkdownContent({ text, className }) {
  const html = React.useMemo(() => {
    if (!text || !window.marked) return text || "";
    const raw = window.marked.parse(text);
    return colorizeNumbers(raw);
  }, [text]);
  return h("div", {
    className: className || "md-content",
    dangerouslySetInnerHTML: { __html: html },
  });
}

function StructuredResultCard({ visibleText, plotUrls }) {
  return h(
    "div",
    { className: "showcase-grid" },
    h(
      "div",
      { className: "plot-card" },
      plotUrls.map((url) =>
        h("img", { key: url, src: `${url}?t=${Date.now()}`, alt: "Generated plot" })
      )
    ),
    h(
      "div",
      { className: "takeaway-card" },
      h("div", { className: "card-title" }, "Key Takeaways"),
      h(MarkdownContent, { text: visibleText, className: "takeaway-para md-content" })
    )
  );
}

function Message({ message }) {
  const isUser = message.role === "user";
  const hasPlots = !message.showcase && message.plotUrls && message.plotUrls.length > 0;
  return h(
    "article",
    { className: `message ${isUser ? "user-message" : "assistant-message"}${message.showcase ? " showcase-article" : ""}` },
    h(
      "div",
      { className: "message-body" },
      h(
        "div",
        { className: `message-header ${isUser ? "user-message-header" : ""}` },
        h("span", { className: "message-avatar" }, h(ChatAvatarIcon, { type: isUser ? "user" : "assistant" })),
        h("span", { className: "message-author" }, isUser ? "You" : "DI Copilot"),
        isUser && message.visibleText && h("span", { className: "user-inline-question" }, message.visibleText)
      ),
      !isUser && message.statusText && h("div", { className: "live-status" }, h("span", null), message.statusText),
      !isUser && !hasPlots && message.visibleText &&
        h(MarkdownContent, { text: message.visibleText, className: "message-text md-content" }),
      message.showcase
        ? h(ShowcasePlot, null)
        : hasPlots
          ? h(StructuredResultCard, { visibleText: message.visibleText, plotUrls: message.plotUrls })
          : null,
      !hasPlots && message.closingText && h("div", { className: "closing-text" }, message.closingText)
    )
  );
}

function ChatAvatarIcon({ type }) {
  if (type === "user") {
    return h("span", { className: "avatar-initials" }, "AM");
  }

  return h("img", { className: "avatar-logo", src: ABB_LOGO, alt: "ABB" });
}

function Takeaway({ tone, icon, children }) {
  return h(
    "div",
    { className: `takeaway-row takeaway-${tone}` },
    h("span", null, icon),
    h("p", null, children)
  );
}

function ShowcasePlot() {
  return h(
    "div",
    { className: "landing-image-wrap" },
    h("img", { src: "/assets/landing.png", alt: "Decision Insights Copilot", className: "landing-image" })
  );
}

function WorkingMessage() {
  return h(
    "article",
    { className: "message assistant-message" },
    h(
      "div",
      { className: "message-body" },
      h(
        "div",
        { className: "message-header" },
        h("span", { className: "message-avatar" }, h(ChatAvatarIcon, { type: "assistant" })),
        h("span", { className: "message-author" }, "DI Copilot")
      ),
      h("div", { className: "working" }, h("span", null), h("strong", null, "Working on it..."))
    )
  );
}

function ChatInput({ disabled, onSend, actions }) {
  const [value, setValue] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [micError, setMicError] = useState("");
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);

  function submit(event) {
    event.preventDefault();
    const prompt = value.trim();
    if (!prompt || disabled) return;
    setValue("");
    onSend(prompt);
  }

  async function toggleMic() {
    if (isTranscribing) return;
    setMicError("");

    if (isRecording) {
      mediaRecorderRef.current?.stop();
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType });
        setIsRecording(false);
        setIsTranscribing(true);
        const reader = new FileReader();
        reader.onloadend = async () => {
          try {
            const base64 = reader.result.split(",")[1];
            const res = await fetch("/api/transcribe", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ audio: base64, mimeType: recorder.mimeType }),
            });
            const data = await res.json();
            if (data.text) {
              setValue((prev) => (prev ? `${prev} ${data.text}` : data.text));
            } else {
              setMicError("Transcription failed. Please try again.");
            }
          } catch (_) {
            setMicError("Transcription failed. Please try again.");
          } finally {
            setIsTranscribing(false);
          }
        };
        reader.readAsDataURL(blob);
      };

      mediaRecorderRef.current = recorder;
      recorder.start();
      setIsRecording(true);
    } catch (err) {
      if (err.name === "NotAllowedError" || err.name === "PermissionDeniedError") {
        setMicError("Microphone access denied. Please allow microphone access in your browser settings.");
      } else {
        setMicError("Could not access microphone.");
      }
    }
  }

  const micClass = `mic-button${isRecording ? " recording" : ""}${isTranscribing ? " transcribing" : ""}`;

  return h(
    "div",
    { className: "chat-input-outer" },
    micError && h("div", { className: "mic-error" }, micError),
    h(
      "form",
      { className: "chat-input-wrap", onSubmit: submit },
      h("textarea", {
        value,
        disabled,
        placeholder: "Ask anything about your financial data...",
        rows: 1,
        onChange: (event) => { setMicError(""); setValue(event.target.value); },
        onKeyDown: (event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit(event);
          }
        },
      }),
      actions && h("div", { className: "input-actions" }, actions),
      h(
        "div",
        { className: "chat-input-right" },
        h(
          "button",
          { type: "button", className: micClass, disabled: disabled || isTranscribing, "aria-label": isRecording ? "Stop recording" : "Voice input", onClick: toggleMic },
          isTranscribing
            ? h("span", { className: "mic-spinner" })
            : h(
                "svg",
                { viewBox: "0 0 24 24", "aria-hidden": "true" },
                h("path", { d: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z" }),
                h("path", { d: "M19 10v2a7 7 0 0 1-14 0v-2" }),
                h("path", { d: "M12 19v3" })
              )
        ),
        h("button", { type: "submit", className: "send-button", disabled: disabled || !value.trim(), "aria-label": "Send message" }, "➤")
      )
    )
  );
}

function App() {
  const [messages, setMessages] = useState([]);
  const [isWorking, setIsWorking] = useState(false);
  const [conversationId, setConversationId] = useState(null);
  const [conversations, setConversations] = useState([]);
  const chatRef = useRef(null);

  async function fetchConversations() {
    try {
      const res = await fetch("/api/conversations");
      const data = await res.json();
      setConversations(data.conversations || []);
    } catch (_) {}
  }

  useEffect(() => { fetchConversations(); }, []);

  async function loadConversation(id) {
    if (isWorking || id === conversationId) return;
    try {
      const res = await fetch(`/api/conversations/${id}/messages`);
      const data = await res.json();
      const restored = (data.messages || []).map((msg, i) => ({
        id: `${msg.role}-${i}-${id}`,
        role: msg.role,
        content: msg.content,
        visibleText: msg.visible_text,
        closingText: msg.closing_text,
        plotUrls: msg.plot_urls || [],
      }));
      setMessages(restored);
      setConversationId(id);
    } catch (_) {}
  }

  async function deleteConversation(id) {
    try {
      await fetch(`/api/conversations/${id}`, { method: "DELETE" });
      if (id === conversationId) {
        setMessages([]);
        setConversationId(null);
      }
      fetchConversations();
    } catch (_) {}
  }

  const renderedMessages = useMemo(() => (messages.length ? messages : [showcaseMessage]), [messages]);

  const activeTitle = useMemo(() => {
    const conv = conversations.find((c) => c.id === conversationId);
    return conv?.title || "Conversation";
  }, [conversations, conversationId]);
  const hasConversationMessages = Boolean(conversationId && messages.length > 0);

  useEffect(() => {
    if (chatRef.current) {
      chatRef.current.scrollTop = chatRef.current.scrollHeight;
    }
  }, [messages, isWorking]);

  async function sendMessage(prompt) {
    const userMessage = { id: `user-${Date.now()}`, role: "user", visibleText: prompt, content: prompt, plotUrls: [], closingText: "" };
    const assistantId = `assistant-${Date.now()}`;
    const assistantMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      visibleText: "",
      closingText: "",
      plotUrls: [],
      statusText: "Working on it",
    };
    setMessages((current) => [...current, userMessage, assistantMessage]);
    setIsWorking(true);

    function updateAssistant(patch) {
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                ...patch(message),
              }
            : message
        )
      );
    }

    try {
      const response = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: prompt, conversationId }),
      });
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(payload.error || "Request failed.");
      }
      if (!response.body) throw new Error("Streaming is not supported by this browser.");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let pendingFinal = null;

      function handleEvent(event) {
        if (event.type === "conversation") {
          setConversationId(event.id);
          fetchConversations();
          return;
        }

        if (event.type === "node") {
          updateAssistant(() => ({ statusText: friendlyStatus(event.message) }));
          return;
        }

        if (event.type === "delta") {
          updateAssistant((message) => ({
            visibleText: `${message.visibleText || ""}${event.text || ""}`,
            statusText: "",
          }));
          return;
        }

        if (event.type === "parsed_final") {
          pendingFinal = event;
          updateAssistant(() => ({ statusText: "Preparing response", visibleText: "" }));
          return;
        }

        if (event.type === "error") {
          updateAssistant(() => ({
            visibleText: event.message || "I ran into an error while processing that request.",
            closingText: "",
            plotUrls: [],
            statusText: "",
          }));
        }
      }

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.trim()) continue;
          handleEvent(JSON.parse(line));
        }
      }

      if (buffer.trim()) {
        handleEvent(JSON.parse(buffer));
      }

      if (pendingFinal) {
        const finalVisibleText = pendingFinal.visibleText || "";
        const finalPlotUrls = pendingFinal.plotUrls || [];
        const isStructured = finalPlotUrls.length > 0;

        if (isStructured) {
          const words = finalVisibleText.split(" ");
          updateAssistant(() => ({
            content: pendingFinal.content || "",
            visibleText: "",
            closingText: "",
            plotUrls: finalPlotUrls,
            statusText: "",
          }));
          for (let index = 0; index < words.length; index += 1) {
            const suffix = index === words.length - 1 ? "" : " ";
            updateAssistant((message) => ({
              visibleText: `${message.visibleText || ""}${words[index]}${suffix}`,
            }));
            await sleep(30);
          }
          updateAssistant(() => ({
            content: pendingFinal.content || "",
            visibleText: finalVisibleText,
            closingText: pendingFinal.closingText || "",
            plotUrls: finalPlotUrls,
            statusText: "",
          }));
        } else {
          const words = finalVisibleText.split(" ");
          updateAssistant(() => ({
            content: pendingFinal.content || "",
            visibleText: "",
            closingText: "",
            plotUrls: [],
            statusText: "",
          }));

          for (let index = 0; index < words.length; index += 1) {
            const suffix = index === words.length - 1 ? "" : " ";
            updateAssistant((message) => ({
              visibleText: `${message.visibleText || ""}${words[index]}${suffix}`,
            }));
            await sleep(30);
          }

          updateAssistant(() => ({
            content: pendingFinal.content || "",
            visibleText: finalVisibleText,
            closingText: pendingFinal.closingText || "",
            plotUrls: [],
            statusText: "",
          }));
        }
      }
    } catch (error) {
      updateAssistant(() => ({
          visibleText: `I ran into an error while processing that request. Details: ${error.message}`,
          closingText: "",
          plotUrls: [],
          statusText: "",
        }));
    } finally {
      setIsWorking(false);
    }
  }

  function startNewConversation() {
    if (isWorking) return;
    setMessages([]);
    setConversationId(null);
  }

  return h(
    "main",
    { className: "app-shell" },
    h(Sidebar, { disabled: isWorking, onSuggestion: sendMessage, onNewConversation: startNewConversation, conversations, conversationId, onConversationClick: loadConversation, onDeleteConversation: deleteConversation }),
    h(
      "section",
      { className: "workspace" },
      h(DashboardCards),
      h(
        "section",
        { className: "chat-panel", ref: chatRef },
        renderedMessages.map((message, index) => h(Message, { key: message.id || `${message.role}-${index}`, message }))
      ),
      h(ChatInput, {
        disabled: isWorking,
        onSend: sendMessage,
        actions: conversationId && h(ConversationActions, { conversationId, conversationTitle: activeTitle, disabled: !hasConversationMessages }),
      })
    )
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(h(App));
