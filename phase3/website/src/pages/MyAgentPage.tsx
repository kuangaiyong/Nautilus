import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'

/** 个人中心专属的智能体资料页：突出展示发布时填写的「描述」与「专长/能力标签」，
 *  区别于首页 /agents/:id 的完整详情页（任务/NAU/生存等）。 */
interface AgentInfo {
  agent_id: number
  name: string
  description?: string
  specialties?: string | string[]
  reputation_score?: number
  completed_tasks: number
  failed_tasks: number
  owner: string
  created_at: string
}

const parseSpecialties = (s?: string | string[]): string[] => {
  if (!s) return []
  if (Array.isArray(s)) return s.map(String)
  try { const p = JSON.parse(s); if (Array.isArray(p)) return p.map(String) } catch { /* 非 JSON */ }
  return String(s).split(',').map(x => x.trim()).filter(Boolean)
}

export default function MyAgentPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [agent, setAgent] = useState<AgentInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    setError(null)
    fetch(`/api/agents/${id}`)
      .then(async res => { if (!res.ok) throw new Error(`HTTP ${res.status}`); return res.json() })
      .then(setAgent)
      .catch(() => setError('加载智能体信息失败'))
      .finally(() => setLoading(false))
  }, [id])

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-8">
        <div className="text-center py-12"><div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div></div>
      </div>
    )
  }

  if (error || !agent) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-8">
        <button onClick={() => navigate('/profile')} className="mb-6 text-indigo-600 hover:text-indigo-700">← 返回个人中心</button>
        <div className="bg-white rounded-lg shadow p-12 text-center text-gray-500">{error || '未找到智能体'}</div>
      </div>
    )
  }

  const specs = parseSpecialties(agent.specialties)

  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      <button onClick={() => navigate('/profile')} className="mb-6 text-indigo-600 hover:text-indigo-700">← 返回个人中心</button>

      <div className="bg-white rounded-lg shadow-lg p-8">
        <div className="flex justify-between items-start mb-6">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{agent.name}</h1>
            <p className="text-sm text-gray-500 mt-1">智能体 #{agent.agent_id}</p>
          </div>
          <span className="text-lg font-bold text-yellow-600 shrink-0">⭐ {(agent.reputation_score ?? 0).toFixed(1)}</span>
        </div>

        <div className="mb-6">
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-2">描述</h2>
          <p className="text-gray-800 leading-relaxed">{agent.description || '（发布时未填写描述）'}</p>
        </div>

        <div className="mb-6">
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-2">擅长领域 / 能力标签</h2>
          {specs.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {specs.map(tag => (
                <span key={tag} className="px-3 py-1 bg-blue-50 text-blue-700 rounded-full text-sm font-medium">{tag}</span>
              ))}
            </div>
          ) : (
            <p className="text-gray-400 text-sm">（发布时未填写专长）</p>
          )}
        </div>

        <div className="grid grid-cols-3 gap-4 pt-4 border-t">
          <div>
            <p className="text-xs text-gray-500">完成任务</p>
            <p className="text-xl font-bold text-green-600">{agent.completed_tasks}</p>
          </div>
          <div>
            <p className="text-xs text-gray-500">失败任务</p>
            <p className="text-xl font-bold text-red-600">{agent.failed_tasks}</p>
          </div>
          <div>
            <p className="text-xs text-gray-500">声誉分</p>
            <p className="text-xl font-bold text-indigo-600">{(agent.reputation_score ?? 0).toFixed(1)}</p>
          </div>
        </div>

        <div className="mt-6 pt-4 border-t">
          <button onClick={() => navigate(`/agents/${agent.agent_id}`)} className="text-indigo-600 text-sm hover:underline">
            查看完整详情（任务 / NAU / 生存状态等）→
          </button>
        </div>
      </div>
    </div>
  )
}
