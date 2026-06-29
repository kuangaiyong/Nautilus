import { useEffect, useState, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { tokenUtils } from '../utils/token'

const API_URL = import.meta.env.VITE_API_URL || ''

interface MintResult {
  tx_hash: string
  to_address: string
  amount: number
}

// 管理员发币页：华币由公司统一发行（合约 onlyOwner 铸造），此页是面向管理员的
// 发放入口。后端用合约 owner 私钥代签铸币并广播到私有链。仅 is_admin 可见。
export default function AdminMintPage() {
  const navigate = useNavigate()
  const token = tokenUtils.get()

  const [isAdmin, setIsAdmin] = useState<boolean | null>(null)
  const [target, setTarget] = useState('')
  const [amount, setAmount] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<MintResult | null>(null)

  useEffect(() => {
    if (!token) { navigate('/login'); return }
    fetch(`${API_URL}/api/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => r.json())
      .then((d) => setIsAdmin(!!d?.data?.user?.is_admin))
      .catch(() => setIsAdmin(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const mint = async (e: FormEvent) => {
    e.preventDefault()
    setSending(true); setError(''); setResult(null)
    try {
      const resp = await fetch(`${API_URL}/api/wallets/mint`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ target: target.trim(), amount: parseFloat(amount) }),
      })
      const data = await resp.json()
      if (!resp.ok) {
        setError(data.detail?.error?.message || data.detail || '铸币失败')
        return
      }
      const h: string = data.tx_hash
      setResult({ ...data, tx_hash: h.startsWith('0x') ? h : `0x${h}` })
      setTarget(''); setAmount('')
    } catch (e: any) {
      setError(e.message || '网络错误，请确认后端服务已启动')
    } finally { setSending(false) }
  }

  const wrap = 'min-h-screen bg-gradient-to-br from-slate-50 to-indigo-100 py-12 px-4'

  if (isAdmin === null) {
    return <div className={wrap}><div className="max-w-xl mx-auto text-center text-gray-500">校验权限中…</div></div>
  }
  if (!isAdmin) {
    return (
      <div className={wrap}>
        <div className="max-w-xl mx-auto bg-white rounded-xl shadow-lg p-8 text-center">
          <h1 className="text-2xl font-bold text-gray-900 mb-2">无权限</h1>
          <p className="text-gray-600">仅管理员可发放华币。</p>
        </div>
      </div>
    )
  }

  return (
    <div className={wrap}>
      <div className="max-w-xl mx-auto space-y-6">
        <div className="text-center">
          <h1 className="text-3xl font-bold text-gray-900 mb-2">发放华币</h1>
          <p className="text-gray-600">管理员铸币 · 1 华币 = 1 元 · 直接发放到用户钱包</p>
        </div>

        {error && (
          <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-red-700">{error}</div>
        )}

        <form onSubmit={mint} className="bg-white rounded-xl shadow-lg p-6 space-y-4">
          <div>
            <label className="block text-sm text-gray-600 mb-1">接收方（用户名或 0x 地址）</label>
            <input
              className="w-full py-3 px-4 rounded-lg border border-gray-300 focus:outline-none focus:border-indigo-500 text-gray-900"
              placeholder="例如 alice 或 0x..."
              value={target} onChange={(e) => setTarget(e.target.value)} required
            />
          </div>
          <div>
            <label className="block text-sm text-gray-600 mb-1">数量（华币）</label>
            <input
              className="w-full py-3 px-4 rounded-lg border border-gray-300 focus:outline-none focus:border-indigo-500 text-gray-900"
              type="number" step="any" min="0" placeholder="例如 1000"
              value={amount} onChange={(e) => setAmount(e.target.value)} required
            />
          </div>
          <button
            type="submit" disabled={sending}
            className="w-full py-3 bg-indigo-600 text-white rounded-lg font-semibold hover:bg-indigo-700 disabled:opacity-50"
          >
            {sending ? '发放中…' : '确认发放'}
          </button>
          {result && (
            <div className="rounded-lg bg-green-50 border border-green-200 p-3 text-sm text-green-800 space-y-1">
              <div>已向 <span className="font-mono break-all">{result.to_address}</span> 发放 {result.amount} 华币</div>
              <div>交易哈希：<span className="font-mono break-all">{result.tx_hash}</span></div>
            </div>
          )}
        </form>
      </div>
    </div>
  )
}
