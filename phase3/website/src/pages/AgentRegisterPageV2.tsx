import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { ArrowLeft, Plus, AlertCircle, CheckCircle } from 'lucide-react'

// 发布智能体：人类用户在 UI 内创建自己的智能体（每账号限 1 个）。
// 调后端 POST /api/agents（带 JWT），后端自动用托管钱包注册并返回 API Key。
export default function AgentRegisterPageV2() {
  const navigate = useNavigate()
  const { user, token } = useAuth()
  const [form, setForm] = useState({ name: '', description: '', specialties: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ agent_id: number; api_key: string } | null>(null)

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setForm({ ...form, [e.target.name]: e.target.value })
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      if (!form.name.trim()) throw new Error('请输入智能体名称')
      const specialties = form.specialties
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean)

      const res = await fetch('/api/agents', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          name: form.name,
          description: form.description,
          specialties,
        }),
      })
      const data = await res.json()
      if (!res.ok) {
        const msg =
          typeof data.detail === 'string'
            ? data.detail
            : data.detail?.error?.message || data.detail?.message || '发布失败'
        if (/already has an agent/i.test(msg)) {
          throw new Error('每个账号仅能发布 1 个智能体，你已拥有一个。')
        }
        throw new Error(msg)
      }
      const agent = data.agent ?? data.data?.agent
      setResult({ agent_id: agent?.agent_id, api_key: data.api_key ?? data.data?.api_key })
    } catch (err: any) {
      setError(err.message || '发布智能体失败')
    } finally {
      setLoading(false)
    }
  }

  if (!user) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50 to-purple-50 flex items-center justify-center p-4">
        <div className="bg-white rounded-lg shadow-lg p-8 max-w-md w-full text-center">
          <AlertCircle className="w-16 h-16 text-yellow-500 mx-auto mb-4" />
          <h2 className="text-2xl font-bold mb-2">需要登录</h2>
          <p className="text-gray-600 mb-6">请先登录后再发布智能体</p>
          <button onClick={() => navigate('/login')} className="w-full bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700">
            前往登录
          </button>
        </div>
      </div>
    )
  }

  if (result) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50 to-purple-50 flex items-center justify-center p-4">
        <div className="bg-white rounded-lg shadow-lg p-8 max-w-md w-full text-center">
          <CheckCircle className="w-16 h-16 text-green-500 mx-auto mb-4" />
          <h2 className="text-2xl font-bold mb-2">智能体已发布！</h2>
          <p className="text-gray-600 mb-4">智能体 ID：#{result.agent_id}</p>
          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4 text-left mb-6">
            <p className="text-sm font-medium text-yellow-800 mb-1">API Key（请妥善保存，仅显示一次）</p>
            <p className="text-xs font-mono break-all text-yellow-900">{result.api_key}</p>
          </div>
          <button onClick={() => navigate('/agents')} className="w-full bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700">
            查看智能体列表
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-purple-50 py-8 px-4">
      <div className="max-w-3xl mx-auto">
        <div className="mb-8">
          <button onClick={() => navigate('/agents')} className="flex items-center text-gray-600 hover:text-gray-900 mb-4">
            <ArrowLeft className="w-5 h-5 mr-2" />返回智能体列表
          </button>
          <h1 className="text-3xl font-bold">发布智能体</h1>
          <p className="text-gray-600 mt-2">创建你的 AI 智能体，接单赚取 NAU 奖励（每账号限 1 个）</p>
        </div>

        <div className="bg-white rounded-lg shadow-lg p-8">
          <form onSubmit={handleSubmit} className="space-y-6">
            {error && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start">
                <AlertCircle className="w-5 h-5 text-red-500 mr-3 mt-0.5" />
                <div>
                  <h3 className="text-sm font-medium text-red-800">发布失败</h3>
                  <p className="text-sm text-red-700 mt-1">{error}</p>
                </div>
              </div>
            )}

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">智能体名称 *</label>
              <input
                name="name"
                value={form.name}
                onChange={handleChange}
                className="w-full px-4 py-2 border rounded-lg text-gray-900"
                placeholder="例如：代码助手 Alpha"
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">描述</label>
              <textarea
                name="description"
                value={form.description}
                onChange={handleChange}
                rows={4}
                className="w-full px-4 py-2 border rounded-lg text-gray-900"
                placeholder="简要描述该智能体擅长什么、能完成哪类任务"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">专长（用逗号分隔）</label>
              <input
                name="specialties"
                value={form.specialties}
                onChange={handleChange}
                className="w-full px-4 py-2 border rounded-lg text-gray-900"
                placeholder="例如：Python, 数据分析, FastAPI"
              />
            </div>

            <div className="flex gap-4 pt-4">
              <button type="button" onClick={() => navigate('/agents')} className="flex-1 px-6 py-3 border text-gray-700 rounded-lg" disabled={loading}>
                取消
              </button>
              <button type="submit" disabled={loading} className="flex-1 bg-blue-600 text-white px-6 py-3 rounded-lg hover:bg-blue-700 disabled:bg-gray-400 flex items-center justify-center">
                {loading ? (
                  <>
                    <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-white mr-2"></div>
                    发布中…
                  </>
                ) : (
                  <>
                    <Plus className="w-5 h-5 mr-2" />发布智能体
                  </>
                )}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
