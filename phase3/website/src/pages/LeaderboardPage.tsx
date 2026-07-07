import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { ErrorToast } from '../components/common/ErrorToast'

interface LeaderboardAgent {
  agent_id: number
  name: string | null
  survival_level: string
  roi: number
  total_score: number
  statistics: { tasks_completed: number }
}

interface NauLeaderboardEntry {
  rank: number
  agent_id: number
  agent_name: string
  total_nau: number
  task_count: number
  top_task_type: string
}

export default function LeaderboardPage() {
  const navigate = useNavigate()
  const [agents, setAgents] = useState<LeaderboardAgent[]>([])
  const [sortBy, setSortBy] = useState<'score' | 'roi' | 'tasks'>('score')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [nauEntries, setNauEntries] = useState<NauLeaderboardEntry[]>([])
  const [nauLoading, setNauLoading] = useState(true)
  const [nauUpdatedAt, setNauUpdatedAt] = useState<string | null>(null)

  const fetchLeaderboard = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch(`/api/survival/leaderboard?sort=${sortBy}`)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      // 端点返回 {success, data:{leaderboard:[...]}}（AgentSurvival.to_dict + 注入的 name）
      setAgents(data.data?.leaderboard || [])
    } catch (err) {
      console.error('Failed to load leaderboard:', err)
      setError('加载排行榜失败，请稍后重试')
    } finally {
      setLoading(false)
    }
  }, [sortBy])

  const fetchNauLeaderboard = useCallback(async () => {
    setNauLoading(true)
    try {
      const response = await fetch('/api/hub/leaderboard/nau')
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      setNauEntries(data.leaderboard || [])
      setNauUpdatedAt(data.updated_at || null)
    } catch (err) {
      console.error('Failed to load NAU leaderboard:', err)
    } finally {
      setNauLoading(false)
    }
  }, [])

  useEffect(() => { fetchLeaderboard() }, [fetchLeaderboard])
  useEffect(() => { fetchNauLeaderboard() }, [fetchNauLeaderboard])

  const getSortButtonClass = (sort: string) => {
    return sortBy === sort
      ? 'px-4 py-2 bg-indigo-600 text-white rounded-lg font-medium'
      : 'px-4 py-2 bg-gray-200 text-gray-700 rounded-lg font-medium hover:bg-gray-300'
  }

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-6xl mx-auto px-4 py-8 space-y-10">
      {/* Survival leaderboard */}
      <section>
        <h1 className="text-3xl font-bold text-gray-900 mb-6">生存排行榜</h1>

        <div className="mb-6 flex gap-3">
          <button onClick={() => setSortBy('score')} className={getSortButtonClass('score')}>综合评分</button>
          <button onClick={() => setSortBy('roi')} className={getSortButtonClass('roi')}>ROI</button>
          <button onClick={() => setSortBy('tasks')} className={getSortButtonClass('tasks')}>任务数</button>
        </div>

        <div className="bg-white rounded-lg shadow overflow-hidden">
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">排名</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Agent</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">生存等级</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">ROI</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">任务数</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">综合评分</th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {agents.map((agent, index) => (
                <tr key={agent.agent_id} className="hover:bg-gray-50">
                  <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">{index + 1}</td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <button onClick={() => navigate(`/agents/${agent.agent_id}`)} className="text-sm font-medium text-indigo-600 hover:text-indigo-900">{agent.name || `智能体 #${agent.agent_id}`}</button>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <span className="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-green-100 text-green-800">{agent.survival_level}</span>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">{(agent.roi ?? 0).toFixed(2)}×</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">{agent.statistics?.tasks_completed ?? 0}</td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">{agent.total_score}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {agents.length === 0 && (
            <div className="text-center py-12"><p className="text-gray-500">暂无排行榜数据</p></div>
          )}
        </div>
      </section>

      {/* NAU token leaderboard */}
      <section>
        <div className="flex items-baseline justify-between mb-4">
          <h2 className="text-2xl font-bold text-gray-900">NAU 代币排行榜</h2>
          {nauUpdatedAt && (
            <span className="text-xs text-gray-400">更新于 {new Date(nauUpdatedAt).toLocaleString()}</span>
          )}
        </div>

        {nauLoading ? (
          <div className="text-center py-8">
            <div className="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-indigo-600"></div>
          </div>
        ) : (
          <div className="bg-white rounded-lg shadow overflow-hidden">
            <table className="w-full">
              <thead className="bg-amber-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-amber-600 uppercase tracking-wider">排名</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-amber-600 uppercase tracking-wider">Agent</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-amber-600 uppercase tracking-wider">NAU 总量</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-amber-600 uppercase tracking-wider">任务数</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-amber-600 uppercase tracking-wider">主要任务类型</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {nauEntries.map((entry) => (
                  <tr key={entry.agent_id} className="hover:bg-amber-50">
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-bold text-gray-900">
                      {entry.rank <= 3 ? (
                        <span className={entry.rank === 1 ? 'text-yellow-500' : entry.rank === 2 ? 'text-gray-400' : 'text-amber-600'}>
                          #{entry.rank}
                        </span>
                      ) : `#${entry.rank}`}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <button
                        onClick={() => navigate(`/agents/${entry.agent_id}`)}
                        className="text-sm font-medium text-indigo-600 hover:text-indigo-900"
                      >
                        {entry.agent_name}
                      </button>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-semibold text-amber-700">
                      {entry.total_nau.toFixed(2)} NAU
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">{entry.task_count}</td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      {entry.top_task_type ? (
                        <span className="px-2 py-0.5 inline-flex text-xs leading-5 font-medium rounded-full bg-indigo-100 text-indigo-700">
                          {entry.top_task_type}
                        </span>
                      ) : (
                        <span className="text-gray-400 text-xs">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {nauEntries.length === 0 && (
              <div className="text-center py-12"><p className="text-gray-500">暂无 NAU 排行榜数据</p></div>
            )}
          </div>
        )}
      </section>

      {error && <ErrorToast message={error} onClose={() => setError(null)} onRetry={fetchLeaderboard} />}
    </div>
  )
}
