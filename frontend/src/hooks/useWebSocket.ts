import { useRef, useState, useCallback, useEffect } from "react";
import { handleUnauthorized } from "../shared/api/client";

export interface WsMessage {
  type: string;
  [key: string]: any;
}

type MessageHandler = (msg: WsMessage) => void;

/** http(s)://host:port → ws(s)://host:port; null — адрес некорректен */
function toWsBase(httpUrl: string): string | null {
  try {
    const url = new URL(httpUrl);
    return `${url.protocol === "https:" ? "wss" : "ws"}://${url.host}`;
  } catch {
    return null;
  }
}

/**
 * Адрес WebSocket ассистента:
 * 1. адрес от оболочки Electron (порт выбирается при запуске, см. shared/lib/backendUrl);
 * 2. VITE_WS_HOST — задаётся сборкой Electron (desktop/scripts/build-frontend.sh);
 * 3. VITE_API_URL — тот же хост, что и у REST API;
 * 4. хост страницы — в dev-режиме запрос идёт через прокси Vite (/ws → бэкенд из vite.config);
 * 5. 127.0.0.1:8000 — если страница открыта без хоста (file://).
 */
function resolveWsUrl(token: string): string {
  const path = `/ws/ai?token=${encodeURIComponent(token)}`;
  const shell = (window as unknown as { electronAPI?: { backendUrl?: string | null } }).electronAPI?.backendUrl;
  const fromShell = shell ? toWsBase(shell) : null;
  if (fromShell) return `${fromShell}${path}`;

  const wsHost = import.meta.env.VITE_WS_HOST as string | undefined;
  if (wsHost) return `ws://${wsHost}${path}`;

  const apiUrl = import.meta.env.VITE_API_URL as string | undefined;
  const fromApi = apiUrl ? toWsBase(apiUrl) : null;
  if (fromApi) return `${fromApi}${path}`;

  const { protocol, host } = window.location;
  if (host && (protocol === "http:" || protocol === "https:")) {
    return `${protocol === "https:" ? "wss" : "ws"}://${host}${path}`;
  }

  return `ws://127.0.0.1:8000${path}`;
}

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [status, setStatus] = useState<"connecting" | "connected" | "disconnected">("disconnected");
  const handlersRef = useRef<Set<MessageHandler>>(new Set());
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const attemptRef = useRef(0);
  const initTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    const token = localStorage.getItem("token");
    if (!token || !mountedRef.current) return;

    // Close existing connection
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }

    setStatus("connecting");
    const ws = new WebSocket(resolveWsUrl(token));

    ws.onopen = () => {
      if (!mountedRef.current) {
        ws.close();
        return;
      }
      attemptRef.current = 0;
      setConnected(true);
      setStatus("connected");
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
    };

    ws.onmessage = (event) => {
      if (!mountedRef.current) return;
      try {
        const msg: WsMessage = JSON.parse(event.data);
        handlersRef.current.forEach((handler) => handler(msg));
      } catch (e) {
        console.error("WS parse error:", e);
      }
    };

    ws.onclose = (event) => {
      if (!mountedRef.current) return;
      setConnected(false);
      setStatus("disconnected");
      wsRef.current = null;
      // 4001 — сервер отверг токен (истёк, ключ сменился, пользователь удалён): как 401 у REST
      if (event.code === 4001) {
        handleUnauthorized();
        return;
      }
      // Auto-reconnect unless intentionally closed
      if (event.code !== 4001 && event.code !== 1000 && mountedRef.current) {
        // Backoff: 2s first retry, 3s subsequent
        const delay = attemptRef.current === 0 ? 2000 : 3000;
        attemptRef.current++;
        reconnectTimeoutRef.current = setTimeout(() => connect(), delay);
      }
    };

    ws.onerror = () => {
      if (!mountedRef.current) return;
      setConnected(false);
    };

    wsRef.current = ws;
  }, []);

  const disconnect = useCallback(() => {
    mountedRef.current = false;
    attemptRef.current = 0;
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close(1000);
      wsRef.current = null;
    }
    setConnected(false);
    setStatus("disconnected");
  }, []);

  /**
   * Подключиться сейчас, не дожидаясь отложенного старта (3 с) или паузы перед переподключением, —
   * например, когда пользователь уже отправляет вопрос. Если сокет подключается или открыт — ничего не делает
   */
  const ensureConnected = useCallback(() => {
    if (!mountedRef.current) return;
    const state = wsRef.current?.readyState;
    if (state === WebSocket.CONNECTING || state === WebSocket.OPEN) return;
    if (initTimeoutRef.current) {
      clearTimeout(initTimeoutRef.current);
      initTimeoutRef.current = null;
    }
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    connect();
  }, [connect]);

  const send = useCallback((msg: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
      return true;
    }
    return false;
  }, []);

  const addHandler = useCallback((handler: MessageHandler) => {
    handlersRef.current.add(handler);
  }, []);

  const removeHandler = useCallback((handler: MessageHandler) => {
    handlersRef.current.delete(handler);
  }, []);

  // Delay initial connect to let backend finish startup
  useEffect(() => {
    mountedRef.current = true;
    initTimeoutRef.current = setTimeout(() => {
      initTimeoutRef.current = null;
      connect();
    }, 3000);
    return () => {
      if (initTimeoutRef.current) {
        clearTimeout(initTimeoutRef.current);
        initTimeoutRef.current = null;
      }
      disconnect();
    };
  }, [connect, disconnect]);

  return { wsRef, connected, status, connect, disconnect, ensureConnected, send, addHandler, removeHandler };
}
