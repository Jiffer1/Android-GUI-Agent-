import { useCallback, useEffect, useState } from 'react'
import { memoryApi, type MemoryData } from '../api/client'
import { useChatStore } from '../stores/chatStore'

function EntryRow({
  text,
  onDelete,
}: {
  text: string
  onDelete: () => void
}) {
  return (
    <div className="group flex items-start gap-2 px-3 py-2">
      <span className="text-gray-500">·</span>
      <span className="text-sm text-gray-200 flex-1">{text}</span>
      <button
        onClick={onDelete}
        className="opacity-0 group-hover:opacity-100 text-gray-500 hover:text-red-400 text-xs px-1 transition-opacity shrink-0"
        title="删除此条"
      >
        ✕
      </button>
    </div>
  )
}

export default function Memory() {
  const memoryVersion = useChatStore((s) => s.memoryVersion)
  const [data, setData] = useState<MemoryData>({ preferences: [], paths: {} })

  const load = useCallback(() => {
    memoryApi
      .list()
      .then((resp) => setData(resp.data))
      .catch(() => {})
  }, [])

  useEffect(() => {
    load()
  }, [load, memoryVersion])

  const deleteEntry = async (file: 'preferences' | 'paths', index: number) => {
    try {
      await memoryApi.deleteEntry(file, index)
    } finally {
      load()
    }
  }

  const clearAll = async () => {
    try {
      await memoryApi.clearAll()
    } finally {
      load()
    }
  }

  const pathSections = Object.entries(data.paths)
  const total = data.preferences.length + pathSections.reduce((n, [, items]) => n + items.length, 0)

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-3xl mx-auto space-y-4">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold text-gray-100">长期记忆</h1>
          <span className="text-xs text-gray-500">共 {total} 条</span>
          <div className="flex-1" />
          {total > 0 && (
            <button
              onClick={clearAll}
              className="text-xs px-3 py-1.5 rounded bg-red-800 hover:bg-red-700 text-red-100 transition-colors"
            >
              清空全部
            </button>
          )}
        </div>

        <div className="bg-gray-900 rounded border border-gray-800">
          <div className="px-3 py-2 text-xs text-gray-500 border-b border-gray-800 font-semibold uppercase tracking-wider">
            用户偏好（preferences）
          </div>
          {data.preferences.length === 0 ? (
            <div className="text-gray-600 text-xs p-4 text-center">暂无偏好记录</div>
          ) : (
            <div className="divide-y divide-gray-800/50">
              {data.preferences.map((p, i) => (
                <EntryRow key={`${i}-${p}`} text={p} onDelete={() => deleteEntry('preferences', i)} />
              ))}
            </div>
          )}
        </div>

        <div className="bg-gray-900 rounded border border-gray-800">
          <div className="px-3 py-2 text-xs text-gray-500 border-b border-gray-800 font-semibold uppercase tracking-wider">
            执行路径（paths）
          </div>
          {pathSections.length === 0 ? (
            <div className="text-gray-600 text-xs p-4 text-center">暂无路径记录</div>
          ) : (
            pathSections.map(([taskType, items]) => (
              <div key={taskType} className="border-b border-gray-800/50 last:border-b-0">
                <div className="px-3 py-2 text-xs text-cyan-400 font-mono">## {taskType}</div>
                <div className="divide-y divide-gray-800/30">
                  {items.map((entry, i) => (
                    <EntryRow
                      key={`${i}-${entry}`}
                      text={entry}
                      onDelete={() => deleteEntry('paths', i)}
                    />
                  ))}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
