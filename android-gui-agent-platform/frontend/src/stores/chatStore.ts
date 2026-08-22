import { create } from 'zustand'
import type { Conversation, ConversationDetail, Message, TurnStep } from '../api/client'
import { screenshotUrl } from '../api/client'
import type { WSEvent } from '../api/websocket'

export interface AskPending {
  turnId: string
  question: string
  options: string[]
}

interface ChatStore {
  conversations: Conversation[]
  currentConversation: ConversationDetail | null
  messages: Message[]
  steps: TurnStep[]
  latestScreenshot: string | null
  latestAction: string | null
  latestParameters: Record<string, unknown> | null
  latestRawOutput: string | null
  latestRiskLevel: string | null
  latestStepIndex: number | null
  askPending: AskPending | null
  riskPending: WSEvent | null
  turnStatus: string | null
  wsConnected: boolean
  memoryVersion: number

  setConversations: (items: Conversation[]) => void
  setDetail: (detail: ConversationDetail) => void
  clearConversation: () => void
  setWsConnected: (v: boolean) => void
  clearAskPending: () => void
  clearRiskPending: () => void
  handleWSEvent: (event: WSEvent) => void
}

function asString(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

export const useChatStore = create<ChatStore>((set) => ({
  conversations: [],
  currentConversation: null,
  messages: [],
  steps: [],
  latestScreenshot: null,
  latestAction: null,
  latestParameters: null,
  latestRawOutput: null,
  latestRiskLevel: null,
  latestStepIndex: null,
  askPending: null,
  riskPending: null,
  turnStatus: null,
  wsConnected: false,
  memoryVersion: 0,

  setConversations: (items) => set({ conversations: items }),

  setDetail: (detail) => {
    const lastTurn = detail.turns[detail.turns.length - 1] ?? null
    let askPending: AskPending | null = null
    if (lastTurn?.status === 'waiting_ask') {
      const askMsg = [...detail.messages].reverse().find((m) => m.kind === 'ask')
      if (askMsg) {
        askPending = {
          turnId: lastTurn.id,
          question: askMsg.content,
          options: Array.isArray(askMsg.extra?.options) ? (askMsg.extra!.options as string[]) : [],
        }
      }
    }
    set({
      currentConversation: detail,
      messages: detail.messages,
      steps: lastTurn ? lastTurn.steps : [],
      askPending,
      // a waiting-confirm turn resumed via refetch has no event payload: show
      // a generic card so the user can still approve / cancel
      riskPending:
        lastTurn?.status === 'waiting_confirm'
          ? ({
              event: 'risk.detected',
              conversation_id: detail.id,
              data: { turn_id: lastTurn.id, risk_level: 'high', action: '—' },
              timestamp: '',
            } as WSEvent)
          : null,
      turnStatus: lastTurn?.status ?? null,
    })
  },

  clearConversation: () =>
    set({
      currentConversation: null,
      messages: [],
      steps: [],
      latestScreenshot: null,
      latestAction: null,
      latestParameters: null,
      latestRawOutput: null,
      latestRiskLevel: null,
      latestStepIndex: null,
      askPending: null,
      riskPending: null,
      turnStatus: null,
    }),

  setWsConnected: (v) => set({ wsConnected: v }),
  clearAskPending: () => set({ askPending: null }),
  clearRiskPending: () => set({ riskPending: null }),

  handleWSEvent: (event) =>
    set((state) => {
      switch (event.event) {
        case 'message.created': {
          const d = event.data
          const msg: Message = {
            id: asString(d.message_id),
            conversation_id: event.conversation_id,
            turn_id: asString(d.turn_id) || null,
            role: (d.role as Message['role']) ?? 'user',
            kind: (d.kind as Message['kind']) ?? 'text',
            content: asString(d.content),
            extra: null,
            created_at: event.timestamp,
          }
          if (state.messages.some((m) => m.id === msg.id)) return {}
          return { messages: [...state.messages, msg] }
        }
        case 'turn.started':
          return { turnStatus: 'running' }
        case 'step.completed': {
          const d = event.data
          const step: TurnStep = {
            id: `${asString(d.turn_id)}-${d.step_index}`,
            turn_id: asString(d.turn_id),
            step_index: d.step_index as number,
            action: asString(d.action) || null,
            parameters: (d.parameters as Record<string, unknown>) ?? null,
            status: 'completed',
            screenshot_path: asString(d.screenshot_path) || null,
            raw_output: asString(d.raw_output) || null,
            risk_level: asString(d.risk_level) || 'safe',
            created_at: event.timestamp,
          }
          const idx = state.steps.findIndex((s) => s.step_index === step.step_index)
          const steps =
            idx >= 0 ? state.steps.map((s, i) => (i === idx ? step : s)) : [...state.steps, step]
          return {
            steps,
            latestScreenshot:
              (d.screenshot_base64 as string | undefined) ??
              screenshotUrl(step.screenshot_path) ??
              state.latestScreenshot,
            latestAction: step.action,
            latestParameters: step.parameters,
            latestRawOutput: step.raw_output,
            latestRiskLevel: step.risk_level,
            latestStepIndex: step.step_index,
          }
        }
        case 'ask.requested': {
          const d = event.data
          return {
            turnStatus: 'waiting_ask',
            askPending: {
              turnId: asString(d.turn_id),
              question: asString(d.question),
              options: Array.isArray(d.options) ? (d.options as string[]) : [],
            },
          }
        }
        case 'ask.answered':
          return { turnStatus: 'running', askPending: null }
        case 'risk.detected':
          return { turnStatus: 'waiting_confirm', riskPending: event }
        case 'turn.finished':
          return { turnStatus: 'finished', askPending: null, riskPending: null }
        case 'turn.stopped':
          return { turnStatus: 'stopped', askPending: null, riskPending: null }
        case 'turn.failed':
          return { turnStatus: 'failed', askPending: null, riskPending: null }
        case 'memory.updated':
          return { memoryVersion: state.memoryVersion + 1 }
        default:
          return {}
      }
    }),
}))
