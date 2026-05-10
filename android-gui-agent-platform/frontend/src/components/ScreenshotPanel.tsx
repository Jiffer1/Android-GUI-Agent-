import { useRef, useState } from 'react'

interface Props {
  screenshot: string | null
  action: string | null
  parameters: Record<string, unknown> | null
}

export default function ScreenshotPanel({ screenshot, action, parameters }: Props) {
  const imgRef = useRef<HTMLImageElement>(null)
  const [imgSize, setImgSize] = useState({ w: 0, h: 0 })

  const onLoad = () => {
    if (imgRef.current) {
      setImgSize({ w: imgRef.current.clientWidth, h: imgRef.current.clientHeight })
    }
  }

  const renderOverlay = () => {
    if (!action || !parameters || imgSize.w === 0) return null

    if (action === 'CLICK') {
      const pt = parameters.point as number[] | undefined
      if (!pt) return null
      const x = (pt[0] / 1000) * imgSize.w
      const y = (pt[1] / 1000) * imgSize.h
      return (
        <div
          className="absolute w-5 h-5 rounded-full border-2 border-red-500 bg-red-500/30 -translate-x-1/2 -translate-y-1/2 pointer-events-none"
          style={{ left: x, top: y }}
        />
      )
    }

    if (action === 'SCROLL') {
      const sp = parameters.start_point as number[] | undefined
      const ep = parameters.end_point as number[] | undefined
      if (!sp || !ep) return null
      const x1 = (sp[0] / 1000) * imgSize.w
      const y1 = (sp[1] / 1000) * imgSize.h
      const x2 = (ep[0] / 1000) * imgSize.w
      const y2 = (ep[1] / 1000) * imgSize.h
      return (
        <svg className="absolute inset-0 w-full h-full pointer-events-none" overflow="visible">
          <defs>
            <marker id="arrow" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
              <path d="M0,0 L0,6 L6,3 z" fill="#22d3ee" />
            </marker>
          </defs>
          <circle cx={x1} cy={y1} r="6" fill="#22d3ee" opacity="0.8" />
          <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="#22d3ee" strokeWidth="2"
            markerEnd="url(#arrow)" opacity="0.8" />
          <circle cx={x2} cy={y2} r="6" fill="#f59e0b" opacity="0.8" />
        </svg>
      )
    }

    return null
  }

  return (
    <div className="flex flex-col h-full bg-gray-900 rounded border border-gray-800">
      <div className="px-3 py-2 text-xs text-gray-500 border-b border-gray-800 font-semibold uppercase tracking-wider">
        Screen
      </div>
      <div className="flex-1 flex items-center justify-center p-2 overflow-hidden">
        {screenshot ? (
          <div className="relative inline-block max-w-full max-h-full">
            <img
              ref={imgRef}
              src={screenshot}
              alt="device screen"
              className="max-w-full max-h-full object-contain rounded"
              onLoad={onLoad}
            />
            {renderOverlay()}
          </div>
        ) : (
          <div className="text-gray-600 text-sm text-center">
            <div className="text-4xl mb-2">📱</div>
            <div>No screenshot yet</div>
          </div>
        )}
      </div>
    </div>
  )
}
