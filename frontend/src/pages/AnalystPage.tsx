import { useState, useRef, useEffect, useCallback } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import {
  Send,
  Plus,
  Mic,
  BarChart3,
  FileText,
  FileDown,
  ChevronDown,
  ChevronLeft,
  Printer,
  Square,
  Bot,
} from "lucide-react";
import { Tooltip } from "react-tooltip";
import "react-tooltip/dist/react-tooltip.css";
import { Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType } from "docx";
import { saveAs } from "file-saver";
import { jsPDF } from "jspdf";
import html2canvas from "html2canvas";
import { useVoice } from "../hooks/useVoice";
import { useWebSocket } from "../hooks/useWebSocket";
import type { WsMessage } from "../hooks/useWebSocket";
import { sessionsApi, dataApi } from "../services/api";
import { useDataset } from "../context/DatasetContext";
import ChatMessage from "../components/ChatMessage";
import type { Message } from "../components/ChatMessage";
import VoiceButton from "../components/VoiceButton";
import "./AnalystPage.css";

const SESSION_STORAGE_KEY = "currentSessionId";

const SUGGESTIONS = [
  "Какая выручка за этот месяц?",
  "TOP N",
  "Сравни продажи по категориям",
  "Покажи динамику продаж по месяцам",
  "Найди слабые места в продажах",
  "Поиск аномалий",
];

const WELCOME_MESSAGE: Message = {
  id: "1",
  role: "assistant",
  text: "Привет! Я ваш AI-аналитик. Я могу отвечать на вопросы по данным о продажах, строить графики и находить аномалии. Спросите меня о чем-нибудь голосом или текстом.",
};

