import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import Chat from './pages/Chat'
import Devices from './pages/Devices'
import Memory from './pages/Memory'

function Nav() {
  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `px-4 py-2 text-sm font-medium transition-colors ${
      isActive ? 'text-cyan-400 border-b-2 border-cyan-400' : 'text-gray-400 hover:text-gray-200'
    }`
  return (
    <nav className="flex items-center gap-1 px-4 h-12 bg-gray-900 border-b border-gray-800 shrink-0">
      <span className="text-cyan-500 font-bold mr-6 text-sm tracking-wider">GUI AGENT</span>
      <NavLink to="/" end className={linkClass}>Chat</NavLink>
      <NavLink to="/memory" className={linkClass}>Memory</NavLink>
      <NavLink to="/devices" className={linkClass}>Devices</NavLink>
    </nav>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex flex-col h-screen overflow-hidden">
        <Nav />
        <div className="flex-1 overflow-hidden">
          <Routes>
            <Route path="/" element={<Chat />} />
            <Route path="/memory" element={<Memory />} />
            <Route path="/devices" element={<Devices />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  )
}
