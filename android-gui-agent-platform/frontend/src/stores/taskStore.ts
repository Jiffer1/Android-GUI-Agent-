import { create } from 'zustand'
import type { Task, TaskStep } from '../api/client'
import type { WSEvent } from '../api/websocket'

export interface RouteInfo {
  route: string
  reason: string
}

export interface EscalationInfo {
  step_index: number
  reason: string
  signals: string[]
  from_route: string
  to_route: string
}

export interface RiskObservedEvent {
  step_index: number
  action: string
  parameters: Record<string, unknown>
  risk_level: string
  risk_category: string
  current_state: string
  consequence: string
  reason: string
  timestamp: string
}

interface TaskStore {
  tasks: Task[]
  currentTask: Task | null
  steps: TaskStep[]
  latestScreenshot: string | null
  latestAction: string | null
  latestParameters: Record<string, unknown> | null
  latestConfidence: number | null
  latestRoute: string | null
  routeInfo: RouteInfo | null
  escalation: EscalationInfo | null
  riskEvent: WSEvent | null
  riskObservedEvents: RiskObservedEvent[]
  wsConnected: boolean

  setTasks: (tasks: Task[]) => void
  setCurrentTask: (task: Task | null) => void
  setSteps: (steps: TaskStep[]) => void
  setWsConnected: (v: boolean) => void
  clearRiskEvent: () => void
  clearRiskObservedEvents: () => void
  handleWSEvent: (event: WSEvent) => void
}

function asString(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function asNumber(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

export const useTaskStore = create<TaskStore>((set) => ({
  tasks: [],
  currentTask: null,
  steps: [],
  latestScreenshot: null,
  latestAction: null,
  latestParameters: null,
  latestConfidence: null,
  latestRoute: null,
  routeInfo: null,
  escalation: null,
  riskEvent: null,
  riskObservedEvents: [],
  wsConnected: false,

  setTasks: (tasks) => set({ tasks }),
  setCurrentTask: (task) =>
    set({ currentTask: task, routeInfo: null, escalation: null, latestRoute: null, latestConfidence: null, riskObservedEvents: [] }),
  setSteps: (steps) => set({ steps }),
  setWsConnected: (v) => set({ wsConnected: v }),
  clearRiskEvent: () => set({ riskEvent: null }),
  clearRiskObservedEvents: () => set({ riskObservedEvents: [] }),

  handleWSEvent: (event) =>
    set((state) => {
      switch (event.event) {
        case 'task.started':
          return {
            currentTask: state.currentTask
              ? { ...state.currentTask, status: 'running' }
              : state.currentTask,
          }
        case 'task.routed':
          return {
            routeInfo: {
              route: asString(event.data.route),
              reason: asString(event.data.reason),
            },
            latestRoute: asString(event.data.route),
          }
        case 'escalation.triggered':
          return {
            escalation: {
              step_index: (event.data.step_index as number) ?? 0,
              reason: asString(event.data.reason),
              signals: Array.isArray(event.data.signals) ? (event.data.signals as string[]) : [],
              from_route: asString(event.data.from_route),
              to_route: asString(event.data.to_route),
            },
            latestRoute: asString(event.data.to_route) || state.latestRoute,
          }
        case 'step.completed': {
          const d = event.data
          const newStep: TaskStep = {
            id: String(d.step_index),
            task_id: event.task_id,
            step_index: d.step_index as number,
            action: d.action as string,
            parameters: d.parameters as Record<string, unknown>,
            status: 'completed',
            screenshot_path: d.screenshot_path as string | null,
            raw_output: d.raw_output as string | null,
            risk_level: d.risk_level as string,
            created_at: event.timestamp,
          }
          const existingIdx = state.steps.findIndex((s) => s.step_index === newStep.step_index)
          const steps =
            existingIdx >= 0
              ? state.steps.map((s, i) => (i === existingIdx ? newStep : s))
              : [...state.steps, newStep]
          return {
            steps,
            latestScreenshot: (d.screenshot_base64 as string | undefined) ?? state.latestScreenshot,
            latestAction: d.action as string,
            latestParameters: d.parameters as Record<string, unknown>,
            latestConfidence: asNumber(d.confidence) ?? state.latestConfidence,
            latestRoute: asString(d.route) || state.latestRoute,
          }
        }
        case 'task.paused':
          return {
            currentTask: state.currentTask
              ? { ...state.currentTask, status: 'paused' }
              : state.currentTask,
          }
        case 'task.finished':
          return {
            currentTask: state.currentTask
              ? { ...state.currentTask, status: 'finished' }
              : state.currentTask,
          }
        case 'task.stopped':
          return {
            currentTask: state.currentTask
              ? { ...state.currentTask, status: 'stopped' }
              : state.currentTask,
          }
        case 'task.failed':
          return {
            currentTask: state.currentTask
              ? { ...state.currentTask, status: 'failed' }
              : state.currentTask,
          }
        case 'risk.detected':
          return { riskEvent: event }
        case 'risk.observed': {
          const d = event.data
          const obs: RiskObservedEvent = {
            step_index: (d.step_index as number) ?? 0,
            action: asString(d.action),
            parameters: (d.parameters as Record<string, unknown>) ?? {},
            risk_level: asString(d.risk_level),
            risk_category: asString(d.risk_category),
            current_state: asString(d.current_state),
            consequence: asString(d.consequence),
            reason: asString(d.reason),
            timestamp: event.timestamp,
          }
          return { riskObservedEvents: [...state.riskObservedEvents, obs] }
        }
        default:
          return {}
      }
    }),
}))
