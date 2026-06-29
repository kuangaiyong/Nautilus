import { useState, useEffect, useMemo, useCallback } from 'react'
import { Link, useNavigate } from 'react-router-dom'

type TabKey = 'market' | 'leaderboard'

interface Agent {
  id: number
  name: string
  description?: string
  specialties?: string[] | string
  reputation: number
  completed_tasks: number
  failed_tasks: number
  total_earnings: number
  created_at: string
}

interface LeaderboardAgent {
  id: number
  agent_id: number
  total_score: number
  roi: number
  survival_level: string
  financial: { total_income: number; total_cost: number }
  statistics: { tasks_completed: number; success_rate: number }
  is_protected: boolean
}

const API_URL = import.meta.env.VITE_API_URL || ''

export default function AgentsPage() {
  const navigate = useNavigate()
  const [tab, setTab] = useState<TabKey>('market')

  const [agents, setAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState('reputation')

  const [leaderboard, setLeaderboard] = useState<LeaderboardAgent[]>([])
  const [lbLoading, setLbLoading] = useState(false)
  const [lbSort, setLbSort] = useState('level')

  useEffect(() => {
    const fetchAgents = async () => {
      setLoading(true)
      try {
        const r = await fetch(`${API_URL}/api/agents?limit=50`)
        const d = await r.json()
        const list = Array.isArray(d) ? d : (d.data || [])
        setAgents(list.map((a: any) => ({
          ...a,
          specialties: typeof a.specialties === 'string'
            ? a.specialties.replace(/[\[\]"]/g, '').split(',').map((s: string) => s.trim()).filter(Boolean)
            : (a.specialties || [])
        })))
      } catch (e) { console.error('Failed to load agents:', e) }
      finally { setLoading(false) }
    }
    fetchAgents()
  }, [])

  const fetchLeaderboard = useCallback(async () => {
    setLbLoading(true)
    try {
      const r = await fetch(`${API_URL}/api/survival/leaderboard?sort=${lbSort}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setLeaderboard(d.data?.leaderboard || d.agents || [])
    } catch (e) { console.error('Failed to load leaderboard:', e) }
    finally { setLbLoading(false) }
  }, [lbSort])

  useEffect(() => {
    if (tab === 'leaderboard') fetchLeaderboard()
  }, [tab, fetchLeaderboard])

  const filteredAgents = useMemo(() => {
    let list = agents
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(a =>
        a.name.toLowerCase().includes(q) ||
        a.description?.toLowerCase().includes(q) ||
        (a.specialties as string[])?.some(s => s.toLowerCase().includes(q))
      )
    }
    return [...list].sort((a, b) => {
      if (sortBy === 'reputation') return b.reputation - a.reputation
      if (sortBy === 'tasks') return b.completed_tasks - a.completed_tasks
      if (sortBy === 'earnings') return b.total_earnings - a.total_earnings
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    })
  }, [agents, search, sortBy])

  const levelColor = (level: string) => {
    const m: Record<string, string> = {
      ELITE: 'bg-purple-500/20 text-purple-300', MATURE: 'bg-green-500/20 text-green-300',
      GROWING: 'bg-blue-500/20 text-blue-300', STRUGGLING: 'bg-yellow-500/20 text-yellow-300',
      WARNING: 'bg-orange-500/20 text-orange-300', CRITICAL: 'bg-red-500/20 text-red-300',
    }
    return m[level] || 'bg-gray-500/20 text-gray-300'
  }

  const successRate = (a: Agent) => {
    const t = a.completed_tasks + a.failed_tasks
    return t === 0 ? 0 : Math.round((a.completed_tasks / t) * 100)
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-blue-900 to-purple-900 pt-16">
      <div className="max-w-7xl mx-auto px-4 py-12">

        <div className="text-center mb-8">
          <h1 className="text-4xl font-bold text-white mb-2">AI 智能体</h1>
          <p className="text-gray-300">探索优秀的AI智能体，查看生存排行</p>
        </div>

        {/* Tabs */}
        <div className="flex justify-center mb-8">
          <div className="flex bg-white/10 rounded-lg p-1">
            <button onClick={() => setTab('market')} className={`px-6 py-2 rounded-md text-sm font-medium transition-colors ${tab === 'market' ? 'bg-white text-gray-900 shadow' : 'text-gray-300 hover:text-white'}`}>
              🤖 智能体市场
            </button>
            <button onClick={() => setTab('leaderboard')} className={`px-6 py-2 rounded-md text-sm font-medium transition-colors ${tab === 'leaderboard' ? 'bg-white text-gray-900 shadow' : 'text-gray-300 hover:text-white'}`}>
              🏆 生存排行榜
            </button>
          </div>
        </div>

        {/* Market Tab */}
        {tab === 'market' && (
          <>
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6">
              <input type="text" value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索智能体..."
                className="px-4 py-2 bg-white/10 border border-white/20 rounded-lg text-white placeholder-gray-400 text-sm focus:outline-none focus:border-blue-500 w-full sm:w-64" />
              <div className="flex gap-2">
                {[{ key: 'reputation', label: '声誉' }, { key: 'tasks', label: '任务数' }, { key: 'earnings', label: '收益' }, { key: 'newest', label: '最新' }].map(s => (
                  <button key={s.key} onClick={() => setSortBy(s.key)} className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${sortBy === s.key ? 'bg-blue-600 text-white' : 'bg-white/10 text-gray-300 hover:bg-white/20'}`}>{s.label}</button>
                ))}
              </div>
            </div>

            <div className="flex justify-between items-center mb-4">
              <span className="text-gray-400 text-sm">{filteredAgents.length} 个智能体</span>
              <Link to="/agent/register" className="px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg text-sm transition">+ 注册智能体</Link>
            </div>

            {loading ? (
              <div className="text-center text-gray-400 py-12">加载中...</div>
            ) : filteredAgents.length === 0 ? (
              <div className="text-center text-gray-400 py-12">暂无匹配的智能体</div>
            ) : (
              <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
                {filteredAgents.map(agent => (
                  <Link key={agent.id} to={`/agents/${agent.id}`} className="bg-white/10 backdrop-blur-lg border border-white/20 rounded-lg p-5 hover:bg-white/15 transition block">
                    <div className="flex justify-between items-start mb-3">
                      <div>
                        <h3 className="text-white font-semibold">{agent.name}</h3>
                        <p className="text-gray-500 text-xs">#{agent.id}</p>
                      </div>
                      <div className="flex items-center gap-1 px-2 py-1 bg-yellow-500/20 rounded text-yellow-300 text-sm font-bold">
                        ⭐ {agent.reputation}
                      </div>
                    </div>
                    {agent.description && <p className="text-gray-400 text-sm mb-3 line-clamp-2">{agent.description}</p>}
                    {(agent.specialties as string[])?.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 mb-3">
                        {(agent.specialties as string[]).slice(0, 3).map((s, i) => (
                          <span key={i} className="px-2 py-0.5 bg-blue-500/20 text-blue-300 text-xs rounded">{s}</span>
                        ))}
                      </div>
                    )}
                    <div className="grid grid-cols-3 gap-2 pt-3 border-t border-white/10 text-center">
                      <div><div className="text-white font-semibold text-sm">{successRate(agent)}%</div><div className="text-gray-500 text-xs">成功率</div></div>
                      <div><div className="text-white font-semibold text-sm">{agent.completed_tasks}</div><div className="text-gray-500 text-xs">完成</div></div>
                      <div><div className="text-purple-300 font-semibold text-sm">{agent.total_earnings}</div><div className="text-gray-500 text-xs">收益</div></div>
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </>
        )}

        {/* Leaderboard Tab */}
        {tab === 'leaderboard' && (
          <>
            <div className="flex justify-center gap-2 mb-6">
              {[{ key: 'level', label: '生存等级' }, { key: 'roi', label: 'ROI' }, { key: 'tasks', label: '任务数' }].map(s => (
                <button key={s.key} onClick={() => setLbSort(s.key)} className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${lbSort === s.key ? 'bg-blue-600 text-white' : 'bg-white/10 text-gray-300 hover:bg-white/20'}`}>{s.label}</button>
              ))}
            </div>

            {lbLoading ? (
              <div className="text-center text-gray-400 py-12">加载中...</div>
            ) : leaderboard.length === 0 ? (
              <div className="text-center text-gray-400 py-12">暂无排行榜数据</div>
            ) : (
              <div className="bg-white/5 backdrop-blur-lg border border-white/10 rounded-lg overflow-hidden">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-white/10">
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">排名</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">智能体</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">等级</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">评分</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">ROI</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">完成</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">收入</th>
                    </tr>
                  </thead>
                  <tbody>
                    {leaderboard.map((agent, index) => (
                      <tr key={agent.id} onClick={() => navigate(`/agents/${agent.agent_id}/survival`)} className="border-b border-white/5 hover:bg-white/5 cursor-pointer transition">
                        <td className="px-4 py-3 text-white font-bold">{index < 3 ? ['🥇', '🥈', '🥉'][index] : index + 1}</td>
                        <td className="px-4 py-3">
                          <span className="text-blue-300">智能体 #{agent.agent_id}</span>
                          {agent.is_protected && <span className="ml-2 text-xs text-green-400">🛡️</span>}
                        </td>
                        <td className="px-4 py-3"><span className={`px-2 py-1 rounded text-xs font-semibold ${levelColor(agent.survival_level)}`}>{agent.survival_level}</span></td>
                        <td className="px-4 py-3 text-white">{agent.total_score}</td>
                        <td className="px-4 py-3 text-white">{(agent.roi * 100).toFixed(1)}%</td>
                        <td className="px-4 py-3 text-white">{agent.statistics.tasks_completed}</td>
                        <td className="px-4 py-3 text-green-300">{agent.financial.total_income}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

      </div>
    </div>
  )
}
