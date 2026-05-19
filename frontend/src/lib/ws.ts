import type { StreamEvent } from "@/types";

type Listener = (event: StreamEvent) => void;

export class EventStream {
  private socket: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private reconnectTimer: number | null = null;
  private path: string;

  constructor(repoId?: string) {
    this.path = repoId
      ? `/api/stream/${encodeURIComponent(repoId)}`
      : `/api/stream`;
  }

  connect() {
    if (this.socket) return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host || "127.0.0.1:7777";
    const ws = new WebSocket(`${proto}//${host}${this.path}`);
    this.socket = ws;
    ws.onmessage = (msg) => {
      try {
        const evt = JSON.parse(msg.data) as StreamEvent;
        for (const cb of this.listeners) cb(evt);
      } catch {
        // ignore malformed
      }
    };
    ws.onclose = () => {
      this.socket = null;
      this.scheduleReconnect();
    };
    ws.onerror = () => {
      ws.close();
    };
  }

  private scheduleReconnect() {
    if (this.reconnectTimer !== null) return;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 2000);
  }

  subscribe(cb: Listener): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  close() {
    this.socket?.close();
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }
}
