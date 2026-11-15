import { useState, useRef, useEffect, useCallback } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import {
  ArrowUp,
  ArrowUpRight,
  BarChart3,
  ChevronDown,
  ChevronLeft,
  FileDown,
  FileText,
  Plus,
  Printer,
  Square,
  Volume2,
} from "lucide-react";
import { Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType } from "docx";
import { saveAs } from "file-saver";
import { jsPDF } from "jspdf";
import html2canvas from "html2canvas";
import { useVoice } from "../hooks/useVoice";
import { useWebSocket } from "../hooks/useWebSocket";
import type { WsMessage } from "../hooks/useWebSocket";
import { sessionsApi } from "../shared/api/sessions.api";
import { useDataset } from "../shared/providers/DatasetContext";
import { useCanOpen } from "../components/shell/useCanOpen";
import { Button, IconButton, Menu, Spinner, ToggleChip } from "../shared/ui";
import { formatDateTime } from "../shared/lib/format";
import { CURRENT_SESSION_KEY } from "../shared/lib/analystSession";
import { isSameBase } from "../shared/lib/datasetLabel";
import { ModelStatus } from "../components/shell/ModelStatus";
import ChatMessage from "../components/ChatMessage";
import type { Message } from "../components/ChatMessage";
import VoiceButton from "../components/VoiceButton";
import "./AnalystPage.css";

/** Текст сообщений и заголовок диалога попадают в HTML печати и PDF как текст, а не как разметка */
function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/**
 * Объявления CSS-переменных для окна печати. Графики раскрашены токенами (var(--chart-1) и т.п.),
 * а tokens.css в это окно не попадает. Бумага белая, поэтому берём значения светлой темы,
 * а если правила стилей недоступны — текущие вычисленные значения.
 */
function printTokenDeclarations(html: string): string {
  const names = Array.from(new Set(html.match(/--[a-z0-9-]+/gi) ?? []));
  if (names.length === 0) return "";
  const light = new Map<string, string>();
  const visit = (rules: CSSRuleList) => {
    for (const rule of Array.from(rules)) {
      if (rule instanceof CSSStyleRule) {
        if (rule.selectorText !== ':root[data-theme="light"]') continue;
        for (const name of names) {
          const value = rule.style.getPropertyValue(name).trim();
          if (value) light.set(name, value);
        }
      } else if (rule instanceof CSSImportRule) {
        if (rule.styleSheet) visit(rule.styleSheet.cssRules);
      } else if (rule instanceof CSSGroupingRule) {
        visit(rule.cssRules);
      }
    }
  };
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      visit(sheet.cssRules);
    } catch {
      /* правила таблицы стилей недоступны (чужой origin) */
    }
  }
  const current = getComputedStyle(document.documentElement);
  return names
    .map((name) => [name, light.get(name) ?? current.getPropertyValue(name).trim()])
    .filter(([, value]) => value)
    .map(([name, value]) => `${name}: ${value};`)
    .join(" ");
}

const SUGGESTIONS = [
  "Какая выручка за этот месяц?",
  "Топ-10 товаров по выручке",
  "Сравни продажи по категориям",
  "Покажи динамику продаж по месяцам",
  "Найди слабые места в продажах",
  "Найди аномалии в продажах",
];