export default function AnalystPage() {
  const [messages, setMessages] = useState<Message[]>([WELCOME_MESSAGE]);
  const [inputValue, setInputValue] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [loadingStatus, setLoadingStatus] = useState("Анализирую данные...");
  const [streamingText, setStreamingText] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionTitle, setSessionTitle] = useState<string | null>(null);
  const [sessionDate, setSessionDate] = useState<string | null>(null);
  const [responseMode, setResponseMode] = useState<"text" | "both" | "voice">(
    "both",
  );
  const [showExportMenu, setShowExportMenu] = useState(false);
  const exportMenuRef = useRef<HTMLDivElement>(null);
  const exportCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [showCharts, setShowCharts] = useState(true);
  const [extendedResponse, setExtendedResponse] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const prevMessageCountRef = useRef(0);
  const sessionIdRef = useRef<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const currentLimitRef = useRef<number | null>(null);

  const { send, addHandler, removeHandler } = useWebSocket();
  const navigate = useNavigate();

  const { selectedDatasetId, setSelectedDatasetId } = useDataset();
  const [searchParams] = useSearchParams();
  const cameFromHistory = searchParams.has("session_id");

  // Auto-select first dataset if none selected
  useEffect(() => {
    if (!selectedDatasetId) {
      dataApi.getDatasets().then(datasets => {
        if (datasets.length > 0) {
          setSelectedDatasetId(datasets[0].id);
        }
      }).catch(() => {});
    }
  }, []);
  const urlSessionId = searchParams.get("session_id");
  const {
    isRecording,
    isProcessing,
    startRecording,
    stopRecording,
    playSynthesizedAudio,
    stopAudio,
    pauseAudio,
    audioState,
  } = useVoice();

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  const loadSessionHistory = useCallback(async (id: string) => {
    try {
      setIsLoading(true);
      const data = await sessionsApi.getSession(id);

      // data.messages contains both user and assistant turns

      const loadedMessages: Message[] = data.messages.map((msg: any, idx: number) => {
        let chartType = undefined;
        let chartValueKey = undefined;
        let chartData = undefined;
        let sql = undefined;
        let processingTime: number | undefined;
        let isCached: boolean | undefined;
        let cachedAt: string | undefined;
        let chartLimit: number | undefined;

        if (msg.data_summary) {
          try {
            const parsed = JSON.parse(msg.data_summary);
            if (parsed.data) chartData = parsed.data;
            if (parsed.chart_type) chartType = parsed.chart_type;
            if (parsed.chart_value_key) chartValueKey = parsed.chart_value_key;
            if (parsed.sql) sql = parsed.sql;
            if (parsed.processing_time) processingTime = parsed.processing_time;
            if (parsed.is_cached !== undefined) isCached = parsed.is_cached;
            if (parsed.cached_at) cachedAt = parsed.cached_at;
          } catch (e) {
            console.error("Failed to parse data_summary", e);
          }
        }

        // Extract chartLimit from the preceding user question
        if (msg.role === "assistant" && idx > 0) {
          const prevMsg = data.messages[idx - 1];
          if (prevMsg && prevMsg.role === "user") {
            chartLimit = extractLimit(prevMsg.content) || undefined;
          }
        }

        return {
          id: msg.id,
          role: msg.role as "user" | "assistant",
          text: msg.content,
          data: chartData,
          chartType: chartType,
          chartValueKey: chartValueKey,
          chartLimit: chartLimit,
          sql: sql,
          processingTime: processingTime,
          isCached: isCached,
          cachedAt: cachedAt,
          feedback: msg.feedback as "positive" | "negative" | null | undefined,
        };
      });

      if (loadedMessages.length === 0) {
        loadedMessages.push({
          id: "1",
          role: "assistant",
          text: WELCOME_MESSAGE.text,
        });
      }

      setMessages(loadedMessages);
      setSessionTitle(data.session.title);
      setSessionDate(data.session.updated_at);
    } catch (err) {
      console.error("Failed to load session", err);
      sessionIdRef.current = null;
      localStorage.removeItem(SESSION_STORAGE_KEY);
      setMessages([WELCOME_MESSAGE]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Track dataset changes — reset session when user switches database
  // Skip reset when navigating to a specific session via URL
  useEffect(() => {
    const storedDataset = sessionStorage.getItem("analystDatasetId");
    if (storedDataset === null) {
      sessionStorage.setItem("analystDatasetId", selectedDatasetId || "");
    } else if (storedDataset !== selectedDatasetId && !urlSessionId) {
      sessionStorage.setItem("analystDatasetId", selectedDatasetId || "");
      stopAudio();
      sessionIdRef.current = null;
      localStorage.removeItem(SESSION_STORAGE_KEY);
      setSessionTitle(null);
      setSessionDate(null);
      setMessages([WELCOME_MESSAGE]);
    }
  }, [selectedDatasetId, urlSessionId]);

  // Load session from URL or localStorage
  useEffect(() => {
    stopAudio();
    const idToLoad = urlSessionId || localStorage.getItem(SESSION_STORAGE_KEY);
    if (idToLoad && idToLoad !== sessionIdRef.current) {
      sessionIdRef.current = idToLoad;
      localStorage.setItem(SESSION_STORAGE_KEY, idToLoad);
      loadSessionHistory(idToLoad);
    } else if (!idToLoad && !sessionIdRef.current) {
      setSessionTitle(null);
      setSessionDate(null);
      setMessages([WELCOME_MESSAGE]);
    }
  }, [urlSessionId]);

  useEffect(() => {
    if (messages.length > prevMessageCountRef.current) {
      scrollToBottom();
    }
    prevMessageCountRef.current = messages.length;
  }, [messages, scrollToBottom]);

  const startNewSession = () => {
    stopAudio();
    sessionIdRef.current = null;
    localStorage.removeItem(SESSION_STORAGE_KEY);
    setSessionTitle(null);
    setSessionDate(null);
    setMessages([WELCOME_MESSAGE]);
    navigate("/analyst", { replace: true });
    setTimeout(() => textareaRef.current?.focus(), 100);
  };

  const handlePrint = () => {
    const container = document.querySelector(".chat__container");
    if (!container) return;

    const printWindow = window.open("", "_blank");
    if (!printWindow) return;

    const chatHTML = container.innerHTML;

    printWindow.document.write(`
      <!DOCTYPE html>
      <html>
      <head>
        <meta charset="utf-8">
        <title>Диалог с ассистентом${sessionTitle ? ": " + sessionTitle : ""}</title>
        <style>
          * { box-sizing: border-box; margin: 0; padding: 0; }
          body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            color: #1a1a2e;
            background: #fff;
            padding: 40px;
            line-height: 1.6;
          }
          h1 { font-size: 1.5rem; margin-bottom: 0.5rem; color: #1a1a2e; }
          .chat-header-print { margin-bottom: 2rem; padding-bottom: 1rem; border-bottom: 2px solid #e0e0e0; }
          .chat-header-print p { color: #666; font-size: 0.9rem; }
          .chat-message { display: flex; gap: 1rem; padding: 1rem 0; border-bottom: 1px solid #f0f0f0; }
          .chat-message--user { flex-direction: row-reverse; }
          .chat-message--user .chat-message__text { background: #f0f4ff; border-radius: 12px 12px 0 12px; }
          .chat-message--assistant .chat-message__text { background: #f8f9fa; border-radius: 12px 12px 12px 0; }
          .chat-message__avatar {
            width: 36px; height: 36px; border-radius: 8px; display: flex;
            align-items: center; justify-content: center; flex-shrink: 0;
            font-size: 1.2rem;
          }
          .chat-message--user .chat-message__avatar { background: #e3f2fd; }
          .chat-message--assistant .chat-message__avatar { background: #e8f5e9; }
          .chat-message__text { padding: 0.75rem 1rem; max-width: 75%; font-size: 0.95rem; }
          .chat-message__audio-controls, .chat-message__feedback { display: none; }
          .chat-message__meta { font-size: 0.75rem; color: #999; margin-top: 0.5rem; }
          .chat-message__chart { margin-top: 0.5rem; }
          .chat-message__chart img { max-width: 100%; border-radius: 8px; }
          @media print {
            body { padding: 20px; }
            .chat-message { break-inside: avoid; }
          }
        </style>
      </head>
      <body>
        <div class="chat-header-print">
          <h1>Диалог с AI-Аналитиком</h1>
          ${sessionTitle ? `<p>${sessionTitle}</p>` : ""}
          <p>${new Date().toLocaleString("ru-RU")}</p>
        </div>
        ${chatHTML}
        <script>
          setTimeout(() => { window.print(); window.close(); }, 500);
        <\/script>
      </body>
      </html>
    `);
    printWindow.document.close();
  };

  const handleExportDocx = async () => {
    if (messages.length <= 1) return;

    const doc = new Document({
      sections: [{
        properties: {},
        children: [
          // Title
          new Paragraph({
            children: [
              new TextRun({
                text: "Диалог с AI-Аналитиком",
                bold: true,
                size: 32,
              }),
            ],
            heading: HeadingLevel.HEADING_1,
            alignment: AlignmentType.CENTER,
          }),
          // Session title
          ...(sessionTitle ? [new Paragraph({
            children: [new TextRun({ text: sessionTitle, size: 24, color: "666666" })],
            alignment: AlignmentType.CENTER,
          })] : []),
          // Date
          new Paragraph({
            children: [new TextRun({ text: new Date().toLocaleString("ru-RU"), size: 20, color: "999999" })],
            alignment: AlignmentType.CENTER,
            spacing: { after: 400 },
          }),
          // Messages
          ...messages.filter(m => m.role !== "assistant" || m.text !== WELCOME_MESSAGE.text).flatMap((msg) => {
            const isUser = msg.role === "user";
            const paragraphs: Paragraph[] = [];

            // Role label
            paragraphs.push(new Paragraph({
              children: [
                new TextRun({
                  text: isUser ? "Пользователь:" : "Ассистент:",
                  bold: true,
                  size: 22,
                  color: isUser ? "1565C0" : "2E7D32",
                }),
              ],
              spacing: { before: 200, after: 100 },
            }));

            // Message text
            const lines = msg.text.split("\n");
            for (const line of lines) {
              if (line.trim()) {
                paragraphs.push(new Paragraph({
                  children: [new TextRun({ text: line, size: 20 })],
                  spacing: { after: 50 },
                }));
              }
            }

            // Separator
            paragraphs.push(new Paragraph({
              children: [],
              spacing: { after: 200 },
              border: { bottom: { style: "single", size: 1, color: "E0E0E0" } },
            }));

            return paragraphs;
          }),
        ],
      }],
    });

    const blob = await Packer.toBlob(doc);
    const fileName = sessionTitle
      ? `Диалог_${sessionTitle.slice(0, 30)}.docx`
      : `Диалог_${new Date().toISOString().slice(0, 10)}.docx`;
    saveAs(blob, fileName);
    setShowExportMenu(false);
  };

  const handleExportPdf = async () => {
    if (messages.length <= 1) return;

    // Build HTML content for rendering
    const filteredMessages = messages.filter(
      (m) => m.role !== "assistant" || m.text !== WELCOME_MESSAGE.text
    );

    const container = document.createElement("div");
    container.style.cssText = `
      position: absolute; left: -9999px; top: 0;
      width: 800px; padding: 40px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 14px; line-height: 1.6; color: #1a1a1a;
      background: #fff;
    `;

    let html = `<h1 style="text-align:center;font-size:24px;margin:0 0 8px">Диалог с AI-Аналитиком</h1>`;
    if (sessionTitle) {
      html += `<p style="text-align:center;color:#666;font-size:16px;margin:0 0 4px">${sessionTitle}</p>`;
    }
    html += `<p style="text-align:center;color:#999;font-size:12px;margin:0 0 24px">${new Date().toLocaleString("ru-RU")}</p>`;

    for (const msg of filteredMessages) {
      const isUser = msg.role === "user";
      const roleColor = isUser ? "#1565C0" : "#2E7D32";
      const roleName = isUser ? "Пользователь" : "Ассистент";
      const lines = msg.text.split("\n").filter(l => l.trim()).join("<br>");

      html += `
        <div style="margin-bottom:16px">
          <div style="font-weight:bold;color:${roleColor};margin-bottom:4px">${roleName}:</div>
          <div style="padding:8px 12px;background:#f8f9fa;border-radius:8px;white-space:pre-wrap">${lines}</div>
        </div>
      `;
    }

    container.innerHTML = html;
    document.body.appendChild(container);

    try {
      const canvas = await html2canvas(container, { scale: 2, useCORS: true });
      document.body.removeChild(container);

      const imgData = canvas.toDataURL("image/png");
      const doc = new jsPDF("p", "mm", "a4");
      const pageWidth = doc.internal.pageSize.getWidth();
      const pageHeight = doc.internal.pageSize.getHeight();
      const margin = 10;
      const imgWidth = pageWidth - margin * 2;
      const imgHeight = (canvas.height * imgWidth) / canvas.width;

      let heightLeft = imgHeight;
      let position = margin;

      doc.addImage(imgData, "PNG", margin, position, imgWidth, imgHeight);
      heightLeft -= pageHeight - margin * 2;

      while (heightLeft > 0) {
        position = -(pageHeight - margin * 2) + margin;
        doc.addPage();
        doc.addImage(imgData, "PNG", margin, position, imgWidth, imgHeight);
        heightLeft -= pageHeight - margin * 2;
      }

      const fileName = sessionTitle
        ? `Диалог_${sessionTitle.slice(0, 30)}.pdf`
        : `Диалог_${new Date().toISOString().slice(0, 10)}.pdf`;
      doc.save(fileName);
    } catch (e) {
      // Clean up on error
      if (document.body.contains(container)) {
        document.body.removeChild(container);
      }
      console.error("PDF export error:", e);
    }

    setShowExportMenu(false);
  };

  // Close export menu on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (exportMenuRef.current && !exportMenuRef.current.contains(event.target as Node)) {
        setShowExportMenu(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // WebSocket message handler
  const handleWsMessage = useCallback(
    (msg: WsMessage) => {
      switch (msg.type) {
        case "status":
          setLoadingStatus(msg.message);
          break;
        case "token":
          setStreamingText((prev) => prev + msg.text);
          setIsStreaming(true);
          break;
        case "sql_progress":
          setLoadingStatus(
            `Обработано ${msg.rows} из ${msg.total} строк...`
          );
          break;
        case "result": {
          const res = msg.data;
          const aiMsg: Message = {
            id: (Date.now() + 1).toString(),
            role: "assistant",
            text: res.answer,
            sql: res.sql,
            data: res.data,
            chartType: res.chart_type || undefined,
            chartValueKey: res.chart_value_key || undefined,
            chartLimit: currentLimitRef.current,
            processingTime: res.processing_time || undefined,
            responseMode: responseMode,
            isCached: res.is_cached,
            cachedAt: res.cached_at || undefined,
          };
          setMessages((prev) => [...prev, aiMsg]);
          setIsLoading(false);
          setIsStreaming(false);
          setStreamingText("");

          // Fetch session title
          const sid = sessionIdRef.current;
          if (sid) {
            sessionsApi
              .getSession(sid)
              .then((data) => {
                setSessionTitle(data.session.title);
                setSessionDate(data.session.updated_at);
              })
              .catch(() => {});
          }

          if (responseMode === "voice" || responseMode === "both") {
            playSynthesizedAudio(res.answer);
          }
          break;
        }
        case "error":
          setMessages((prev) => [
            ...prev,
            {
              id: (Date.now() + 1).toString(),
              role: "assistant",
              text: msg.message || "Произошла ошибка.",
              retryable: msg.retryable !== false,
            },
          ]);
          setIsLoading(false);
          setIsStreaming(false);
          setStreamingText("");
          break;
        case "cancelled":
          if (streamingText) {
            setMessages((prev) => [
              ...prev,
              {
                id: (Date.now() + 1).toString(),
                role: "assistant",
                text: streamingText + "\n\n[Запрос отменён]",
              },
            ]);
          }
          setIsLoading(false);
          setIsStreaming(false);
          setStreamingText("");
          break;
      }
    },
    [responseMode, streamingText]
  );

  // Extract number limit from question (e.g., "топ-5" → 5)
  const extractLimit = (text: string): number | null => {
    const match = text.match(/топ[-\s]*(\d+)/i);
    if (match) return parseInt(match[1], 10);
    const match2 = text.match(/(\d+)\s*(товар|запис|позиц|штук)/i);
    if (match2) return parseInt(match2[1], 10);
    return null;
  };

  // Register WS handler
  useEffect(() => {
    addHandler(handleWsMessage);
    return () => removeHandler(handleWsMessage);
  }, [addHandler, removeHandler, handleWsMessage]);

  const handleSend = async (text: string) => {
    if (!text.trim()) return;

    currentLimitRef.current = extractLimit(text);
    const userMsg: Message = { id: Date.now().toString(), role: "user", text };
    setMessages((prev) => [...prev, userMsg]);
    setInputValue("");
    setIsLoading(true);
    setIsStreaming(false);
    setStreamingText("");
    setLoadingStatus("Анализирую запрос...");

    let currentSessionId = sessionIdRef.current;

    if (!currentSessionId) {
      try {
        const newSession = await sessionsApi.createSession(
          text,
          selectedDatasetId || null,
        );
        currentSessionId = newSession.id;
        sessionIdRef.current = newSession.id;
        localStorage.setItem(SESSION_STORAGE_KEY, newSession.id);
      } catch {
        setMessages((prev) => [
          ...prev,
          {
            id: (Date.now() + 1).toString(),
            role: "assistant",
            text: "Ошибка при создании сессии.",
          },
        ]);
        setIsLoading(false);
        return;
      }
    }

    if (!sessionTitle && currentSessionId) {
      setSessionTitle(text.length > 80 ? text.substring(0, 80) + "..." : text);
    }

    const payload = {
      type: "ask",
      question: text,
      extended: extendedResponse,
      session_id: currentSessionId || undefined,
      dataset_id: selectedDatasetId || null,
    };

    // Try to send, retry up to 5 times with 500ms delay
    let sent = false;
    for (let i = 0; i < 5; i++) {
      sent = send(payload);
      if (sent) break;
      await new Promise((r) => setTimeout(r, 500));
    }

    if (!sent) {
      setMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          role: "assistant",
          text: "Нет соединения с сервером. Обновите страницу.",
        },
      ]);
      setIsLoading(false);
    }
  };

  const handleCancel = () => {
    send({ type: "cancel" });
  };

  const handleRetry = () => {
    // Find the last user message and resend it
    const lastUserMsg = [...messages].reverse().find((m) => m.role === "user");
    if (lastUserMsg) {
      // Remove the error message (last assistant message)
      setMessages((prev) => {
        const lastAssistantIdx = prev.length - 1;
        if (lastAssistantIdx >= 0 && prev[lastAssistantIdx].role === "assistant" && prev[lastAssistantIdx].retryable) {
          return prev.slice(0, lastAssistantIdx);
        }
        return prev;
      });
      handleSend(lastUserMsg.text);
    }
  };

  const handleVoiceStop = async () => {
    try {
      const text = await stopRecording();
      if (text) {
        handleSend(text);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleFeedback = async (
    messageId: string,
    feedback: "positive" | "negative" | null,
  ) => {
    try {
      const token = localStorage.getItem("token");
      await fetch("/api/v1/sessions/feedback", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ message_id: messageId, feedback }),
      });
      // Update local state
      setMessages((prev) =>
        prev.map((m) => (m.id === messageId ? { ...m, feedback } : m)),
      );
    } catch (e) {
      console.error("Feedback error:", e);
    }
  };

  const handleTextareaInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInputValue(e.target.value);
    e.target.style.height = "auto";
    e.target.style.height = `${Math.min(e.target.scrollHeight, 200)}px`;
  };

  return (
    <div className="chat">
      <header className="chat__header">
        <div className="chat__header-left">
          {cameFromHistory && (
            <button
              className="chat__back-btn"
              onClick={() => navigate("/sessions")}
              data-tooltip-id="chat-tooltip" data-tooltip-content="Назад к истории" data-tooltip-place="bottom"
            >
              <ChevronLeft size={20} />
            </button>
          )}
          <div>
            <h1 className="chat__header-title">Ассистент</h1>
            {sessionTitle && (
              <div className="chat__session-info">
                <span className="chat__session-title">{sessionTitle}</span>
                {sessionDate && (
                  <span className="chat__session-date">
                    {new Date(sessionDate).toLocaleString("ru-RU", {
                      day: "numeric",
                      month: "long",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
        <div className="chat__header-actions">
          {messages.some(m => m.role === "user") && (
            <>
              <button
                className="chat__setting-btn chat__setting-btn--lg"
                onClick={handlePrint}
                data-tooltip-id="chat-tooltip" data-tooltip-content="Печать диалога" data-tooltip-place="bottom"
              >
                <Printer size={16} />
                <span>Печать</span>
              </button>
              <div
                className="export-dropdown"
                ref={exportMenuRef}
                onMouseEnter={() => {
                  if (exportCloseTimer.current) clearTimeout(exportCloseTimer.current);
                  setShowExportMenu(true);
                }}
                onMouseLeave={() => {
                  exportCloseTimer.current = setTimeout(() => setShowExportMenu(false), 300);
                }}
              >
                <button
                  className="chat__setting-btn chat__setting-btn--lg"
                  data-tooltip-id="chat-tooltip" data-tooltip-content="Экспорт" data-tooltip-place="bottom"
                >
                  <FileDown size={16} />
                  <span>Экспорт</span>
                  <ChevronDown size={12} style={{ marginLeft: 2 }} />
                </button>
                {showExportMenu && (
                  <div className="export-dropdown__menu">
                    <button
                      className="export-dropdown__item"
                      onClick={handleExportDocx}
                    >
                      <FileText size={14} />
                      <span>DOCX</span>
                    </button>
                    <button
                      className="export-dropdown__item"
                      onClick={handleExportPdf}
                    >
                      <FileDown size={14} />
                      <span>PDF</span>
                    </button>
                  </div>
                )}
              </div>
            </>
          )}
          <button
            className="chat__setting-btn chat__setting-btn--active chat__setting-btn--lg"
            onClick={startNewSession}
            data-tooltip-id="chat-tooltip" data-tooltip-content="Новая сессия" data-tooltip-place="bottom"
          >
            <Plus size={16} />
            <span>Новая сессия</span>
          </button>
        </div>
      </header>

      <main className="chat__main">
        <div className="chat__container">
          {messages.map((msg) => (
            <ChatMessage
              key={msg.id}
              message={msg}
              onPlayAudio={playSynthesizedAudio}
              onStopAudio={stopAudio}
              onPauseAudio={pauseAudio}
              audioState={audioState}
              showCharts={showCharts}
              onFeedback={handleFeedback}
              onRetry={msg.retryable ? handleRetry : undefined}
            />
          ))}
          {/* Streaming text with cursor */}
          {isLoading && isStreaming && streamingText && (
            <div className="chat-message chat-message--assistant">
              <div className="chat-message__avatar">
                <Bot size={20} />
              </div>
              <div className="chat-message__content">
                <div className="chat-message__text">
                  {streamingText}
                  <span className="streaming-cursor">|</span>
                </div>
              </div>
            </div>
          )}

          {/* Loading dots with status */}
          {isLoading && !isStreaming && (
            <div className="chat-message chat-message--assistant">
              <div className="chat-message__avatar">
                <div className="typing-dots">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
              </div>
              <div className="chat-message__content">
                <div className="chat-message__text chat-message__text--loading">
                  <span className="loading-status">{loadingStatus}</span>
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </main>

      <footer className="chat__footer">
        <div className="chat__suggestions">
          {SUGGESTIONS.map((text, i) => (
            <button
              key={i}
              className="chat__suggestion-btn"
              onClick={() => handleSend(text)}
            >
              {text}
            </button>
          ))}
        </div>
        <div className="chat__settings-bar">
          <button
            className={`chat__setting-btn ${responseMode === "both" ? "chat__setting-btn--active" : ""}`}
            onClick={() =>
              setResponseMode(responseMode === "both" ? "text" : "both")
            }
            title={responseMode === "both" ? "Голос включён" : "Голос выключен"}
          >
            <Mic size={14} />
            <span>Голос</span>
          </button>
          <button
            className={`chat__setting-btn ${showCharts ? "chat__setting-btn--active" : ""}`}
            onClick={() => setShowCharts(!showCharts)}
            data-tooltip-id="chat-tooltip" data-tooltip-content="Графики" data-tooltip-place="top"
          >
            <BarChart3 size={14} />
            <span>Графики</span>
          </button>
          <button
            className={`chat__setting-btn ${extendedResponse ? "chat__setting-btn--active" : ""}`}
            onClick={() => setExtendedResponse(!extendedResponse)}
            data-tooltip-id="chat-tooltip" data-tooltip-content="Расширенный режим" data-tooltip-place="top"
          >
            <FileText size={14} />
            <span>Расширенный</span>
          </button>
        </div>
        <div className="chat__input-wrapper">
          <textarea
            ref={textareaRef}
            className="chat__textarea"
            placeholder="Спросите что-нибудь о продажах..."
            value={inputValue}
            onChange={handleTextareaInput}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend(inputValue);
                e.currentTarget.style.height = "auto";
              }
            }}
            rows={1}
          />
          <div className="chat__actions">
            <VoiceButton
              isRecording={isRecording}
              isProcessing={isProcessing}
              onStart={startRecording}
              onStop={handleVoiceStop}
            />
            {isLoading ? (
              <button
                className="chat__stop-btn"
                onClick={handleCancel}
                data-tooltip-id="chat-tooltip" data-tooltip-content="Остановить" data-tooltip-place="top"
              >
                <Square size={14} />
              </button>
            ) : (
              <button
                className="chat__send-btn"
                onClick={() => {
                  handleSend(inputValue);
                  setInputValue("");
                }}
                disabled={!inputValue.trim()}
              >
                <Send size={16} />
              </button>
            )}
          </div>
        </div>
      </footer>
      <Tooltip id="chat-tooltip" delayShow={300} />
    </div>
  );
}
