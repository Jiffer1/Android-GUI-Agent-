import { useEffect, useRef } from 'react'

const WS_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000')
  .replace(/^http/, 'ws')

export interface WSEvent {
  event: string
  conversation_id: string
  data: Record<string, unknown>
  timestamp: string
}

/**
 * Live event stream for one conversation. Reconnects every 2s while the
 * page is open; ``onReconnect`` fires after each successful re-open so the
 * caller can refetch the conversation detail and catch up on any
 * ask.requested / risk.detected / message.created events missed offline.
 */
export function useConversationWebSocket(
  conversationId: string | undefined,
  onEvent: (e: WSEvent) => void,
  onReconnect?: () => void,
) {
  const wsRef = useRef<WebSocket | null>(null)
  const onEventRef = useRef(onEvent)
  const onReconnectRef = useRef(onReconnect)
  onEventRef.current = onEvent
  onReconnectRef.current = onReconnect

  useEffect(() => {
    if (!conversationId) return

    let closed = false
    let openedOnce = false

    const connect = () => {
      if (closed) return
      const ws = new WebSocket(`${WS_BASE}/ws/conversations/${conversationId}`)
      wsRef.current = ws

      ws.onopen = () => {
        if (openedOnce) onReconnectRef.current?.()
        openedOnce = true
      }

      ws.onmessage = (e) => {
        try {
          onEventRef.current(JSON.parse(e.data) as WSEvent)
        } catch {
          // ignore malformed messages
        }
      }

      ws.onclose = () => {
        if (!closed) setTimeout(connect, 2000)
      }

      ws.onerror = () => {
        ws.close()
      }
    }

    connect()

    return () => {
      closed = true
      wsRef.current?.close()
    }
  }, [conversationId])
}
