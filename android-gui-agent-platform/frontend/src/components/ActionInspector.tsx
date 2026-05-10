interface Props {
  action: string | null
  parameters: Record<string, unknown> | null
  rawOutput: string | null
  riskLevel: string | null
  stepIndex: number | null
}

const RISK_BADGE: Record<string, string> = {
  safe: 'bg-green-900 text-green-400',
  high: 'bg-red-900 text-red-400',
}

export default function ActionInspector({ action, parameters, rawOutput, riskLevel, stepIndex }: Props) {
  return (
    <div className="bg-gray-900 rounded border border-gray-800">
      <div className="px-3 py-2 text-xs text-gray-500 border-b border-gray-800 font-semibold uppercase tracking-wider">
        Current Action
      </div>
      <div className="p-3 space-y-2 text-xs font-mono">
        <div className="flex items-center gap-2">
          <span className="text-gray-500 w-20 shrink-0">Step</span>
          <span className="text-gray-200">{stepIndex ?? '—'}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-gray-500 w-20 shrink-0">Action</span>
          <span className="text-cyan-400 font-bold">{action ?? '—'}</span>
        </div>
        <div className="flex items-start gap-2">
          <span className="text-gray-500 w-20 shrink-0 mt-0.5">Params</span>
          <pre className="text-gray-300 whitespace-pre-wrap break-all flex-1 bg-gray-800 rounded p-1.5 text-xs">
            {parameters ? JSON.stringify(parameters, null, 2) : '—'}
          </pre>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-gray-500 w-20 shrink-0">Risk</span>
          <span className={`px-1.5 py-0.5 rounded text-xs ${RISK_BADGE[riskLevel ?? 'safe'] ?? RISK_BADGE.safe}`}>
            {riskLevel ?? 'safe'}
          </span>
        </div>
        {rawOutput && (
          <div className="flex items-start gap-2">
            <span className="text-gray-500 w-20 shrink-0 mt-0.5">Raw</span>
            <pre className="text-gray-400 whitespace-pre-wrap break-all flex-1 bg-gray-800 rounded p-1.5 text-xs max-h-24 overflow-y-auto">
              {rawOutput}
            </pre>
          </div>
        )}
      </div>
    </div>
  )
}
