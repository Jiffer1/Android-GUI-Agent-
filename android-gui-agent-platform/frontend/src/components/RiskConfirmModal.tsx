import type { WSEvent } from '../api/websocket'

interface Props {
  event: WSEvent | null
  onConfirm: () => void
  onCancel: () => void
}

export default function RiskConfirmModal({ event, onConfirm, onCancel }: Props) {
  if (!event) return null

  const d = event.data

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-red-800 rounded-lg w-full max-w-md mx-4 shadow-2xl">
        <div className="px-4 py-3 border-b border-red-800 flex items-center gap-2">
          <span className="text-red-400 text-lg">⚠</span>
          <span className="text-red-400 font-semibold text-sm">High-Risk Action Detected</span>
        </div>
        <div className="p-4 space-y-3 text-sm">
          <div className="text-gray-400">{String(d.reason ?? '')}</div>
          <div className="bg-gray-800 rounded p-3 font-mono text-xs space-y-1">
            <div>
              <span className="text-gray-500">Action: </span>
              <span className="text-cyan-400">{String(d.action ?? '')}</span>
            </div>
            <div>
              <span className="text-gray-500">Params: </span>
              <span className="text-gray-300">{JSON.stringify(d.parameters ?? {})}</span>
            </div>
            <div>
              <span className="text-gray-500">Step: </span>
              <span className="text-gray-300">{String(d.step_index ?? '')}</span>
            </div>
          </div>
          <div className="text-gray-500 text-xs">Confirm to proceed, or cancel to stop the task.</div>
        </div>
        <div className="px-4 py-3 border-t border-gray-800 flex gap-2 justify-end">
          <button
            onClick={onCancel}
            className="px-4 py-1.5 text-sm rounded bg-gray-700 hover:bg-gray-600 text-gray-200 transition-colors"
          >
            Cancel Task
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-1.5 text-sm rounded bg-red-700 hover:bg-red-600 text-white transition-colors"
          >
            Confirm
          </button>
        </div>
      </div>
    </div>
  )
}
