import { useRef, useState, useCallback, useEffect } from "react";

export interface WsMessage {
  type: string;
  [key: string]: any;
}

type MessageHandler = (msg: WsMessage) => void;

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [status, setStatus] = useState<"connecting" | "connected" | "disconnected">("disconnected");
  const handlersRef = useRef<Set<MessageHandler>>(new Set());
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const attemptRef = useRef(0);

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
    const wsUrl = `ws://127.0.0.1:8000/ws/ai?token=${token}`;
    const ws = new WebSocket(wsUrl);

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
    const initTimeout = setTimeout(() => connect(), 3000);
    return () => {
      clearTimeout(initTimeout);
      disconnect();
    };
  }, [connect, disconnect]);

  return { wsRef, connected, status, connect, disconnect, send, addHandler, removeHandler };
}
