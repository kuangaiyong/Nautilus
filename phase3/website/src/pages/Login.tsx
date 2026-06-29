import { useState, FormEvent } from 'react'
import { tokenUtils } from '../utils/token'

const API_URL = import.meta.env.VITE_API_URL || ''

type Region = null | 'cn' | 'intl'

const Login = () => {
  const [region, setRegion] = useState<Region>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  const credentialAuth = async (e: FormEvent) => {
    e.preventDefault()
    setLoading(true); setError('')
    try {
      const path = mode === 'register' ? '/api/auth/register' : '/api/auth/login'
      const body = mode === 'register' ? { username, email, password } : { username, password }
      const resp = await fetch(`${API_URL}${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await resp.json()
      if (!resp.ok) { setError(data.detail || data.error || '登录失败，请检查用户名和密码'); return }
      const token = data.data?.access_token || data.access_token
      if (token) { tokenUtils.save(token, true); window.location.href = '/' }
      else setError('登录失败：服务端未返回令牌')
    } catch (err: any) {
      setError(err.message || '网络错误，请确认后端服务已启动')
    } finally { setLoading(false) }
  }

  const btn = 'w-full flex items-center justify-center gap-2 py-3 px-6 rounded-xl text-white transition font-medium'
  const input = 'w-full py-3 px-4 rounded-xl bg-white/10 border border-white/20 text-white placeholder-gray-400 focus:outline-none focus:border-blue-400'

  if (!region) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-gray-900 via-blue-900 to-purple-900 py-12 px-4">
        <div className="max-w-md w-full space-y-5 bg-white/10 backdrop-blur-lg border border-white/20 p-8 rounded-xl shadow-xl">
          <div className="text-center mb-4">
            <h2 className="text-3xl font-extrabold text-white">Nautilus</h2>
            <p className="mt-2 text-gray-300">AI 智能体平台</p>
          </div>
          <button onClick={() => setRegion('cn')} className={btn + ' bg-blue-600 hover:bg-blue-700 text-lg py-4'}>
            🇨🇳 中国大陆用户
          </button>
          <button onClick={() => setRegion('intl')} className={btn + ' bg-purple-600 hover:bg-purple-700 text-lg py-4'}>
            🌏 International Users
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-gray-900 via-blue-900 to-purple-900 py-12 px-4">
      <div className="max-w-md w-full space-y-4 bg-white/10 backdrop-blur-lg border border-white/20 p-8 rounded-xl shadow-xl">
        <div className="text-center mb-2">
          <h2 className="text-3xl font-extrabold text-white">Nautilus</h2>
          <p className="mt-1 text-gray-300 text-sm">
            {region === 'cn' ? 'AI Agent 平台 — 登录即创建账户' : 'AI Agent Platform — Login to get started'}
          </p>
        </div>

        {error && (
          <div className="rounded-md bg-red-500/20 border border-red-400/50 p-3">
            <p className="text-sm text-red-200">{error}</p>
          </div>
        )}

        <form onSubmit={credentialAuth} className="space-y-3">
          <input className={input} type="text" placeholder="用户名 / Username" autoComplete="username"
            value={username} onChange={(e) => setUsername(e.target.value)} required />
          {mode === 'register' && (
            <input className={input} type="email" placeholder="邮箱 / Email" autoComplete="email"
              value={email} onChange={(e) => setEmail(e.target.value)} required />
          )}
          <input className={input} type="password" placeholder="密码 / Password"
            autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
            value={password} onChange={(e) => setPassword(e.target.value)} required />
          <button type="submit" disabled={loading} className={btn + ' bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-lg py-4'}>
            {loading ? '处理中...' : mode === 'register' ? '注册并登录' : '账号密码登录'}
          </button>
          <button type="button" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError('') }}
            className="w-full text-center text-xs text-gray-400 hover:text-white transition">
            {mode === 'login' ? '没有账号？点此注册' : '已有账号？返回登录'}
          </button>
        </form>

        <button onClick={() => { setRegion(null); setError('') }}
          className="w-full text-center text-xs text-gray-400 hover:text-white transition pt-2">
          {region === 'cn' ? '切换到国际版 / Switch' : 'Switch to 中国大陆版'}
        </button>
        <p className="text-center text-xs text-gray-500">首次登录自动注册</p>
      </div>
    </div>
  )
}

export default Login
