import axios from 'axios'

const BASE_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

export const apiClient = axios.create({ baseURL: BASE_URL })

export interface TaskStep {
  id: string
  task_id: string
  step_index: number
  action: string | null
  parameters: Record<string, unknown> | null
  status: string
  screenshot_path: string | null
  raw_output: string | null
  risk_level: string
  created_at: string
}

export interface Task {
  id: string
  instruction: string
  device_id: string | null
  status: string
  created_at: string
  updated_at: string
  max_steps: number
  current_step: number
  steps: TaskStep[]
}

export interface Device {
  serial: string
  status: string
}

export const tasksApi = {
  list: () => apiClient.get<Task[]>('/api/tasks'),
  get: (id: string) => apiClient.get<Task>(`/api/tasks/${id}`),
  create: (data: { instruction: string; device_id?: string; max_steps?: number }) =>
    apiClient.post<Task>('/api/tasks', data),
  start: (id: string) => apiClient.post(`/api/tasks/${id}/start`),
  pause: (id: string) => apiClient.post(`/api/tasks/${id}/pause`),
  resume: (id: string) => apiClient.post(`/api/tasks/${id}/resume`),
  stop: (id: string) => apiClient.post(`/api/tasks/${id}/stop`),
  confirm: (id: string) => apiClient.post(`/api/tasks/${id}/confirm`),
}

export const devicesApi = {
  list: () => apiClient.get<{ devices: Device[]; count: number }>('/api/devices'),
}
