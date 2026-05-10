import type { Device } from '../api/client'

interface Props {
  devices: Device[]
  selected: string | null
  onSelect: (serial: string) => void
}

export default function DevicePanel({ devices, selected, onSelect }: Props) {
  return (
    <div className="bg-gray-900 rounded border border-gray-800">
      <div className="px-3 py-2 text-xs text-gray-500 border-b border-gray-800 font-semibold uppercase tracking-wider">
        Device
      </div>
      {devices.length === 0 ? (
        <div className="p-3 text-xs text-gray-600">No devices found. Connect a device or start an emulator.</div>
      ) : (
        <div className="divide-y divide-gray-800">
          {devices.map((d) => (
            <button
              key={d.serial}
              onClick={() => onSelect(d.serial)}
              className={`w-full text-left px-3 py-2 text-xs flex items-center gap-2 hover:bg-gray-800 transition-colors ${
                selected === d.serial ? 'bg-gray-800' : ''
              }`}
            >
              <span className="w-2 h-2 rounded-full bg-green-500 shrink-0" />
              <span className="font-mono text-gray-200 flex-1">{d.serial}</span>
              <span className="text-gray-500">{d.status}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
