import { useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { tasksApi } from '../api/client'
import { useTaskWebSocket } from '../api/websocket'
import { useTaskStore } from '../stores/taskStore'
import ScreenshotPanel from '../components/ScreenshotPanel'
import Timeline from '../components/Timeline'
import ActionInspector from '../components/ActionInspector'
import RiskConfirmModal from '../components/RiskConfirmModal'

const STATUS_COLORS: Record<string, string> = {
  pending: 'text-gray-400',
  running: 'text-cyan-400 animate-pulse',
  paused: 'text-yellow-400',
  finished: 'text-green-400',
  failed: 'text-red-400',
  stopped: 'text-gray-500',
}

export default function TaskDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const {
    currentTask, steps, latestScreenshot, latestAction, latestParameters,
    latestConfidence, latestRoute, routeInfo, escalation,
    riskEvent, setCurrentTask, setSteps, handleWSEvent, clearRiskEvent,
  } = useTaskStore()

  useEffect(() => {
    if (!id) return
    tasksApi.get(id).then((r) => {
      setCurrentTask(r.data)
      setSteps(r.data.steps)
    }).catch(() => {})
  }, [id, setCurrentTask, setSteps])

  useTaskWebSocket(id, handleWSEvent)

  const latestStep = steps.length > 0 ? steps[steps.length - 1] : null

  const handleStart = () => id && tasksApi.start(id).catch(() => {})
  const handlePause = () => id && tasksApi.pause(id).catch(() => {})
  const handleResume = () => id && tasksApi.resume(id).catch(() => {})
  const handleStop = () => id && tasksApi.stop(id).catch(() => {})

  const handleConfirm = () => {
    if (id) tasksApi.confirm(id).catch(() => {})
    clearRiskEvent()
  }
  const handleCancelRisk = () => {
    if (id) tasksApi.stop(id).catch(() => {})
    clearRiskEvent()
  }

  const status = currentTask?.status ?? 'pending'
  const isRunning = status === 'running'
  const isPaused = status === 'paused'
  const isActive = isRunning || isPaused

  return (
    <div className="flex flex-col h-full p-3 gap-3">
      {/* Header */}
      <div className="flex items-center gap-3 shrink-0">
        <button onClick={() => navigate('/')} className="text-gray-500 hover:text-gray-300 text-sm">← Back</button>
        <div className="flex-1 min-w-0">
          <div className="text-sm text-gray-200 truncate">{currentTask?.instruction ?? '…'}</div>
          <div className="text-xs text-gray-500 font-mono">
            {id?.slice(0, 8)} · {currentTask?.device_id ?? 'mock'} · step {currentTask?.current_step ?? 0}/{currentTask?.max_steps ?? 20}
            {latestRoute && <span className="ml-2 text-cyan-700">[{latestRoute}]</span>}
            {latestConfidence !== null && <span className="ml-2 text-gray-600">conf {(latestConfidence * 100).toFixed(0)}%</span>}
          </div>
          {escalation && (
            <div className="text-xs text-yellow-500 mt-0.5">
              ⬆ 升级到 ReAct @ step {escalation.step_index}：{escalation.reason}
            </div>
          )}
        </div>
        <span className={`text-sm font-medium ${STATUS_COLORS[status] ?? 'text-gray-400'}`}>{status}</span>
      </div>

      {/* Main layout */}
      <div className="flex-1 grid grid-cols-12 gap-3 min-h-0">
        {/* Screenshot */}
        <div className="col-span-5 min-h-0">
          <ScreenshotPanel
            screenshot={latestScreenshot}
            action={latestAction}
            parameters={latestParameters}
          />
        </div>

        {/* Timeline + Inspector */}
        <div className="col-span-4 flex flex-col gap-3 min-h-0">
          <div className="flex-1 min-h-0">
            <Timeline steps={steps} activeStep={latestStep?.step_index} />
          </div>
          <div className="shrink-0">
            <ActionInspector
              action={latestAction}
              parameters={latestParameters}
              rawOutput={latestStep?.raw_output ?? null}
              riskLevel={latestStep?.risk_level ?? null}
              stepIndex={latestStep?.step_index ?? null}
            />
          </div>
        </div>

        {/* Controls */}
        <div className="col-span-3 flex flex-col gap-2">
          <div className="bg-gray-900 rounded border border-gray-800 p-3 space-y-2">
            <div className="text-xs text-gray-500 font-semibold uppercase tracking-wider mb-2">Controls</div>
            {status === 'pending' && (
              <button onClick={handleStart}
                className="w-full py-2 rounded bg-cyan-700 hover:bg-cyan-600 text-sm font-medium transition-colors">
                ▶ Start
              </button>
            )}
            {isRunning && (
              <button onClick={handlePause}
                className="w-full py-2 rounded bg-yellow-700 hover:bg-yellow-600 text-sm font-medium transition-colors">
                ⏸ Pause
              </button>
            )}
            {isPaused && (
              <button onClick={handleResume}
                className="w-full py-2 rounded bg-cyan-700 hover:bg-cyan-600 text-sm font-medium transition-colors">
                ▶ Resume
              </button>
            )}
            {isActive && (
              <button onClick={handleStop}
                className="w-full py-2 rounded bg-red-800 hover:bg-red-700 text-sm font-medium transition-colors">
                ■ Stop
              </button>
            )}
            {!isActive && status !== 'pending' && (
              <button onClick={handleStart}
                className="w-full py-2 rounded bg-gray-700 hover:bg-gray-600 text-sm font-medium transition-colors">
                ↺ Restart
              </button>
            )}
          </div>

          {/* Step log */}
          <div className="flex-1 bg-gray-900 rounded border border-gray-800 overflow-hidden flex flex-col">
            <div className="px-3 py-2 text-xs text-gray-500 border-b border-gray-800 font-semibold uppercase tracking-wider">
              Log
            </div>
            <div className="flex-1 overflow-y-auto p-2 space-y-1">
              {steps.map((s) => (
                <div key={s.step_index} className="text-xs font-mono text-gray-500">
                  <span className="text-gray-600">[{s.step_index}]</span>{' '}
                  <span className="text-cyan-600">{s.action}</span>{' '}
                  <span>{s.raw_output ?? ''}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <RiskConfirmModal event={riskEvent} onConfirm={handleConfirm} onCancel={handleCancelRisk} />
    </div>
  )
}
