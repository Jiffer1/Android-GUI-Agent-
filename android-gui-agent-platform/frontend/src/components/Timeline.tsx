import type { TaskStep } from '../api/client'

const ACTION_COLORS: Record<string, string> = {
  CLICK: 'bg-blue-900 text-blue-300',
  SCROLL: 'bg-purple-900 text-purple-300',
  TYPE: 'bg-green-900 text-green-300',
  OPEN: 'bg-yellow-900 text-yellow-300',
  COMPLETE: 'bg-cyan-900 text-cyan-300',
  BACK: 'bg-gray-700 text-gray-300',
  HOME: 'bg-gray-700 text-gray-300',
}

const STATUS_ICONS: Record<string, string> = {
  completed: '✓',
  running: '⟳',
  failed: '✗',
  pending: '○',
}

interface Props {
  steps: TaskStep[]
  activeStep?: number
}

export default function Timeline({ steps, activeStep }: Props) {
  return (
    <div className="flex flex-col h-full bg-gray-900 rounded border border-gray-800">
      <div className="px-3 py-2 text-xs text-gray-500 border-b border-gray-800 font-semibold uppercase tracking-wider">
        Timeline ({steps.length} steps)
      </div>
      <div className="flex-1 overflow-y-auto">
        {steps.length === 0 ? (
          <div className="text-gray-600 text-xs p-4 text-center">No steps yet</div>
        ) : (
          <div className="divide-y divide-gray-800">
            {steps.map((step) => (
              <div
                key={step.step_index}
                className={`px-3 py-2 flex items-start gap-2 text-xs ${
                  activeStep === step.step_index ? 'bg-gray-800' : ''
                }`}
              >
                <span className="text-gray-500 w-5 shrink-0 text-right">{step.step_index}</span>
                <span
                  className={`px-1.5 py-0.5 rounded text-xs font-mono shrink-0 ${
                    ACTION_COLORS[step.action ?? ''] ?? 'bg-gray-700 text-gray-300'
                  }`}
                >
                  {step.action ?? '—'}
                </span>
                <span className="text-gray-400 truncate flex-1 font-mono">
                  {step.parameters ? JSON.stringify(step.parameters) : ''}
                </span>
                <span
                  className={`shrink-0 ${
                    step.status === 'completed'
                      ? 'text-green-500'
                      : step.status === 'failed'
                      ? 'text-red-500'
                      : 'text-yellow-500'
                  }`}
                >
                  {STATUS_ICONS[step.status] ?? '?'}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
