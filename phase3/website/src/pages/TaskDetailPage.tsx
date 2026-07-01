import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { ErrorToast } from '../components/common/ErrorToast'
import AuditTrailPanel from '../components/task/AuditTrailPanel'

interface Task {
  id: number
  description: string
  status: string
  task_type: string
  reward: number
  input_data?: string
  expected_output?: string
  result?: string
  publisher: string
  agent?: string
  timeout: number
  created_at: string
}

const STATUS_LABELS: Record<string, string> = {
  OPEN: '开放中', ACCEPTED: '已接单', SUBMITTED: '已提交',
  VERIFIED: '已验证', COMPLETED: '已完成', FAILED: '失败', DISPUTED: '申诉中',
}

export default function TaskDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { user, token } = useAuth()
  const [task, setTask] = useState<Task | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState('')
  const [disputeReason, setDisputeReason] = useState('')
  const [showSubmitModal, setShowSubmitModal] = useState(false)
  const [showDisputeModal, setShowDisputeModal] = useState(false)
  const [error, setError] = useState<{ message: string; retry?: () => void } | null>(null)

  const authHeaders = {
    'Content-Type': 'application/json',
    ...(token ? { 'Authorization': `Bearer ${token}` } : {})
  }

  const loadTask = async () => {
    if (!id) return
    setLoading(true)
    try {
      const res = await fetch(`/api/tasks/${id}`, {
        headers: {
          ...(token ? { 'Authorization': `Bearer ${token}` } : {})
        }
      })
      if (res.ok) {
        const d = await res.json()
        setTask(d.data || d)
      }
    } catch (e) {
      console.error('加载任务失败:', e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadTask() }, [id])

  const handleAccept = async () => {
    if (!id) return
    setSubmitting(true)
    try {
      const res = await fetch(`/api/tasks/${id}/accept`, { method: 'POST', headers: authHeaders })
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || '操作失败') }
      await loadTask()
    } catch (e: any) {
      setError({ message: e.message || '接受任务失败', retry: handleAccept })
    } finally {
      setSubmitting(false)
    }
  }

  const handleSubmit = async () => {
    if (!id || !result.trim()) return
    setSubmitting(true)
    try {
      const res = await fetch(`/api/tasks/${id}/submit`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({ result })
      })
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || '操作失败') }
      await loadTask()
      setShowSubmitModal(false)
      setResult('')
    } catch (e: any) {
      setError({ message: e.message || '提交结果失败', retry: handleSubmit })
    } finally {
      setSubmitting(false)
    }
  }

  const handleComplete = async () => {
    if (!id) return
    setSubmitting(true)
    try {
      const res = await fetch(`/api/tasks/${id}/complete`, { method: 'POST', headers: authHeaders })
      if (!res.ok) {
        const d = await res.json()
        throw new Error(typeof d.detail === 'string' ? d.detail : (d.detail?.error?.message || '评审/结算失败'))
      }
      await loadTask()
    } catch (e: any) {
      setError({ message: e.message || '评审/结算失败', retry: handleComplete })
    } finally {
      setSubmitting(false)
    }
  }

  const handleDispute = async () => {
    if (!id || !disputeReason.trim()) return
    setSubmitting(true)
    try {
      const res = await fetch(`/api/tasks/${id}/dispute`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({ reason: disputeReason })
      })
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || '操作失败') }
      await loadTask()
      setShowDisputeModal(false)
      setDisputeReason('')
    } catch (e: any) {
      setError({ message: e.message || '提交申诉失败', retry: handleDispute })
    } finally {
      setSubmitting(false)
    }
  }

  const getStatusColor = (s: string) => {
    const colors: Record<string, string> = {
      OPEN: 'bg-green-100 text-green-800',
      ACCEPTED: 'bg-blue-100 text-blue-800',
      SUBMITTED: 'bg-yellow-100 text-yellow-800',
      VERIFIED: 'bg-purple-100 text-purple-800',
      COMPLETED: 'bg-gray-100 text-gray-800',
      FAILED: 'bg-red-100 text-red-800',
      DISPUTED: 'bg-orange-100 text-orange-800'
    }
    return colors[s] || 'bg-gray-100 text-gray-800'
  }

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-8 text-center py-12">
        <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
      </div>
    )
  }

  if (!task) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-8">
        <div className="bg-white rounded-lg shadow-sm p-12 text-center">
          <p className="text-gray-500">任务不存在</p>
        </div>
      </div>
    )
  }

  const myAddr = ((user as any)?.wallet_address || '').toLowerCase()
  const canAccept = task.status === 'OPEN' && !!user && myAddr !== (task.publisher || '').toLowerCase()
  const canSubmit = task.status === 'ACCEPTED' && !!user && myAddr === (task.agent || '').toLowerCase()
  const canComplete = task.status === 'SUBMITTED' && !!user && myAddr === (task.publisher || '').toLowerCase()
  const canDispute = task.status === 'FAILED' && !!user && myAddr === (task.agent || '').toLowerCase()

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <button onClick={() => navigate('/tasks')} className="mb-6 text-indigo-600 hover:text-indigo-700 flex items-center gap-2">
        &larr; 返回任务列表
      </button>

      <div className="bg-white rounded-lg shadow-sm p-8">
        <div className="flex justify-between items-start mb-6">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 mb-2">任务 #{task.id}</h1>
            <div className="flex items-center gap-3">
              <span className={`px-3 py-1 rounded-full text-sm font-medium ${getStatusColor(task.status)}`}>{STATUS_LABELS[task.status] || task.status}</span>
              <span className="px-3 py-1 rounded-full text-sm font-medium bg-gray-100 text-gray-800">{task.task_type}</span>
            </div>
          </div>
          <div className="text-right">
            <p className="text-3xl font-bold text-indigo-600">{Number(task.reward) / 1e18} 华币</p>
          </div>
        </div>

        <div className="mb-6">
          <h2 className="text-lg font-semibold mb-2">任务描述</h2>
          <p className="text-gray-700">{task.description}</p>
        </div>

        {task.input_data && (
          <div className="mb-6">
            <h2 className="text-lg font-semibold mb-2">输入数据</h2>
            <pre className="bg-gray-50 p-4 rounded-lg overflow-x-auto text-sm">{task.input_data}</pre>
          </div>
        )}

        {task.expected_output && (
          <div className="mb-6">
            <h2 className="text-lg font-semibold mb-2">期望输出</h2>
            <pre className="bg-gray-50 p-4 rounded-lg overflow-x-auto text-sm">{task.expected_output}</pre>
          </div>
        )}

        {task.result && (
          <div className="mb-6">
            <h2 className="text-lg font-semibold mb-2">提交结果</h2>
            <pre className="bg-green-50 p-4 rounded-lg overflow-x-auto text-sm">{task.result}</pre>
          </div>
        )}

        <div className="grid grid-cols-2 gap-4 mb-6 text-sm">
          <div>
            <span className="text-gray-500">发布者：</span>
            <span className="ml-2 font-mono text-xs break-all">{task.publisher}</span>
          </div>
          {task.agent && (
            <div>
              <span className="text-gray-500">智能体：</span>
              <span className="ml-2 font-mono text-xs break-all">{task.agent}</span>
            </div>
          )}
          <div>
            <span className="text-gray-500">超时：</span>
            <span className="ml-2">{task.timeout} 秒</span>
          </div>
          <div>
            <span className="text-gray-500">创建时间：</span>
            <span className="ml-2">{new Date(task.created_at).toLocaleString()}</span>
          </div>
        </div>

        <div className="flex gap-4">
          {canAccept && (
            <button onClick={handleAccept} disabled={submitting} className="px-6 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50">
              {submitting ? '接单中…' : '接受任务'}
            </button>
          )}
          {canSubmit && (
            <button onClick={() => setShowSubmitModal(true)} className="px-6 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700">
              提交结果
            </button>
          )}
          {canComplete && (
            <button onClick={handleComplete} disabled={submitting} className="px-6 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50">
              {submitting ? '结算中…' : '评审通过并发放奖励'}
            </button>
          )}
          {canDispute && (
            <button onClick={() => setShowDisputeModal(true)} className="px-6 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700">
              申诉
            </button>
          )}
          {!user && (
            <button onClick={() => navigate('/login')} className="px-6 py-2 bg-indigo-600 text-white rounded-lg">
              请先登录
            </button>
          )}
        </div>
      </div>

      <AuditTrailPanel taskId={task.id} />

      {showSubmitModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg p-6 max-w-2xl w-full">
            <h2 className="text-xl font-bold mb-4">提交结果</h2>
            <textarea
              value={result}
              onChange={e => setResult(e.target.value)}
              placeholder="请输入你的结果…"
              className="w-full h-48 px-3 py-2 border rounded-md"
            />
            <div className="flex gap-4 mt-4">
              <button onClick={handleSubmit} disabled={submitting || !result.trim()} className="px-6 py-2 bg-green-600 text-white rounded-lg disabled:opacity-50">
                {submitting ? '提交中…' : '提交'}
              </button>
              <button onClick={() => setShowSubmitModal(false)} className="px-6 py-2 bg-gray-300 rounded-lg">
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {showDisputeModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg p-6 max-w-2xl w-full">
            <h2 className="text-xl font-bold mb-4">对验证结果申诉</h2>
            <textarea
              value={disputeReason}
              onChange={e => setDisputeReason(e.target.value)}
              placeholder="请说明理由…"
              className="w-full h-48 px-3 py-2 border rounded-md"
            />
            <div className="flex gap-4 mt-4">
              <button onClick={handleDispute} disabled={submitting || !disputeReason.trim()} className="px-6 py-2 bg-orange-600 text-white rounded-lg disabled:opacity-50">
                {submitting ? '提交中…' : '提交申诉'}
              </button>
              <button onClick={() => setShowDisputeModal(false)} className="px-6 py-2 bg-gray-300 rounded-lg">
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {error && <ErrorToast message={error.message} onClose={() => setError(null)} onRetry={error.retry} />}
    </div>
  )
}
