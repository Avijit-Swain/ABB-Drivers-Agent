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
  visibleText:
    "ELSP orders declined by 18.7% in the last 6 months (Jan - Jun 2024 vs Jul - Dec 2023). The decline was driven primarily by weaker Data Center demand, partially offset by pricing benefits.",
  plotUrls: ["/plots/showcase_contribution.png"],
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
  if (lower.includes("selected tool")) return "Selecting the right tool";
  if (lower.includes("tools returned")) return "Reading tool results";
  if (lower.includes("tool result trace")) return "Reviewing retrieved data";
  if (lower.includes("prepared final answer")) return "Preparing response";
  if (lower.includes("completed request")) return "Finishing up";
  if (lower.includes("plotter") || lower.includes("plot")) return "Generating visual";
  return "Working on it";
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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

function RedSparkline() {
  return h(
    "svg",
    { viewBox: "0 0 126 58", className: "sparkline red-sparkline", preserveAspectRatio: "none" },
    h("polyline", {
      points: "4,30 14,24 24,29 34,18 44,26 54,20 64,30 74,25 84,36 94,34 104,41 116,39 122,46",
      fill: "none",
      stroke: "#ff000f",
      strokeWidth: "2.6",
      strokeLinecap: "round",
      strokeLinejoin: "round",
    }),
    h("circle", { cx: "122", cy: "46", r: "3.6", fill: "#ff000f" })
  );
}

