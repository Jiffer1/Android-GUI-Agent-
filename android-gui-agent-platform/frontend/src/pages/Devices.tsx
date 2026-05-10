import { useEffect, useState } from 'react'
import { devicesApi, type Device } from '../api/client'

export default function Devices() {
  const [devices, setDevices] = useState<Device[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    devicesApi.list()
      .then((r) => setDevices(r.data.devices))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  return (
    <div className="p-4 max-w-2xl mx-auto">
      <div className="bg-gray-900 rounded border border-gray-800">
        <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">ADB Devices</h2>
          <button onClick={load} className="text-xs text-gray-500 hover:text-gray-300">Refresh</button>
        </div>
        {loading ? (
          <div className="p-8 text-center text-gray-600 text-sm">Loading…</div>
        ) : error ? (
          <div className="p-4 text-red-400 text-sm">{error}</div>
        ) : devices.length === 0 ? (
          <div className="p-8 text-center text-gray-600 text-sm">
            <div className="text-3xl mb-2">📵</div>
            <div>No devices found.</div>
            <div className="text-xs mt-1 text-gray-700">Connect a device via USB or start an emulator, then run <code className="bg-gray-800 px-1 rounded">adb devices</code>.</div>
          </div>
        ) : (
          <div className="divide-y divide-gray-800">
            {devices.map((d) => (
              <div key={d.serial} className="px-4 py-3 flex items-center gap-3">
                <span className="w-2 h-2 rounded-full bg-green-500 shrink-0" />
                <span className="font-mono text-sm text-gray-200 flex-1">{d.serial}</span>
                <span className="text-xs text-green-500">{d.status}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
