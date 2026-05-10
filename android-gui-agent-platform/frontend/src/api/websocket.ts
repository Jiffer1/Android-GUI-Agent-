import { useEffect, useRef } from 'react'

const WS_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000')
  .replace(/^http/, 'ws')

export interface WSEvent {
  event: string
  task_id: string
  data: Record<string, unknown>
  timestamp: string
}

export function useTaskWebSocket(taskId: string | undefined, onEvent: (e: WSEvent) => void) {
  const wsRef = useRef<WebSocket | null>(null)
  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent

  useEffect(() => {
    if (!taskId) return

    let closed = false

    const connect = () => {
      if (closed) return
      const ws = new WebSocket(`${WS_BASE}/ws/tasks/${taskId}`)
      wsRef.current = ws

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
  }, [taskId])
}