function TrendChart() {
  return h(
    "svg",
    { viewBox: "0 0 190 92", className: "trend-chart", preserveAspectRatio: "none" },
    h("line", { x1: "30", y1: "16", x2: "30", y2: "70", stroke: "#d8dee9", strokeWidth: "1" }),
    h("line", { x1: "30", y1: "70", x2: "178", y2: "70", stroke: "#d8dee9", strokeWidth: "1" }),
    h("line", { x1: "30", y1: "28", x2: "178", y2: "28", stroke: "#edf1f7", strokeWidth: "1" }),
    h("line", { x1: "30", y1: "49", x2: "178", y2: "49", stroke: "#edf1f7", strokeWidth: "1" }),
    h("text", { x: "5", y: "20", fill: "#7b8496", fontSize: "7", fontWeight: "650" }, "20%"),
    h("text", { x: "13", y: "52", fill: "#7b8496", fontSize: "7", fontWeight: "650" }, "0%"),
    h("text", { x: "4", y: "74", fill: "#7b8496", fontSize: "7", fontWeight: "650" }, "-20%"),
    h("polyline", {
      points: "33,43 42,25 52,32 61,24 71,42 80,39 90,43 100,31 109,54 118,39 128,61 137,50 147,66 156,58 166,73 176,75",
      fill: "none",
      stroke: "#5a7fd9",
      strokeWidth: "2.8",
      strokeLinecap: "round",
      strokeLinejoin: "round",
    }),
    h("circle", { cx: "176", cy: "75", r: "4", fill: "#2463eb" }),
    h("text", { x: "31", y: "87", fill: "#7b8496", fontSize: "7", fontWeight: "650" }, "Jul 23"),
    h("text", { x: "72", y: "87", fill: "#7b8496", fontSize: "7", fontWeight: "650" }, "Oct 23"),
    h("text", { x: "145", y: "87", fill: "#7b8496", fontSize: "7", fontWeight: "650" }, "Jun 24"),
    h("text", { x: "151", y: "62", fill: "#2463eb", fontSize: "8", fontWeight: "800" }, "-18.7%")
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

function Sidebar({ disabled, onSuggestion, onNewConversation, conversations, conversationId, onConversationClick }) {
  return h(
    "aside",
    { className: "sidebar" },
    h(
      "div",
      { className: "brand-card" },
      h("img", { src: ABB_LOGO, alt: "ABB" }),
      h("div", { className: "brand-title" }, "Driver Analysis Copilot")
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
          h("time", null, formatConvTime(conv.updated_at))
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

function DashboardCards() {
  return h(
    "div",
    { className: "kpi-grid" },
    h(KpiCard, {
      icon: "▦",
      iconClass: "red-icon",
      title: "Orders Growth (ELSP)",
      subtitle: "vs previous 6 months",
      detail: h("span", null, "Total Orders ", h("strong", null, "$2.42B")),
      pill: "↓ Declined vs previous period",
    },
      h(
        "div",
        { className: "growth-body" },
        h("div", { className: "kpi-metric negative" }, "-18.7%"),
        h(RedSparkline)
      )
    ),
    h(KpiCard, {
      icon: "↗",
      iconClass: "blue-icon",
      title: "Orders Trend",
      subtitle: "Last 12 months",
      chart: h(React.Fragment, null, h(TrendChart)),
    }),
    h(KpiCard, {
      icon: "▼",
      iconClass: "purple-icon",
      cardClass: "compact-kpi",
      title: "Top Negative Driver",
      subtitle: "Impact %",
      metric: "-7.2%",
      metricClass: "negative small",
      detail: h("strong", null, "Data Center Demand"),
    }),
    h(KpiCard, {
      icon: "▲",
      iconClass: "green-icon",
      cardClass: "compact-kpi",
      title: "Top Positive Driver",
      subtitle: "Impact %",
      metric: "+4.3%",
      metricClass: "positive small",
      detail: h("strong", null, "Pricing / Realization"),
    }),
    h(KpiCard, {
      icon: "⚠",
      iconClass: "orange-icon",
      cardClass: "compact-kpi",
      title: "Largest Anomaly",
      subtitle: "vs normal range",
      metric: "-7.2%",
      metricClass: "negative small",
      detail: h("strong", null, "Data Center Demand"),
    })
  );
}

function Message({ message }) {
  const isUser = message.role === "user";
  return h(
    "article",
    { className: `message ${isUser ? "user-message" : "assistant-message"}` },
    h(
      "div",
      { className: "message-body" },
      h(
        "div",
        { className: `message-header ${isUser ? "user-message-header" : ""}` },
        h("span", { className: "message-avatar" }, h(ChatAvatarIcon, { type: isUser ? "user" : "assistant" })),
        h("span", { className: "message-author" }, isUser ? "You" : "AIC Copilot"),
        isUser && message.visibleText && h("span", { className: "user-inline-question" }, message.visibleText)
      ),
      !isUser && message.statusText && h("div", { className: "live-status" }, h("span", null), message.statusText),
      !isUser &&
        message.visibleText &&
        h(
          "div",
          { className: "message-text" },
          message.visibleText.split("\n").map((line, index) => h("p", { key: `${line}-${index}` }, line))
        ),
      message.showcase
        ? h(ShowcasePlot, null)
        : message.plotUrls?.map((url) =>
            h("div", { className: "plot-card", key: url }, h("img", { src: `${url}?t=${Date.now()}`, alt: "Generated plot" }))
          ),
      message.closingText && h("div", { className: "closing-text" }, message.closingText)
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
    { className: "showcase-grid" },
    h(
      "div",
      { className: "plot-card showcase-plot" },
      h("div", { className: "card-title" }, "Driver Contribution ", h("span", null, "Jan - Jun 2024 vs Jul - Dec 2023")),
      h("img", { src: "/plots/showcase_contribution.png", alt: "Driver contribution waterfall" })
    ),
    h(
      "div",
      { className: "takeaway-card" },
      h("div", { className: "card-title" }, "Key Takeaways"),
      h(Takeaway, { tone: "negative", icon: "↓" }, "Data Center Demand decline was the largest contributor (-7.2%)."),
      h(Takeaway, { tone: "positive", icon: "↑" }, "Pricing gains (+4.3% overall) helped offset part of the decline."),
      h(Takeaway, { tone: "info", icon: "i" }, "Enterprise Demand showed improvement in the period.")
    )
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
        h("span", { className: "message-author" }, "AIC Copilot")
      ),
      h("div", { className: "working" }, h("span", null), h("strong", null, "Working on it..."))
    )
  );
}

function ChatInput({ disabled, onSend }) {
  const [value, setValue] = useState("");

  function submit(event) {
    event.preventDefault();
    const prompt = value.trim();
    if (!prompt || disabled) return;
    setValue("");
    onSend(prompt);
  }

  return h(
    "form",
    { className: "chat-input-wrap", onSubmit: submit },
    h("textarea", {
      value,
      disabled,
      placeholder: "Ask anything about your financial data...",
      rows: 1,
      onChange: (event) => setValue(event.target.value),
      onKeyDown: (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          submit(event);
        }
      },
    }),
    h(
      "button",
      { type: "button", className: "mic-button", disabled, "aria-label": "Voice input" },
      h(
        "svg",
        { viewBox: "0 0 24 24", "aria-hidden": "true" },
        h("path", { d: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z" }),
        h("path", { d: "M19 10v2a7 7 0 0 1-14 0v-2" }),
        h("path", { d: "M12 19v3" })
      )
    ),
    h("button", { type: "submit", className: "send-button", disabled: disabled || !value.trim(), "aria-label": "Send message" }, "➤")
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

  const renderedMessages = useMemo(() => (messages.length ? messages : [showcaseMessage]), [messages]);

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
    const history = messages.map((message) => ({
      role: message.role,
      content: message.content || [message.visibleText, message.closingText].filter(Boolean).join("\n"),
    }));

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
        body: JSON.stringify({ message: prompt, history, conversationId }),
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
        const finalVisibleText = pendingFinal.visibleText || pendingFinal.content || "";
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
          const text = `${words[index]}${suffix}`;
          updateAssistant((message) => ({
            visibleText: `${message.visibleText || ""}${text}`,
          }));
          await sleep(30);
        }

        updateAssistant(() => ({
          content: pendingFinal.content || "",
          visibleText: finalVisibleText,
          closingText: pendingFinal.closingText || "",
          plotUrls: pendingFinal.plotUrls || [],
          statusText: "",
        }));
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
    h(Sidebar, { disabled: isWorking, onSuggestion: sendMessage, onNewConversation: startNewConversation, conversations, conversationId, onConversationClick: loadConversation }),
    h(
      "section",
      { className: "workspace" },
      h(DashboardCards),
      h(
        "section",
        { className: "chat-panel", ref: chatRef },
        renderedMessages.map((message, index) => h(Message, { key: message.id || `${message.role}-${index}`, message }))
      ),
      h(ChatInput, { disabled: isWorking, onSend: sendMessage })
    )
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(h(App));
