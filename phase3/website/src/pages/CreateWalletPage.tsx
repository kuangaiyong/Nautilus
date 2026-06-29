import { useEffect, useState, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { tokenUtils } from '../utils/token'

const API_URL = import.meta.env.VITE_API_URL || ''

interface WalletOverview {
  wallet_id: string
  address: string
  eth: number
  hua: number
  nau: number
}

// 内网托管钱包：注册即自动拥有，私钥由平台保管，签名/转账由后端代办并广播到私有链。
// 本页是钱包的"资产视图"，不依赖 MetaMask / 任何浏览器扩展或公网服务。
export default function CreateWalletPage() {
  const navigate = useNavigate()
  const token = tokenUtils.get()

  const [wallet, setWallet] = useState<WalletOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [to, setTo] = useState('')
  const [amount, setAmount] = useState('')
  const [sending, setSending] = useState(false)
  const [txHash, setTxHash] = useState('')

  const loadWallet = async () => {
    setLoading(true); setError('')
    try {
      const resp = await fetch(`${API_URL}/api/wallets/me`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (resp.status === 401) { navigate('/login'); return }
      const data = await resp.json()
      if (!resp.ok) { setError(data.detail?.error?.message || '加载钱包失败'); return }
      setWallet(data)
    } catch (e: any) {
      setError(e.message || '网络错误，请确认后端服务已启动')
    } finally { setLoading(false) }
  }

  useEffect(() => {
    if (!token) { navigate('/login'); return }
    loadWallet()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const transfer = async (e: FormEvent) => {
    e.preventDefault()
    if (!wallet) return
    setSending(true); setError(''); setTxHash('')
    try {
      const resp = await fetch(`${API_URL}/api/wallets/${wallet.wallet_id}/transfer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ to_address: to, amount: parseFloat(amount) }),
      })
      const data = await resp.json()
      if (!resp.ok) {
        setError(data.detail?.error?.message || data.detail || '转账失败')
        return
      }
      const h: string = data.tx_hash
      setTxHash(h.startsWith('0x') ? h : `0x${h}`)
      setTo(''); setAmount('')
      await loadWallet()
    } catch (e: any) {
      setError(e.message || '网络错误')
    } finally { setSending(false) }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 py-12 px-4">
      <div className="max-w-2xl mx-auto space-y-6">
        <div className="text-center">
          <h1 className="text-3xl font-bold text-gray-900 mb-2">我的钱包</h1>
          <p className="text-gray-600">平台托管 · 直连内网私有链 · 华币结算</p>
        </div>

        {error && (
          <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-red-700">{error}</div>
        )}

        {loading ? (
          <div className="bg-white rounded-xl shadow-lg p-8 text-center text-gray-500">加载中…</div>
        ) : wallet ? (
          <>
            <div className="bg-white rounded-xl shadow-lg p-6 space-y-4">
              <div>
                <div className="text-sm text-gray-500 mb-1">钱包地址</div>
                <div className="font-mono text-sm break-all text-gray-900">{wallet.address}</div>
              </div>
              <div className="grid grid-cols-3 gap-4">
                <div className="bg-indigo-50 rounded-lg p-4">
                  <div className="text-sm text-indigo-700">华币 (HUA)</div>
                  <div className="text-2xl font-bold text-indigo-900">{wallet.hua}</div>
                </div>
                <div className="bg-emerald-50 rounded-lg p-4">
                  <div className="text-sm text-emerald-700">NAU</div>
                  <div className="text-2xl font-bold text-emerald-900">{wallet.nau}</div>
                </div>
                <div className="bg-gray-50 rounded-lg p-4">
                  <div className="text-sm text-gray-600">ETH (Gas)</div>
                  <div className="text-2xl font-bold text-gray-800">{wallet.eth}</div>
                </div>
              </div>
              <p className="text-xs text-gray-500 mt-3">
                华币用于结算与转账；NAU 为智能体完成任务自动获得的工作量奖励，由系统自动铸造到钱包，无需手动领取。
              </p>
            </div>

            <form onSubmit={transfer} className="bg-white rounded-xl shadow-lg p-6 space-y-4">
              <h2 className="text-xl font-bold text-gray-900">转账华币</h2>
              <input
                className="w-full py-3 px-4 rounded-lg border border-gray-300 focus:outline-none focus:border-indigo-500 font-mono text-sm text-gray-900"
                placeholder="收款地址 0x..."
                value={to} onChange={(e) => setTo(e.target.value)} required
              />
              <input
                className="w-full py-3 px-4 rounded-lg border border-gray-300 focus:outline-none focus:border-indigo-500 text-gray-900"
                type="number" step="any" min="0" placeholder="金额（华币）"
                value={amount} onChange={(e) => setAmount(e.target.value)} required
              />
              <button
                type="submit" disabled={sending}
                className="w-full py-3 bg-indigo-600 text-white rounded-lg font-semibold hover:bg-indigo-700 disabled:opacity-50"
              >
                {sending ? '转账中…' : '确认转账'}
              </button>
              {txHash && (
                <div className="rounded-lg bg-green-50 border border-green-200 p-3 text-sm text-green-800 break-all">
                  转账成功，交易哈希：<span className="font-mono">{txHash}</span>
                </div>
              )}
            </form>
          </>
        ) : null}
      </div>
    </div>
  )
}
