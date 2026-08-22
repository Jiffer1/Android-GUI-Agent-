import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  conversationsApi,
  devicesApi,
  screenshotUrl,
  type Conversation,
  type Device,
  type Message,
  type TurnStep,
} from '../api/client'
import { useConversationWebSocket } from '../api/websocket'
import { useChatStore } from '../stores/chatStore'
import ActionInspector from '../components/ActionInspector'
import DevicePanel from '../components/DevicePanel'
import ScreenshotPanel from '../components/ScreenshotPanel'
import Timeline from '../components/Timeline'

const ACTIVE_STATUSES = new Set(['pending', 'running', 'waiting_ask', 'waiting_confirm'])

const TURN_STATUS_LABELS: Record<string, string> = {
  pending: '启动中…',
  running: '执行中…',
  waiting_ask: '等待你的回答…',
  waiting_confirm: '等待风险确认…',
  finished: '已完成',
  stopped: '已停止',
  failed: '执行失败',
}

// ---------------------------------------------------------------------------
// Left column: conversation list
// ---------------------------------------------------------------------------

function ConversationList({
  conversations,
  activeId,
  onSelect,
  onDelete,
}: {
  conversations: Conversation[]
  activeId: string | null
  onSelect: (id: string) => void
  onDelete: (id: string) => void
}) {
  const [creating, setCreating] = useState(false)
  const [title, setTitle] = useState('')
  const [devices, setDevices] = useState<Device[]>([])
  const [deviceId, setDeviceId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { setConversations } = useChatStore()

  const openCreate = async () => {
    setCreating(true)
    setTitle('')
    setDeviceId(null)
    try {
      const resp = await devicesApi.list()
      setDevices(resp.data.devices)
    } catch {
      setDevices([])
    }
  }

  const create = async () => {
    setBusy(true)
    try {
      const resp = await conversationsApi.create({
        title: title.trim() || undefined,
        device_id: deviceId,
      })
      const list = await conversationsApi.list()
      setConversations(list.data)
      setCreating(false)
      onSelect(resp.data.id)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col h-full bg-gray-900 border-r border-gray-800">
      <div className="px-3 py-2 border-b border-gray-800 flex items-center justify-between">
        <span className="text-xs text-gray-500 font-semibold uppercase tracking-wider">会话</span>
        <button
          onClick={openCreate}
          className="text-xs px-2 py-1 rounded bg-cyan-700 hover:bg-cyan-600 text-white transition-colors"
        >
          + 新建
        </button>
      </div>

      {creating && (
        <div className="p-3 space-y-2 border-b border-gray-800 bg-gray-800/50">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="会话标题（可选）"
            className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-cyan-600"
          />
          <DevicePanel devices={devices} selected={deviceId} onSelect={setDeviceId} />
          <div className="flex gap-2">
            <button
              onClick={create}
              disabled={busy}
              className="flex-1 px-2 py-1.5 text-xs rounded bg-cyan-700 hover:bg-cyan-600 disabled:opacity-50 text-white transition-colors"
            >
              创建
            </button>
            <button
              onClick={() => setCreating(false)}
              className="px-2 py-1.5 text-xs rounded bg-gray-700 hover:bg-gray-600 text-gray-200 transition-colors"
            >
              取消
            </button>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {conversations.length === 0 ? (
          <div className="text-gray-600 text-xs p-4 text-center">还没有会话，点击「新建」开始</div>
        ) : (
          <div className="divide-y divide-gray-800">
            {conversations.map((c) => (
              <div
                key={c.id}
                className={`group flex items-center gap-1 px-3 py-2 cursor-pointer transition-colors ${
                  activeId === c.id ? 'bg-gray-800' : 'hover:bg-gray-800/50'
                }`}
                onClick={() => onSelect(c.id)}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    {c.status === 'active' && (
                      <span className="w-1.5 h-1.5 rounded-full bg-green-500 shrink-0" />
                    )}
                    <span className="text-xs text-gray-200 truncate">{c.title ?? '未命名会话'}</span>
                  </div>
                  <div className="text-xs text-gray-500 truncate mt-0.5">
                    {c.last_message ?? '（暂无消息）'}
                  </div>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    onDelete(c.id)
                  }}
                  className="opacity-0 group-hover:opacity-100 text-gray-500 hover:text-red-400 text-xs px-1 transition-opacity"
                  title="删除会话"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Center column: chat flow
// ---------------------------------------------------------------------------

function AskCard({
  question,
  options,
  onReply,
}: {
  question: string
  options: string[]
  onReply: (text: string) => void
}) {
  const [text, setText] = useState('')
  return (
    <div className="bg-gray-800 border border-cyan-800 rounded-lg p-3 max-w-[85%]">
      <div className="flex items-center gap-1.5 mb-2">
        <span className="text-cyan-400">❓</span>
        <span className="text-xs text-cyan-400 font-semibold">Agent 提问</span>
      </div>
      <div className="text-sm text-gray-200 mb-2">{question}</div>
      {options.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-2">
          {options.map((opt) => (
            <button
              key={opt}
              onClick={() => onReply(opt)}
              className="px-3 py-1 text-xs rounded-full border border-cyan-700 text-cyan-300 hover:bg-cyan-900/50 transition-colors"
            >
              {opt}
            </button>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && text.trim()) {
              onReply(text.trim())
              setText('')
            }
          }}
          placeholder="输入回答…"
          className="flex-1 bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-cyan-600"
        />
        <button
          onClick={() => {
            if (text.trim()) {
              onReply(text.trim())
              setText('')
            }
          }}
          className="px-3 py-1.5 text-xs rounded bg-cyan-700 hover:bg-cyan-600 text-white transition-colors"
        >
          回答
        </button>
      </div>
    </div>
  )
}

function RiskCard({
  action,
  riskLevel,
  riskCategory,
  currentState,
  consequence,
  rollbackHint,
  reason,
  onConfirm,
  onCancel,
}: {
  action: string
  riskLevel: string
  riskCategory: string
  currentState: string
  consequence: string
  rollbackHint: string
  reason: string
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <div className="bg-gray-800 border border-red-800 rounded-lg p-3 max-w-[85%]">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-red-400">⚠</span>
        <span className="text-xs text-red-400 font-semibold flex-1">需要人工确认</span>
        <span className="text-xs px-2 py-0.5 rounded border border-red-600 bg-red-700/60 text-red-200">
          {(riskLevel || 'high').toUpperCase()}
        </span>
        <span className="text-xs px-2 py-0.5 rounded border border-gray-600 bg-gray-700/40 text-gray-300">
          {riskCategory || 'none'}
        </span>
      </div>
      {currentState && (
        <div className="text-xs text-gray-500 mb-0.5">当前状态</div>
      )}
      {currentState && <div className="text-sm text-gray-200 mb-2">{currentState}</div>}
      <div className="bg-gray-900 rounded p-2 font-mono text-xs text-gray-300 mb-2">
        Action: <span className="text-cyan-400">{action || '—'}</span>
      </div>
      {consequence && (
        <div className="text-xs text-amber-200 mb-1">后果：{consequence}</div>
      )}
      {rollbackHint && <div className="text-xs text-gray-400 mb-1">回退：{rollbackHint}</div>}
      {reason && <div className="text-xs text-gray-500 mb-2">原因：{reason}</div>}
      <div className="flex gap-2 justify-end">
        <button
          onClick={onCancel}
          className="px-3 py-1.5 text-xs rounded bg-gray-700 hover:bg-gray-600 text-gray-200 transition-colors"
        >
          取消
        </button>
        <button
          onClick={onConfirm}
          className="px-3 py-1.5 text-xs rounded bg-red-700 hover:bg-red-600 text-white transition-colors"
        >
          确认执行
        </button>
      </div>
    </div>
  )
}

function ChatMessage({
  msg,
  interactive,
  onReply,
}: {
  msg: Message
  interactive: boolean
  onReply: (text: string) => void
}) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="bg-cyan-800/70 rounded-lg px-3 py-2 max-w-[85%] text-sm text-cyan-50">
          {msg.content}
        </div>
      </div>
    )
  }
  if (msg.kind === 'summary') {
    return (
      <div className="flex justify-start">
        <div className="bg-gray-800 rounded-lg px-3 py-2 max-w-[85%] text-sm text-gray-200 border border-gray-700">
          <div className="text-xs text-gray-500 mb-1">本轮总结</div>
          {msg.content}
        </div>
      </div>
    )
  }
  if (msg.kind === 'ask') {
    const options = Array.isArray(msg.extra?.options) ? (msg.extra!.options as string[]) : []
    if (interactive) return <AskCard question={msg.content} options={options} onReply={onReply} />
    return (
      <div className="flex justify-start">
        <div className="bg-gray-800/60 border border-gray-700 rounded-lg px-3 py-2 max-w-[85%] text-sm text-gray-300">
          <div className="text-xs text-gray-500 mb-1">已提问</div>
          {msg.content}
        </div>
      </div>
    )
  }
  return null
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Chat() {
  const {
    conversations,
    setConversations,
    currentConversation,
    messages,
    steps,
    latestScreenshot,
    latestAction,
    latestParameters,
    latestRawOutput,
    latestRiskLevel,
    latestStepIndex,
    askPending,
    riskPending,
    turnStatus,
    setDetail,
    clearConversation,
    clearAskPending,
    clearRiskPending,
    handleWSEvent,
  } = useChatStore()

  const [activeId, setActiveId] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [selectedStep, setSelectedStep] = useState<TurnStep | null>(null)
  const flowRef = useRef<HTMLDivElement>(null)

  const active = ACTIVE_STATUSES.has(turnStatus ?? '')

  const refetchDetail = useCallback(
    async (id: string) => {
      const resp = await conversationsApi.get(id)
      setDetail(resp.data)
    },
    [setDetail],
  )

  useEffect(() => {
    conversationsApi
      .list()
      .then((resp) => {
        setConversations(resp.data)
        if (resp.data.length > 0) setActiveId((cur) => cur ?? resp.data[0].id)
      })
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!activeId) return
    setSelectedStep(null)
    refetchDetail(activeId).catch(() => {})
  }, [activeId, refetchDetail])

  useConversationWebSocket(activeId ?? undefined, handleWSEvent, () => {
    if (activeId) refetchDetail(activeId).catch(() => {})
  })

  useEffect(() => {
    flowRef.current?.scrollTo({ top: flowRef.current.scrollHeight })
  }, [messages.length, askPending, riskPending])

  const selectConversation = (id: string) => {
    clearConversation()
    setActiveId(id)
  }

  const deleteConversation = async (id: string) => {
    await conversationsApi.remove(id)
    const resp = await conversationsApi.list()
    setConversations(resp.data)
    if (activeId === id) {
      clearConversation()
      setActiveId(resp.data[0]?.id ?? null)
    }
  }

  const send = async () => {
    if (!activeId || !input.trim() || active) return
    const text = input.trim()
    setInput('')
    try {
      await conversationsApi.sendMessage(activeId, text)
    } catch {
      // busy (conversation_busy / device_busy): the WS status refreshes the UI
      await refetchDetail(activeId).catch(() => {})
    }
  }

  const reply = async (text: string) => {
    if (!activeId) return
    clearAskPending()
    try {
      await conversationsApi.reply(activeId, text)
    } catch {
      if (activeId) refetchDetail(activeId).catch(() => {})
    }
  }

  const confirmRisk = async (approved: boolean) => {
    if (!activeId) return
    clearRiskPending()
    try {
      await conversationsApi.confirm(activeId, approved)
    } catch {
      if (activeId) refetchDetail(activeId).catch(() => {})
    }
  }

  const stop = async () => {
    if (!activeId) return
    try {
      await conversationsApi.stop(activeId)
    } catch {
      /* no active turn */
    }
  }

  const shownScreenshot = useMemo(() => {
    if (selectedStep) return screenshotUrl(selectedStep.screenshot_path)
    return latestScreenshot
  }, [selectedStep, latestScreenshot])

  const inspector = selectedStep
    ? {
        action: selectedStep.action,
        parameters: selectedStep.parameters,
        rawOutput: selectedStep.raw_output,
        riskLevel: selectedStep.risk_level,
        stepIndex: selectedStep.step_index,
      }
    : {
        action: latestAction,
        parameters: latestParameters,
        rawOutput: latestRawOutput,
        riskLevel: latestRiskLevel,
        stepIndex: latestStepIndex,
      }

  const riskCardData = riskPending?.data as Record<string, unknown> | undefined

  return (
    <div className="flex h-full">
      <div className="w-64 shrink-0">
        <ConversationList
          conversations={conversations}
          activeId={activeId}
          onSelect={selectConversation}
          onDelete={deleteConversation}
        />
      </div>

      <div className="flex-1 flex flex-col min-w-0 bg-gray-950">
        <div className="flex items-center gap-2 px-4 h-12 bg-gray-900 border-b border-gray-800 shrink-0">
          <span className="text-sm text-gray-200 truncate flex-1">
            {currentConversation?.title ?? '未选择会话'}
          </span>
          {currentConversation?.device_id && (
            <span className="text-xs text-gray-500 font-mono">{currentConversation.device_id}</span>
          )}
          {turnStatus && (
            <span
              className={`text-xs px-2 py-0.5 rounded ${
                active ? 'bg-green-900/60 text-green-300' : 'bg-gray-800 text-gray-400'
              }`}
            >
              {TURN_STATUS_LABELS[turnStatus] ?? turnStatus}
            </span>
          )}
          {active && (
            <button
              onClick={stop}
              className="text-xs px-2 py-1 rounded bg-red-800 hover:bg-red-700 text-red-100 transition-colors"
            >
              停止
            </button>
          )}
        </div>

        <div ref={flowRef} className="flex-1 overflow-y-auto p-4 space-y-3">
          {messages.map((msg) => (
            <ChatMessage
              key={msg.id}
              msg={msg}
              interactive={
                !!askPending &&
                msg.kind === 'ask' &&
                msg.content === askPending.question &&
                msg.id === [...messages].reverse().find((m) => m.kind === 'ask')?.id
              }
              onReply={reply}
            />
          ))}
          {riskCardData && turnStatus === 'waiting_confirm' && (
            <div className="flex justify-start">
              <RiskCard
                action={String(riskCardData.action ?? '—')}
                riskLevel={String(riskCardData.risk_level ?? 'high')}
                riskCategory={String(riskCardData.risk_category ?? 'none')}
                currentState={String(riskCardData.current_state ?? '')}
                consequence={String(riskCardData.consequence ?? '')}
                rollbackHint={String(riskCardData.rollback_hint ?? '')}
                reason={String(riskCardData.reason ?? '')}
                onConfirm={() => confirmRisk(true)}
                onCancel={() => confirmRisk(false)}
              />
            </div>
          )}
        </div>

        <div className="px-4 py-3 border-t border-gray-800 bg-gray-900 shrink-0">
          <div className="flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') send()
              }}
              disabled={!activeId || active}
              placeholder={
                turnStatus === 'waiting_ask'
                  ? '请在上方卡片中回答 Agent 的提问'
                  : active
                    ? TURN_STATUS_LABELS[turnStatus ?? 'running'] ?? '执行中…'
                    : '输入任务指令，例如：打开抖音搜索猫咪视频'
              }
              className="flex-1 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-cyan-600 disabled:opacity-50"
            />
            <button
              onClick={send}
              disabled={!activeId || active || !input.trim()}
              className="px-4 py-2 text-sm rounded bg-cyan-700 hover:bg-cyan-600 disabled:opacity-50 text-white transition-colors"
            >
              发送
            </button>
          </div>
        </div>
      </div>

      <div className="w-[380px] shrink-0 flex flex-col gap-2 p-2 bg-gray-950 border-l border-gray-800 overflow-y-auto">
        <div className="h-[46%] min-h-[240px]">
          <ScreenshotPanel
            screenshot={shownScreenshot}
            action={inspector.action}
            parameters={inspector.parameters}
          />
        </div>
        <div className="flex-1 min-h-[160px]" onClick={() => setSelectedStep(null)}>
          <Timeline
            steps={steps}
            activeStep={selectedStep?.step_index ?? latestStepIndex ?? undefined}
            onStepClick={(idx) => {
              const step = steps.find((s) => s.step_index === idx)
              setSelectedStep(step && step !== selectedStep ? step : null)
            }}
          />
        </div>
        <ActionInspector {...inspector} />
      </div>
    </div>
  )
}
