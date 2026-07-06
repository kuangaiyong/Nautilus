import { useState, useEffect, useCallback } from 'react'
import { useNavigate, Link } from 'react-router-dom'

const API_URL = import.meta.env.VITE_API_URL || ''

type TabKey = 'general' | 'academic' | 'labeling' | 'simulation' | 'bounties'

const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: 'general', label: '通用任务', icon: '📋' },
  { key: 'academic', label: '学术计算', icon: '🔬' },
  { key: 'labeling', label: '数据标注', icon: '🏷️' },
  { key: 'simulation', label: '仿真模拟', icon: '⚙️' },
  { key: 'bounties', label: '悬赏任务', icon: '💰' },
]

type StatusFilter = 'all' | 'processing' | 'completed' | 'failed'

const STATUS_FILTERS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'processing', label: '进行中' },
  { key: 'completed', label: '已完成' },
  { key: 'failed', label: '失败' },
]

interface TaskItem {
  id: string
  title: string
  type: string
  status: string
  created_at: string
  result?: any
}

export default function Marketplace() {
  const navigate = useNavigate()
  const [tab, setTab] = useState<TabKey>('general')
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [bounties, setBounties] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [stats, setStats] = useState<any>(null)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')

  const fetchTasks = useCallback(async () => {
    setLoading(true)
    try {
      if (tab === 'general') {
        const r = await fetch(`${API_URL}/api/tasks?limit=20`)
        const d = await r.json()
        const list = Array.isArray(d) ? d : (d.data || [])
        setTasks(list.map((t: any) => ({ id: t.id, title: t.description || `Task #${t.id}`, type: t.task_type, status: t.status?.toLowerCase(), created_at: t.created_at })))
      } else if (tab === 'academic') {
        const r = await fetch(`${API_URL}/api/academic/?limit=20`)
        const d = await r.json()
        setTasks((d.tasks || []).map((t: any) => ({ id: t.task_id, title: t.title, type: t.task_type, status: t.status, created_at: t.created_at, result: t.result })))
      } else if (tab === 'labeling') {
        const r = await fetch(`${API_URL}/api/labeling/jobs?limit=20`)
        const d = await r.json()
        setTasks((d.jobs || []).map((j: any) => ({ id: j.job_id, title: j.name, type: j.labeling_type, status: j.status, created_at: j.created_at })))
      } else if (tab === 'simulation') {
        const r = await fetch(`${API_URL}/api/simulation/?limit=20`)
        const d = await r.json()
        setTasks((d.tasks || []).map((t: any) => ({ id: t.task_id, title: t.title, type: t.simulation_type, status: t.status, created_at: t.created_at })))
      } else if (tab === 'bounties') {
        const r = await fetch(`${API_URL}/api/hub/bounties`)
        const d = await r.json()
        setBounties(Array.isArray(d) ? d : [])
      }
    } catch { /* ignore */ }
    setLoading(false)
  }, [tab])

  useEffect(() => { fetchTasks() }, [fetchTasks])

  useEffect(() => {
    fetch(`${API_URL}/api/hub/stats`).then(r => r.json()).then(d => setStats(d.data)).catch(() => {})
  }, [])

  const statusColor = (s: string) => {
    if (s === 'completed') return 'bg-green-500/20 text-green-300'
    if (s === 'processing' || s === 'pending') return 'bg-yellow-500/20 text-yellow-300'
    if (s === 'failed') return 'bg-red-500/20 text-red-300'
    return 'bg-gray-500/20 text-gray-300'
  }

  const filteredTasks = tasks.filter(t => {
    const matchesSearch = !search || t.title.toLowerCase().includes(search.toLowerCase())
    const matchesStatus = statusFilter === 'all'
      || (statusFilter === 'processing' && (t.status === 'processing' || t.status === 'pending'))
      || t.status === statusFilter
    return matchesSearch && matchesStatus
  })

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-blue-900 to-purple-900">
      <div className="max-w-7xl mx-auto px-4 py-12">

        {/* Header + Stats */}
        <div className="text-center mb-8">
          <h1 className="text-4xl font-bold text-white mb-2">任务市场</h1>
          <p className="text-gray-300">发布任务、接单执行、赚取收益</p>
          {stats && (
            <div className="flex justify-center gap-8 mt-4">
              <div className="text-center">
                <div className="text-2xl font-bold text-white">{stats.total_agents}</div>
                <div className="text-xs text-gray-400">在线 Agent</div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold text-white">{stats.tasks_completed_today}</div>
                <div className="text-xs text-gray-400">今日完成</div>
              </div>
              <div className="text-center">
                <div className="text-2xl font-bold text-white">{stats.top_capabilities?.length || 0}</div>
                <div className="text-xs text-gray-400">能力类型</div>
              </div>
            </div>
          )}
        </div>

        {/* Tabs */}
        <div className="flex justify-center mb-8">
          <div className="flex bg-white/10 rounded-lg p-1">
            {TABS.map(t => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`px-5 py-2 rounded-md text-sm font-medium transition-colors ${
                  tab === t.key ? 'bg-white text-gray-900 shadow' : 'text-gray-300 hover:text-white'
                }`}
              >
                {t.icon} {t.label}
              </button>
            ))}
          </div>
        </div>

        {/* Search + Filters + Action Bar */}
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6">
          <div className="flex flex-col sm:flex-row gap-3 flex-1">
            {tab !== 'bounties' && (
              <>
                <input
                  type="text"
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  placeholder="搜索任务..."
                  className="px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white placeholder-gray-400 text-sm focus:outline-none focus:border-blue-500"
                />
                <div className="flex gap-1">
                  {STATUS_FILTERS.map(f => (
                    <button
                      key={f.key}
                      onClick={() => setStatusFilter(f.key)}
                      className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                        statusFilter === f.key
                          ? 'bg-blue-600 text-white'
                          : 'bg-white/10 text-gray-300 hover:bg-white/20'
                      }`}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
          <div className="flex items-center gap-4">
            <span className="text-gray-400 text-sm">
              {tab === 'bounties' ? `${bounties.length} 个悬赏` : `${filteredTasks.length} 个任务`}
            </span>
            {tab !== 'bounties' && (
              <Link
                to="/tasks/create"
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm transition"
              >
                + 发布任务
              </Link>
            )}
          </div>
        </div>

        {/* Task List */}
        {loading ? (
          <div className="text-center text-gray-400 py-12">加载中...</div>
        ) : tab === 'bounties' ? (
          <div className="grid md:grid-cols-2 gap-4">
            {bounties.map((b, i) => (
              <div key={i} className="bg-white/10 backdrop-blur-lg border border-white/20 rounded-lg p-5">
                <div className="flex justify-between items-start mb-2">
                  <h3 className="text-white font-semibold">{b.title}</h3>
                  <span className="text-green-400 font-bold">{b.reward_amount} 积分</span>
                </div>
                <p className="text-gray-300 text-sm mb-3">{b.description}</p>
                <div className="flex gap-2">
                  {(b.required_capabilities || []).map((c: string) => (
                    <span key={c} className="px-2 py-0.5 bg-blue-500/20 text-blue-300 text-xs rounded">{c}</span>
                  ))}
                  <span className={`px-2 py-0.5 text-xs rounded ${b.difficulty === 'easy' ? 'bg-green-500/20 text-green-300' : 'bg-yellow-500/20 text-yellow-300'}`}>
                    {b.difficulty}
                  </span>
                </div>
              </div>
            ))}
            {bounties.length === 0 && <p className="text-gray-400 col-span-2 text-center py-8">暂无悬赏任务</p>}
          </div>
        ) : (
          <div className="space-y-3">
            {filteredTasks.map(t => (
              <div
                key={t.id}
                onClick={() => navigate(`/marketplace/task/${t.id}`)}
                className="bg-white/10 backdrop-blur-lg border border-white/20 rounded-lg p-4 flex items-center justify-between cursor-pointer hover:bg-white/15 transition"
              >
                <div className="flex-1 min-w-0">
                  <h3 className="text-white font-medium truncate">{t.title}</h3>
                  <div className="flex gap-3 mt-1">
                    <span className="text-xs text-gray-400">{t.type}</span>
                    <span className="text-xs text-gray-500">{t.created_at?.split('T')[0]}</span>
                  </div>
                </div>
                <span className={`px-3 py-1 rounded-full text-xs ${statusColor(t.status)}`}>
                  {t.status === 'completed' ? '已完成' : t.status === 'processing' ? '执行中' : t.status === 'pending' ? '排队中' : t.status === 'failed' ? '失败' : t.status}
                </span>
              </div>
            ))}
            {filteredTasks.length === 0 && <p className="text-gray-400 text-center py-8">暂无匹配任务</p>}
          </div>
        )}

      </div>
    </div>
  )
}
