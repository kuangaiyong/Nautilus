import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { ArrowLeft, Plus, AlertCircle, CheckCircle } from 'lucide-react'

type TaskType = 'CODE' | 'DATA' | 'COMPUTE'

export default function CreateTaskPage() {
  const navigate = useNavigate()
  const { user, token } = useAuth()
  const [formData, setFormData] = useState({
    description: '',
    requirements: '',
    reward: '',
    task_type: 'CODE' as TaskType,
    timeout: '3600'
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      if (!formData.description.trim()) throw new Error('请输入任务描述')
      if (!formData.requirements.trim()) throw new Error('请输入任务要求')
      if (!formData.reward || parseFloat(formData.reward) <= 0) throw new Error('请输入有效的奖励金额')

      const rewardInWei = Math.floor(parseFloat(formData.reward) * 1e18).toString()
      const res = await fetch('/api/tasks', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'Authorization': `Bearer ${token}` } : {})
        },
        body: JSON.stringify({
          description: formData.description,
          input_data: formData.requirements,
          expected_output: null,
          reward: parseInt(rewardInWei),
          task_type: formData.task_type,
          timeout: parseInt(formData.timeout)
        })
      })
      if (!res.ok) {
        const d = await res.json()
        throw new Error(d.detail || '提交失败')
      }
      const data = await res.json()
      setSuccess(true)
      setTimeout(() => navigate(`/tasks/${data.data?.id || data.id}`), 2000)
    } catch (err: any) {
      setError(err.message || '创建任务失败')
    } finally {
      setLoading(false)
    }
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    setFormData({ ...formData, [e.target.name]: e.target.value })
  }

  if (!user) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50 to-purple-50 flex items-center justify-center p-4">
        <div className="bg-white rounded-lg shadow-lg p-8 max-w-md w-full text-center">
          <AlertCircle className="w-16 h-16 text-yellow-500 mx-auto mb-4" />
          <h2 className="text-2xl font-bold mb-2">需要登录</h2>
          <p className="text-gray-600 mb-6">请先登录后再发布任务</p>
          <button onClick={() => navigate('/login')} className="w-full bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700">
            前往登录
          </button>
        </div>
      </div>
    )
  }

  if (success) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50 to-purple-50 flex items-center justify-center p-4">
        <div className="bg-white rounded-lg shadow-lg p-8 max-w-md w-full text-center">
          <CheckCircle className="w-16 h-16 text-green-500 mx-auto mb-4" />
          <h2 className="text-2xl font-bold mb-2">任务已发布！</h2>
          <p className="text-gray-600">正在跳转到任务详情…</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-purple-50 py-8 px-4">
      <div className="max-w-3xl mx-auto">
        <div className="mb-8">
          <button onClick={() => navigate('/tasks')} className="flex items-center text-gray-600 hover:text-gray-900 mb-4">
            <ArrowLeft className="w-5 h-5 mr-2" />返回任务列表
          </button>
          <h1 className="text-3xl font-bold">发布新任务</h1>
          <p className="text-gray-600 mt-2">向 Nautilus 网络发布任务，由 AI 智能体完成</p>
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
              <label className="block text-sm font-medium text-gray-700 mb-2">任务类型 *</label>
              <select name="task_type" value={formData.task_type} onChange={handleChange} className="w-full px-4 py-2 border rounded-lg text-gray-900 bg-white" required>
                <option value="CODE">代码执行（CODE）</option>
                <option value="DATA">数据处理（DATA）</option>
                <option value="COMPUTE">计算任务（COMPUTE）</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">任务描述 *</label>
              <textarea
                name="description"
                value={formData.description}
                onChange={handleChange}
                rows={3}
                className="w-full px-4 py-2 border rounded-lg text-gray-900"
                placeholder="简要描述你的任务…"
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">详细要求 *</label>
              <textarea
                name="requirements"
                value={formData.requirements}
                onChange={handleChange}
                rows={8}
                className="w-full px-4 py-2 border rounded-lg font-mono text-sm text-gray-900"
                placeholder="详细描述输入、处理步骤与期望输出"
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">奖励（华币）*</label>
              <input
                type="number"
                name="reward"
                value={formData.reward}
                onChange={handleChange}
                step="0.001"
                min="0.001"
                className="w-full px-4 py-2 border rounded-lg text-gray-900"
                placeholder="100"
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">超时时间</label>
              <select name="timeout" value={formData.timeout} onChange={handleChange} className="w-full px-4 py-2 border rounded-lg text-gray-900 bg-white">
                <option value="300">5 分钟</option>
                <option value="600">10 分钟</option>
                <option value="1800">30 分钟</option>
                <option value="3600">1 小时</option>
                <option value="7200">2 小时</option>
              </select>
            </div>

            <div className="flex gap-4 pt-4">
              <button type="button" onClick={() => navigate('/tasks')} className="flex-1 px-6 py-3 border text-gray-700 rounded-lg" disabled={loading}>
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
                    <Plus className="w-5 h-5 mr-2" />发布任务
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
