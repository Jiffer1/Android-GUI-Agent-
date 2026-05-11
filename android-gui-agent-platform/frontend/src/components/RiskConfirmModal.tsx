import type { WSEvent } from '../api/websocket'

interface Props {
  event: WSEvent | null
  onConfirm: () => void
  onCancel: () => void
}

const CATEGORY_LABELS: Record<string, string> = {
  payment: '支付类',
  delete: '删除/清空',
  auth: '授权/登录',
  submit: '提交/发布',
  communication: '通讯发送',
  system: '系统级操作',
  none: '未分类',
}

const CATEGORY_COLORS: Record<string, string> = {
  payment: 'bg-amber-700/40 text-amber-300 border-amber-700',
  delete: 'bg-red-800/40 text-red-300 border-red-700',
  auth: 'bg-purple-800/40 text-purple-300 border-purple-700',
  submit: 'bg-blue-800/40 text-blue-300 border-blue-700',
  communication: 'bg-emerald-800/40 text-emerald-300 border-emerald-700',
  system: 'bg-rose-800/40 text-rose-300 border-rose-700',
  none: 'bg-gray-700/40 text-gray-300 border-gray-700',
}

const LEVEL_COLORS: Record<string, string> = {
  high: 'bg-red-700/60 text-red-200 border-red-600',
  medium: 'bg-yellow-700/60 text-yellow-200 border-yellow-600',
  safe: 'bg-gray-700/60 text-gray-300 border-gray-600',
}

function pickString(d: Record<string, unknown>, key: string): string {
  const v = d[key]
  return typeof v === 'string' ? v : ''
}

export default function RiskConfirmModal({ event, onConfirm, onCancel }: Props) {
  if (!event) return null

  const d = event.data
  const riskLevel = pickString(d, 'risk_level') || 'high'
  const riskCategory = pickString(d, 'risk_category') || 'none'
  const currentState = pickString(d, 'current_state')
  const consequence = pickString(d, 'consequence')
  const rollback = pickString(d, 'rollback_hint')
  const reason = pickString(d, 'reason')
  const action = pickString(d, 'action')
  const stepIndex = d.step_index
  const uiRiskElements = Array.isArray(d.ui_risk_elements) ? (d.ui_risk_elements as Array<Record<string, unknown>>) : []

  const levelColor = LEVEL_COLORS[riskLevel] ?? LEVEL_COLORS.high
  const categoryColor = CATEGORY_COLORS[riskCategory] ?? CATEGORY_COLORS.none

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-red-800 rounded-lg w-full max-w-xl mx-4 shadow-2xl max-h-[90vh] overflow-y-auto">
        <div className="px-4 py-3 border-b border-red-800 flex items-center gap-2">
          <span className="text-red-400 text-lg">⚠</span>
          <span className="text-red-400 font-semibold text-sm flex-1">需要人工确认</span>
          <span className={`text-xs px-2 py-0.5 rounded border ${levelColor}`}>{riskLevel.toUpperCase()}</span>
          <span className={`text-xs px-2 py-0.5 rounded border ${categoryColor}`}>
            {CATEGORY_LABELS[riskCategory] ?? riskCategory}
          </span>
        </div>

        <div className="p-4 space-y-3 text-sm">
          {currentState && (
            <section>
              <div className="text-xs text-gray-500 mb-1">当前状态</div>
              <div className="text-gray-200">{currentState}</div>
            </section>
          )}

          <section>
            <div className="text-xs text-gray-500 mb-1">拟执行动作</div>
            <div className="bg-gray-800 rounded p-3 font-mono text-xs space-y-1">
              <div>
                <span className="text-gray-500">Action: </span>
                <span className="text-cyan-400">{action}</span>
              </div>
              <div>
                <span className="text-gray-500">Params: </span>
                <span className="text-gray-300">{JSON.stringify(d.parameters ?? {})}</span>
              </div>
              <div>
                <span className="text-gray-500">Step: </span>
                <span className="text-gray-300">{String(stepIndex ?? '')}</span>
              </div>
            </div>
          </section>

          {consequence && (
            <section>
              <div className="text-xs text-gray-500 mb-1">执行后果</div>
              <div className="text-amber-200">{consequence}</div>
            </section>
          )}

          {rollback && (
            <section>
              <div className="text-xs text-gray-500 mb-1">回退方式</div>
              <div className="text-gray-300">{rollback}</div>
            </section>
          )}

          {reason && (
            <section>
              <div className="text-xs text-gray-500 mb-1">判定原因</div>
              <div className="text-gray-400">{reason}</div>
            </section>
          )}

          {uiRiskElements.length > 0 && (
            <section>
              <div className="text-xs text-gray-500 mb-1">UI 高风险元素</div>
              <div className="bg-gray-800 rounded p-2 font-mono text-xs text-gray-300 space-y-0.5">
                {uiRiskElements.map((el, i) => (
                  <div key={i}>· {String(el.text ?? '')} @ {JSON.stringify(el.point ?? [])}</div>
                ))}
              </div>
            </section>
          )}

          <div className="text-gray-500 text-xs">点击「确认执行」将放行此次动作；点击「取消任务」将停止整个任务。</div>
        </div>

        <div className="px-4 py-3 border-t border-gray-800 flex gap-2 justify-end sticky bottom-0 bg-gray-900">
          <button
            onClick={onCancel}
            className="px-4 py-1.5 text-sm rounded bg-gray-700 hover:bg-gray-600 text-gray-200 transition-colors"
          >
            取消任务
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-1.5 text-sm rounded bg-red-700 hover:bg-red-600 text-white transition-colors"
          >
            确认执行
          </button>
        </div>
      </div>
    </div>
  )
}
