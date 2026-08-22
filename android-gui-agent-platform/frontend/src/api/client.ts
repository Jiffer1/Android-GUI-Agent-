import axios from 'axios'

const BASE_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

export const apiClient = axios.create({ baseURL: BASE_URL })

export function screenshotUrl(path: string | null | undefined): string | null {
  if (!path) return null
  if (path.startsWith('data:')) return path
  if (path.startsWith('/artifacts/')) return `${BASE_URL}${path}`
  return `${BASE_URL}/artifacts/${path}`
}

export interface TurnStep {
  id: string
  turn_id: string
  step_index: number
  action: string | null
  parameters: Record<string, unknown> | null
  status: string
  screenshot_path: string | null
  raw_output: string | null
  risk_level: string
  created_at: string
}

export interface Turn {
  id: string
  conversation_id: string
  user_message_id: string | null
  status: string
  max_steps: number
  error: string | null
  created_at: string
  finished_at: string | null
  steps: TurnStep[]
}

export interface Message {
  id: string
  conversation_id: string
  turn_id: string | null
  role: 'user' | 'assistant'
  kind: 'text' | 'ask' | 'risk' | 'summary'
  content: string
  extra: Record<string, unknown> | null
  created_at: string
}

export interface Conversation {
  id: string
  title: string | null
  device_id: string | null
  status: string
  created_at: string
  updated_at: string
  last_message?: string | null
}

export interface ConversationDetail extends Conversation {
  messages: Message[]
  turns: Turn[]
}

export interface Device {
  serial: string
  status: string
}

export interface MemoryData {
  preferences: string[]
  paths: Record<string, string[]>
}

export const conversationsApi = {
  list: () => apiClient.get<Conversation[]>('/api/conversations'),
  get: (id: string) => apiClient.get<ConversationDetail>(`/api/conversations/${id}`),
  create: (data: { title?: string; device_id?: string | null }) =>
    apiClient.post<Conversation>('/api/conversations', data),
  remove: (id: string) => apiClient.delete(`/api/conversations/${id}`),
  sendMessage: (id: string, text: string) =>
    apiClient.post<{ turn_id: string; status: string }>(`/api/conversations/${id}/messages`, { text }),
  reply: (id: string, text: string) =>
    apiClient.post(`/api/conversations/${id}/reply`, { text }),
  confirm: (id: string, approved: boolean) =>
    apiClient.post(`/api/conversations/${id}/confirm`, { approved }),
  stop: (id: string) => apiClient.post(`/api/conversations/${id}/stop`),
}

export const memoryApi = {
  list: () => apiClient.get<MemoryData>('/api/memory'),
  deleteEntry: (file: 'preferences' | 'paths', index: number) =>
    apiClient.delete('/api/memory/entries', { data: { file, index } }),
  clearAll: () => apiClient.delete('/api/memory'),
}

export const devicesApi = {
  list: () => apiClient.get<{ devices: Device[]; count: number }>('/api/devices'),
}