export default function AnalystPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [loadingStatus, setLoadingStatus] = useState("Разбираю вопрос");
  const [streamingText, setStreamingTextState] = useState("");
  // Копия текста в ref обновляется сразу, а не после рендера: обработчик «cancelled» читал
  // streamingText из замыкания и терял последние токены (или весь частичный ответ)
  const streamingTextRef = useRef("");
  const setStreamingText = useCallback((next: string | ((prev: string) => string)) => {
    const value = typeof next === "function" ? next(streamingTextRef.current) : next;
    streamingTextRef.current = value;
    setStreamingTextState(value);
  }, []);
  // Вопрос уже ушёл на сервер (иначе «Стоп» отменяет его локально, см. handleCancel)
  const askSentRef = useRef(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionTitle, setSessionTitle] = useState<string | null>(null);
  const [sessionDate, setSessionDate] = useState<string | null>(null);
  // Зачитывать ли ответы вслух (текст показывается всегда)
  const [speakAnswers, setSpeakAnswers] = useState(true);
  const [showCharts, setShowCharts] = useState(true);
  const [extendedResponse, setExtendedResponse] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const prevMessageCountRef = useRef(0);
  const sessionIdRef = useRef<string | null>(null);
  // Растёт при каждой смене диалога: handleSend после await проверяет, что диалог тот же
  const dialogGenRef = useRef(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const currentLimitRef = useRef<number | null>(null);

  const { send, addHandler, removeHandler, connected, ensureConnected } = useWebSocket();
  const navigate = useNavigate();

  const { selectedDatasetId, groups } = useDataset();
  const prevDatasetIdRef = useRef(selectedDatasetId);
  // Диалог сбросили, пока ответ ещё шёл: сервер подтверждает отмену сообщением «cancelled»,
  // а до него все сообщения относятся к старому диалогу и игнорируются
  const discardUntilCancelledRef = useRef(false);
  const [searchParams] = useSearchParams();
  const canOpen = useCanOpen();
  // Без права на «Историю» кнопка «Назад» вела бы на защищённый маршрут и молча перебрасывала
  const cameFromHistory = searchParams.has("session_id") && canOpen("/sessions");

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
      // Пока шла загрузка, диалог сбросили или открыли другой — результат устарел
      if (sessionIdRef.current !== id) return;

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

      setMessages(loadedMessages);
      setSessionTitle(data.session.title);
      setSessionDate(data.session.updated_at);
    } catch (err) {
      if (sessionIdRef.current !== id) return;
      console.error("Failed to load session", err);
      sessionIdRef.current = null;
      localStorage.removeItem(CURRENT_SESSION_KEY);
      setMessages([]);
      // finally ниже уже не узнает этот диалог: ссылку на него только что сбросили
      setIsLoading(false);
    } finally {
      if (sessionIdRef.current === id) setIsLoading(false);
    }
  }, []);

  // Текущее значение isLoading для эффектов и обработчиков, которые не должны перезапускаться от него
  const isLoadingRef = useRef(isLoading);
  useEffect(() => {
    isLoadingRef.current = isLoading;
  }, [isLoading]);

  // Соединение оборвалось — сервер сам отменил запрос, подтверждения «cancelled» не будет.
  // Если ответ ещё шёл, ждать его больше нечего: снимаем загрузку и предлагаем повторить вопрос.
  // Только переход «было соединение → нет»: до первого подключения handleSend сам ждёт сокет
  const wasConnectedRef = useRef(connected);
  useEffect(() => {
    const dropped = wasConnectedRef.current && !connected;
    wasConnectedRef.current = connected;
    if (!connected) discardUntilCancelledRef.current = false;
    if (!dropped || !isLoadingRef.current) return;
    // handleSend, который ещё ждёт создания сессии, не должен отправить вопрос после переподключения:
    // пользователь уже видит ошибку и кнопку «Повторить»
    dialogGenRef.current++;
    setMessages((prev) => [
      ...prev,
      {
        id: (Date.now() + 1).toString(),
        role: "assistant",
        text: "Соединение с сервером прервалось, ответ не получен.",
        retryable: true,
      },
    ]);
    setIsLoading(false);
    setIsStreaming(false);
    setStreamingText("");
  }, [connected]);

  // Сброс диалога посреди ответа: отменяем запрос, иначе ответ старого диалога попадёт в новый
  const abandonPendingAnswer = () => {
    if (!isLoading) return;
    if (send({ type: "cancel" })) discardUntilCancelledRef.current = true;
    setIsLoading(false);
    setIsStreaming(false);
    setStreamingText("");
  };

  // Чистый лист: новый диалог или смена базы. Останавливаем озвучку и ответ, забываем текущий диалог
  const resetConversation = () => {
    stopAudio();
    abandonPendingAnswer();
    dialogGenRef.current++;
    sessionIdRef.current = null;
    localStorage.removeItem(CURRENT_SESSION_KEY);
    setSessionTitle(null);
    setSessionDate(null);
    setMessages([]);
  };

  // Track dataset changes — reset session when user switches database
  // Skip reset when navigating to a specific session via URL
  useEffect(() => {
    const prevDatasetId = prevDatasetIdRef.current;
    prevDatasetIdRef.current = selectedDatasetId;
    // Базу переключили, пока страница открыта (переключатель в сайдбаре), — сбрасываем и диалог,
    // открытый по ссылке. Автовыбор провайдера вместо пустого или удалённого выбора сюда не попадает.
    // Другая таблица той же многотабличной базы — не смена базы: диалог остаётся
    const switchedWhileOpen =
      prevDatasetId !== null &&
      !isSameBase(groups, prevDatasetId, selectedDatasetId) &&
      groups.some((g) => g.tables.some((t) => t.id === prevDatasetId));
    const storedDataset = sessionStorage.getItem("analystDatasetId");
    if (storedDataset === null && !switchedWhileOpen) {
      sessionStorage.setItem("analystDatasetId", selectedDatasetId || "");
    } else if (switchedWhileOpen || (!isSameBase(groups, storedDataset, selectedDatasetId) && !urlSessionId)) {
      sessionStorage.setItem("analystDatasetId", selectedDatasetId || "");
      resetConversation();
      if (urlSessionId) navigate("/analyst", { replace: true });
    } else if (storedDataset !== selectedDatasetId) {
      // Другая таблица той же базы: запоминаем её, чтобы следующее сравнение шло от актуальной
      sessionStorage.setItem("analystDatasetId", selectedDatasetId || "");
    }
  }, [selectedDatasetId, urlSessionId]);

  // Load session from URL or localStorage
  useEffect(() => {
    stopAudio();
    const idToLoad = urlSessionId || localStorage.getItem(CURRENT_SESSION_KEY);
    if (idToLoad && idToLoad !== sessionIdRef.current) {
      dialogGenRef.current++;
      sessionIdRef.current = idToLoad;
      localStorage.setItem(CURRENT_SESSION_KEY, idToLoad);
      loadSessionHistory(idToLoad);
    } else if (!idToLoad && !sessionIdRef.current) {
      setSessionTitle(null);
      setSessionDate(null);
      setMessages([]);
    }
  }, [urlSessionId]);

  useEffect(() => {
    if (messages.length > prevMessageCountRef.current) {
      scrollToBottom();
    }
    prevMessageCountRef.current = messages.length;
  }, [messages, scrollToBottom]);

  const startNewSession = () => {
    resetConversation();
    navigate("/analyst", { replace: true });
    setTimeout(() => textareaRef.current?.focus(), 100);
  };

  const handlePrint = () => {
    const container = document.querySelector(".chat__column");
    if (!container) return;

    const printWindow = window.open("", "_blank");
    if (!printWindow) return;

    const chatHTML = container.innerHTML;
    const tokens = printTokenDeclarations(chatHTML);

    printWindow.document.write(`
      <!DOCTYPE html>
      <html>
      <head>
        <meta charset="utf-8">
        <title>Диалог с ассистентом${sessionTitle ? ": " + escapeHtml(sessionTitle) : ""}</title>
        <style>
          :root { ${tokens} }
          * { box-sizing: border-box; margin: 0; padding: 0; }
          body { font-family: "IBM Plex Sans", -apple-system, "Segoe UI", Roboto, sans-serif; color: #171717; background: #fff; padding: 40px; font-size: 13px; line-height: 1.6; }
          h1 { font-size: 20px; font-weight: 500; margin-bottom: 4px; }
          .chat-header-print { margin-bottom: 24px; padding-bottom: 12px; border-bottom: 1px solid #e5e5e5; }
          .chat-header-print p { color: #6e6e73; font-size: 12px; }
          .chat-message { display: flex; flex-direction: column; gap: 8px; padding: 12px 0; border-bottom: 1px solid #f0f0f0; break-inside: avoid; }
          .chat-message--user { align-items: flex-end; }
          .chat-message__bubble { background: #f4f4f5; border-radius: 12px 12px 4px 12px; padding: 8px 12px; max-width: 80%; white-space: pre-wrap; }
          .chat-message__text { white-space: pre-wrap; }
          .chat-message__footer button, .chat__intro, .chat__pending { display: none !important; }
          .chat-message__meta { font-size: 11px; color: #6e6e73; }
          .chat-sheet { border: 1px solid #e5e5e5; border-radius: 8px; overflow: hidden; }
          table { width: 100%; border-collapse: collapse; font-size: 12px; }
          th, td { padding: 4px 8px; border-bottom: 1px solid #eee; text-align: left; }
          th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
          svg { max-width: 100%; }
          @media print { body { padding: 20px; } }
        </style>
      </head>
      <body>
        <div class="chat-header-print">
          <h1>Диалог с ассистентом</h1>
          ${sessionTitle ? `<p>${escapeHtml(sessionTitle)}</p>` : ""}
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
    if (messages.length === 0) return;

    const doc = new Document({
      sections: [{
        properties: {},
        children: [
          // Title
          new Paragraph({
            children: [
              new TextRun({
                text: "Диалог с ассистентом",
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
          ...messages.flatMap((msg) => {
            const isUser = msg.role === "user";
            const paragraphs: Paragraph[] = [];

            // Role label
            paragraphs.push(new Paragraph({
              children: [
                new TextRun({
                  text: isUser ? "Пользователь:" : "Ассистент:",
                  bold: true,
                  size: 22,
                  color: isUser ? "525252" : "9A5B00",
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
  };

  const handleExportPdf = async () => {
    if (messages.length === 0) return;

    // Build HTML content for rendering
    const container = document.createElement("div");
    container.style.cssText = `
      position: absolute; left: -9999px; top: 0;
      width: 800px; padding: 40px;
      font-family: "IBM Plex Sans", -apple-system, "Segoe UI", Roboto, sans-serif;
      font-size: 14px; line-height: 1.6; color: #171717;
      background: #fff;
    `;

    let html = `<h1 style="text-align:center;font-size:24px;margin:0 0 8px">Диалог с ассистентом</h1>`;
    if (sessionTitle) {
      html += `<p style="text-align:center;color:#666;font-size:16px;margin:0 0 4px">${escapeHtml(sessionTitle)}</p>`;
    }
    html += `<p style="text-align:center;color:#999;font-size:12px;margin:0 0 24px">${new Date().toLocaleString("ru-RU")}</p>`;

    for (const msg of messages) {
      const isUser = msg.role === "user";
      const roleColor = isUser ? "#525252" : "#9A5B00";
      const roleName = isUser ? "Пользователь" : "Ассистент";
      const lines = msg.text.split("\n").filter(l => l.trim()).map(escapeHtml).join("<br>");

      html += `
        <div style="margin-bottom:16px">
          <div style="font-weight:bold;color:${roleColor};margin-bottom:4px">${roleName}:</div>
          <div style="padding:8px 12px;background:#f4f4f5;border-radius:8px;white-space:pre-wrap">${lines}</div>
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

      // Одна длинная картинка режется на страницы сдвигом вверх на высоту области печати;
      // поля закрашиваются, чтобы строки на стыке не печатались дважды
      const pageContent = pageHeight - margin * 2;
      const pageCount = Math.max(1, Math.ceil(imgHeight / pageContent));
      for (let page = 0; page < pageCount; page++) {
        if (page > 0) doc.addPage();
        doc.addImage(imgData, "PNG", margin, margin - page * pageContent, imgWidth, imgHeight);
        doc.setFillColor(255, 255, 255);
        doc.rect(0, 0, pageWidth, margin, "F");
        doc.rect(0, pageHeight - margin, pageWidth, margin, "F");
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

  };

  // WebSocket message handler
  const handleWsMessage = useCallback(
    (msg: WsMessage) => {
      if (discardUntilCancelledRef.current) {
        if (msg.type === "cancelled") discardUntilCancelledRef.current = false;
        return;
      }
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
            // id из БД: по нему сохраняется оценка 👍/👎
            id: res.message_id || (Date.now() + 1).toString(),
            role: "assistant",
            text: res.answer,
            sql: res.sql,
            data: res.data,
            chartType: res.chart_type || undefined,
            chartValueKey: res.chart_value_key || undefined,
            chartLimit: currentLimitRef.current,
            processingTime: res.processing_time || undefined,
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

          if (speakAnswers) {
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
        case "cancelled": {
          const partial = streamingTextRef.current;
          if (partial) {
            setMessages((prev) => [
              ...prev,
              {
                id: (Date.now() + 1).toString(),
                role: "assistant",
                // Не в квадратных скобках: cleanText вырезает их содержимое, и метка не была бы видна
                text: partial + "\n\n(запрос отменён)",
              },
            ]);
          }
          setIsLoading(false);
          setIsStreaming(false);
          setStreamingText("");
          break;
        }
      }
    },
    [speakAnswers, setStreamingText]
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

  const handleSend = async (text: string, options: { appendUserMessage?: boolean } = {}) => {
    if (!text.trim()) return;

    askSentRef.current = false;
    currentLimitRef.current = extractLimit(text);
    // При повторе вопрос уже есть в ленте — второй раз его не добавляем
    if (options.appendUserMessage !== false) {
      const userMsg: Message = { id: Date.now().toString(), role: "user", text };
      setMessages((prev) => [...prev, userMsg]);
    }
    setInputValue("");
    setIsLoading(true);
    setIsStreaming(false);
    setStreamingText("");
    setLoadingStatus("Разбираю вопрос");

    const gen = dialogGenRef.current;
    // Пока ждали сервер, пользователь начал новый диалог или сменил базу — вопрос туда не относится
    const dialogChanged = () => dialogGenRef.current !== gen;
    let currentSessionId = sessionIdRef.current;

    if (!currentSessionId) {
      try {
        const newSession = await sessionsApi.createSession(
          text,
          selectedDatasetId || null,
        );
        if (dialogChanged()) return;
        currentSessionId = newSession.id;
        sessionIdRef.current = newSession.id;
        localStorage.setItem(CURRENT_SESSION_KEY, newSession.id);
      } catch {
        if (dialogChanged()) return;
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

    // Сокет мог ещё не подключиться (старт откладывается на 3 с) — подключаемся сразу и ждём до ~5 с
    if (dialogChanged()) return;
    let sent = send(payload);
    if (!sent) ensureConnected();
    for (let i = 0; !sent && i < 10; i++) {
      await new Promise((r) => setTimeout(r, 500));
      if (dialogChanged()) return;
      sent = send(payload);
    }
    askSentRef.current = sent;

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
    if (!askSentRef.current) {
      // Вопрос ещё не ушёл (ждём создания сессии или подключения): отменяем локально. Иначе сервер
      // подтвердил бы отмену, интерфейс стал бы свободен, а handleSend всё равно отправил бы вопрос —
      // и чужой ответ позже появился бы посреди следующего диалога
      dialogGenRef.current++;
      setIsLoading(false);
      setIsStreaming(false);
      setStreamingText("");
      return;
    }
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
      handleSend(lastUserMsg.text, { appendUserMessage: false });
    }
  };

  const handleVoiceStop = async () => {
    try {
      const text = await stopRecording();
      if (!text) return;
      // Пока идёт ответ, новый вопрос не отправляем (как Enter): сервер отменил бы текущий ответ.
      // Распознанный текст оставляем в поле ввода — его можно отправить после ответа
      if (isLoadingRef.current) {
        setInputValue(text);
        return;
      }
      handleSend(text);
    } catch (e) {
      console.error(e);
    }
  };

  const handleFeedback = async (
    messageId: string,
    feedback: "positive" | "negative" | null,
  ) => {
    try {
      // apiClient знает адрес API (в Electron страница открыта по file://) и бросает на ошибке HTTP
      await sessionsApi.setFeedback(messageId, feedback);
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

  const hasConversation = messages.length > 0;

  return (
    <div className="chat">
      <header className="chat__header">
        <div className="chat__heading">
          {cameFromHistory && (
            <IconButton label="Назад к истории" icon={<ChevronLeft />} onClick={() => navigate("/sessions")} tooltipPlace="bottom" />
          )}
          <div className="chat__titles">
            <h1 className="chat__title">{sessionTitle || "Новый диалог"}</h1>
            {sessionDate && <span className="chat__date">{formatDateTime(sessionDate)}</span>}
          </div>
        </div>
        <div className="chat__header-actions">
          {messages.some((m) => m.role === "user") && (
            <>
              <IconButton label="Печать диалога" icon={<Printer />} onClick={handlePrint} tooltipPlace="bottom" />
              <Menu
                align="end"
                width={220}
                items={[
                  { key: "docx", label: "Документ Word", meta: ".docx", icon: <FileText />, onSelect: handleExportDocx },
                  { key: "pdf", label: "PDF", meta: ".pdf", icon: <FileDown />, onSelect: handleExportPdf },
                ]}
                trigger={(props) => (
                  <Button {...props} size="sm" variant="ghost" icon={<FileDown />} iconRight={<ChevronDown />}>
                    Экспорт
                  </Button>
                )}
              />
            </>
          )}
          <Button size="sm" variant="secondary" icon={<Plus />} onClick={startNewSession}>
            Новый диалог
          </Button>
        </div>
      </header>

      <main className="chat__main">
        <div className="chat__column">
          {!hasConversation && !isLoading && (
            <section className="chat__intro" aria-labelledby="chat-intro-title">
              <h2 id="chat-intro-title" className="chat__intro-title">
                Спросите о продажах
              </h2>
              <p className="chat__intro-text">
                Ассистент составит SQL-запрос к выбранной базе, посчитает показатели и построит график. Вопрос можно задать голосом.
              </p>
              <div className="chat__prompts">
                {SUGGESTIONS.map((text) => (
                  <button key={text} type="button" className="chat__prompt" onClick={() => handleSend(text)}>
                    <span>{text}</span>
                    <ArrowUpRight aria-hidden="true" />
                  </button>
                ))}
              </div>
            </section>
          )}

          {messages.map((msg, index) => (
            <ChatMessage
              key={msg.id}
              message={msg}
              onPlayAudio={playSynthesizedAudio}
              onStopAudio={stopAudio}
              onPauseAudio={pauseAudio}
              audioState={audioState}
              showCharts={showCharts}
              onFeedback={handleFeedback}
              // Повтор — только у последней ошибки и не во время ответа: иначе второй ask отменял
              // текущий вопрос, а его «cancelled» снимал индикатор загрузки у нового
              onRetry={msg.retryable && !isLoading && index === messages.length - 1 ? handleRetry : undefined}
            />
          ))}

          {isLoading && isStreaming && streamingText && (
            <div className="chat-message chat-message--assistant">
              <div className="chat-message__text">
                {streamingText}
                <span className="chat__cursor" aria-hidden="true" />
              </div>
            </div>
          )}

          {isLoading && !isStreaming && (
            <div className="chat__pending" role="status" aria-live="polite">
              <Spinner size={12} />
              <span>{loadingStatus}</span>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </main>

      <footer className="chat__footer">
        <div className="chat__composer">
          <label htmlFor="chat-input" className="visually-hidden">
            Вопрос ассистенту
          </label>
          <textarea
            id="chat-input"
            ref={textareaRef}
            className="chat__textarea"
            placeholder="Вопрос по данным"
            value={inputValue}
            onChange={handleTextareaInput}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (isLoading) return;
                handleSend(inputValue);
                e.currentTarget.style.height = "auto";
              }
            }}
            rows={1}
          />
          <div className="chat__composer-bar">
            <div className="chat__toggles">
              <ToggleChip
                pressed={speakAnswers}
                onChange={setSpeakAnswers}
                icon={<Volume2 />}
                title="Зачитывать ответы вслух"
              >
                Озвучка
              </ToggleChip>
              <ToggleChip pressed={showCharts} onChange={setShowCharts} icon={<BarChart3 />} title="Показывать графики в ответах">
                Графики
              </ToggleChip>
              <ToggleChip pressed={extendedResponse} onChange={setExtendedResponse} icon={<FileText />} title="Развёрнутый ответ с пояснениями">
                Подробно
              </ToggleChip>
            </div>
            <div className="chat__send">
              <VoiceButton isRecording={isRecording} isProcessing={isProcessing} onStart={startRecording} onStop={handleVoiceStop} />
              {isLoading ? (
                <IconButton label="Остановить ответ" icon={<Square />} variant="outline" onClick={handleCancel} />
              ) : (
                <IconButton
                  variant="primary"
                  label="Отправить вопрос"
                  icon={<ArrowUp />}
                  onClick={() => {
                    handleSend(inputValue);
                    setInputValue("");
                    if (textareaRef.current) textareaRef.current.style.height = "auto";
                  }}
                  disabled={!inputValue.trim()}
                />
              )}
            </div>
          </div>
        </div>
        <div className="chat__footnote">
          <ModelStatus />
          <span className="chat__hint">
            <kbd>Enter</kbd> отправить · <kbd>Shift</kbd>+<kbd>Enter</kbd> новая строка
          </span>
        </div>
      </footer>
    </div>
  );
}
