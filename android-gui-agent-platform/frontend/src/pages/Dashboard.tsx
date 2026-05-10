import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { tasksApi, devicesApi, type Task, type Device } from '../api/client'

const STATUS_COLORS: Record<string, string> = {
  pending: 'text-gray-400',
  running: 'text-cyan-400',
  paused: 'text-yellow-400',
  finished: 'text-green-400',
  failed: 'text-red-400',
  stopped: 'text-gray-500',
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [tasks, setTasks] = useState<Task[]>([])
  const [devices, setDevices] = useState<Device[]>([])
  const [instruction, setInstruction] = useState('')
  const [deviceId, setDeviceId] = useState('')
  const [maxSteps, setMaxSteps] = useState(20)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadTasks = () => {
    tasksApi.list().then((r) => setTasks(r.data)).catch(() => {})
  }

  useEffect(() => {
    loadTasks()
    devicesApi.list().then((r) => setDevices(r.data.devices)).catch(() => {})
    const interval = setInterval(loadTasks, 5000)
    return () => clearInterval(interval)
  }, [])

  const handleCreate = async () => {
    if (!instruction.trim()) return
    setCreating(true)
    setError(null)
    try {
      const r = await tasksApi.create({
        instruction: instruction.trim(),
        device_id: deviceId || undefined,
        max_steps: maxSteps,
      })
      setInstruction('')
      navigate(`/tasks/${r.data.id}`)
    } catch (e: unknown) {
      setError(String(e))
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="p-4 max-w-6xl mx-auto">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Create task */}
        <div className="lg:col-span-1 bg-gray-900 rounded border border-gray-800 p-4 space-y-3">
          <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">New Task</h2>
          <div>
            <label className="text-xs text-gray-500 block mb-1">Instruction</label>
            <textarea
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 resize-none focus:outline-none focus:border-cyan-600 h-24"
              placeholder="e.g. Open Settings and turn on Wi-Fi"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">Device</label>
            <select
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-cyan-600"
              value={deviceId}
              onChange={(e) => setDeviceId(e.target.value)}
            >
              <option value="">— No device (mock mode) —</option>
              {devices.map((d) => (
                <option key={d.serial} value={d.serial}>{d.serial}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">Max Steps</label>
            <input
              type="number"
              min={1}
              max={100}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-cyan-600"
              value={maxSteps}
              onChange={(e) => setMaxSteps(Number(e.target.value))}
            />
          </div>
          {error && <div className="text-red-400 text-xs">{error}</div>}
          <button
            onClick={handleCreate}
            disabled={creating || !instruction.trim()}
            className="w-full py-2 rounded bg-cyan-700 hover:bg-cyan-600 disabled:opacity-40 text-sm font-medium transition-colors"
          >
            {creating ? 'Creating…' : 'Create Task'}
          </button>
        </div>

        {/* Task list */}
        <div className="lg:col-span-2 bg-gray-900 rounded border border-gray-800">
          <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">Tasks</h2>
            <button onClick={loadTasks} className="text-xs text-gray-500 hover:text-gray-300">Refresh</button>
          </div>
          {tasks.length === 0 ? (
            <div className="p-8 text-center text-gray-600 text-sm">No tasks yet</div>
          ) : (
            <div className="divide-y divide-gray-800">
              {tasks.map((t) => (
                <button
                  key={t.id}
                  onClick={() => navigate(`/tasks/${t.id}`)}
                  className="w-full text-left px-4 py-3 hover:bg-gray-800 transition-colors"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-gray-200 truncate">{t.instruction}</div>
                      <div className="text-xs text-gray-500 mt-0.5 font-mono">
                        {t.id.slice(0, 8)} · {t.device_id ?? 'mock'} · step {t.current_step}/{t.max_steps}
                      </div>
                    </div>
                    <span className={`text-xs font-medium shrink-0 ${STATUS_COLORS[t.status] ?? 'text-gray-400'}`}>
                      {t.status}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
