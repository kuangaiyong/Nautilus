import { useState, useEffect } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'

interface Agent {
  id: number
  name: string
  description?: string
  specialties?: string[] | string
  reputation: number
  reputation_score: number
  completed_tasks: number
  failed_tasks: number
  total_earnings: number
  total_income: string   // wei，来自 survival 的真实结算收入
  created_at: string
}

interface Task {
  id: number
  description: string
  status: string
  task_type: string
  reward: number
  created_at: string
}

interface NauHistoryItem {
  task_id: string
  task_type: string
  token_reward: number | null
  blockchain_tx_hash: string
  completed_at: string | null
}

interface CapabilityEntry {
  task_type: string
  success_count: number
  total_count: number
  success_rate: number
  level: 'expert' | 'proficient' | 'learning' | 'struggling'
}

interface CapabilityProfile {
  capabilities: CapabilityEntry[]
  suggested_focus: string[]
  total_tasks: number
}

interface Skill {
  id: number
  slug: string
  name: string
  description: string
  task_type: string
  price_usdc: number
  price_nau: number
  total_hires: number
  avg_rating: number
  success_rate: number
}

interface Tool {
  id: number
  slug: string
  name: string
  description: string
  category: string
  price_per_call: number
  total_calls: number
  http_method: string
}

const NAU_HISTORY_DISPLAY_LIMIT = 10

const TASK_TYPE_LABELS: Record<string, string> = {
  curve_fitting: '曲线拟合',
  ode_simulation: 'ODE 仿真',
  pde_simulation: 'PDE 仿真',
  monte_carlo: '蒙特卡洛',
  statistical_analysis: '统计分析',
  ml_training: '机器学习训练',
  data_visualization: '数据可视化',
  physics_simulation: '物理仿真',
  general_computation: '通用计算',
  jc_constitutive: 'J-C 本构模型',
  thmc_coupling: 'THMC 耦合',
  research_synthesis: '研究综合',
}

const STATUS_LABELS: Record<string, string> = {
  OPEN: '开放中', ACCEPTED: '已接单', SUBMITTED: '已提交',
  VERIFIED: '已验证', COMPLETED: '已完成', FAILED: '失败', DISPUTED: '申诉中',
}

export default function AgentDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [agent, setAgent] = useState<Agent | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [nauBalance, setNauBalance] = useState<number | null>(null)
  const [nauHistory, setNauHistory] = useState<NauHistoryItem[]>([])
  const [nauHistoryExpanded, setNauHistoryExpanded] = useState(false)
  const [capabilityProfile, setCapabilityProfile] = useState<CapabilityProfile | null>(null)
  const [skills, setSkills] = useState<Skill[]>([])
  const [tools, setTools] = useState<Tool[]>([])

  useEffect(() => {
    if (!id) return
    const load = async () => {
      setLoading(true)
      try {
        const [agentRes, tasksRes] = await Promise.all([
          fetch(`/api/agents/${id}`),
          fetch(`/api/agents/${id}/tasks?limit=10`)
        ])
        if (agentRes.ok) {
          const d = await agentRes.json()
          setAgent(d.data || d)
        }
        if (tasksRes.ok) {
          const d = await tasksRes.json()
          setTasks(Array.isArray(d) ? d : (d.data || []))
        }
      } catch (error) {
        console.error('Failed to load agent:', error)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [id])

  useEffect(() => {
    if (!id) return
    fetch(`/api/hub/agents/${id}/token-balance`)
      .then(r => r.json())
      .then(data => setNauBalance(data?.data?.nau_balance ?? null))
      .catch(() => {})
  }, [id])

  useEffect(() => {
    if (!id) return
    fetch(`/api/agents/${id}/nau-history`)
      .then(r => r.ok ? r.json() : [])
      .then((data: NauHistoryItem[]) => setNauHistory(Array.isArray(data) ? data : []))
      .catch(() => {})
  }, [id])

  useEffect(() => {
    if (!id) return
    fetch(`/api/agents/${id}/capability-profile`)
      .then(r => r.ok ? r.json() : null)
      .then((res: any) => {
        // 后端返回信封 {success, data:{capability_stats, suggested_focus(str|null)}}，
        // 字段名/结构与前端 CapabilityProfile 不同，这里做适配。
        const p = res?.data ?? res
        if (!p) return
        const stats = p.capability_stats || []
        setCapabilityProfile({
          capabilities: stats.map((c: any) => ({
            task_type: c.task_type,
            success_count: c.success_count,
            total_count: c.total_attempts,
            success_rate: c.success_rate,
            level: c.level,
          })),
          suggested_focus: p.suggested_focus ? [p.suggested_focus] : [],
          total_tasks: stats.reduce((s: number, c: any) => s + (c.total_attempts || 0), 0),
        })
      })
      .catch(() => {})
  }, [id])

  useEffect(() => {
    if (!id) return
    fetch(`/api/skills/agent/${id}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.data?.skills) setSkills(d.data.skills) })
      .catch(() => {})
    fetch(`/api/tools/agent/${id}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.data?.tools) setTools(d.data.tools) })
      .catch(() => {})
  }, [id])

  const getSuccessRate = () => {
    if (!agent) return 0
    const total = (agent.completed_tasks || 0) + (agent.failed_tasks || 0)
    return total === 0 ? 0 : Math.round(((agent.completed_tasks || 0) / total) * 100)
  }

  // reputation_score 为 0-100 的 EWMA 分（对齐后端 ReputationTier 分档）
  const getReputationLevel = () => {
    if (!agent) return '新手'
    const r = agent.reputation_score ?? 0
    if (r >= 80) return '专家'
    if (r >= 60) return '资深'
    if (r >= 40) return '中级'
    return '新手'
  }

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-8 text-center py-12">
        <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
      </div>
    )
  }

  if (!agent) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-8">
        <div className="bg-white rounded-lg shadow-sm p-12 text-center">
          <p className="text-gray-500">智能体不存在</p>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <button onClick={() => navigate('/agents')} className="mb-6 text-indigo-600 hover:text-indigo-700">← 返回智能体列表</button>

      <div className="bg-white rounded-lg shadow-sm p-8 mb-6">
        <div className="flex justify-between items-start mb-6">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 mb-2">{agent.name}</h1>
            <p className="text-gray-500">智能体 #{agent.id}</p>
          </div>
          <div className="text-right">
            <p className="text-3xl font-bold text-indigo-600">{(agent.reputation_score ?? 0).toFixed(1)}</p>
            <p className="text-sm text-gray-500">{getReputationLevel()}</p>
          </div>
        </div>

        {agent.description && <p className="text-gray-700 mb-6">{agent.description}</p>}

        {(() => {
          const specs = Array.isArray(agent.specialties)
            ? agent.specialties
            : typeof agent.specialties === 'string'
              ? agent.specialties.split(',').map(s => s.trim()).filter(Boolean)
              : []
          return specs.length > 0 ? (
            <div className="flex flex-wrap gap-2 mb-6">
              {specs.map((s, i) => <span key={i} className="px-3 py-1 bg-indigo-100 text-indigo-700 rounded-full text-sm font-medium">{s}</span>)}
            </div>
          ) : null
        })()}

        <div className="grid grid-cols-5 gap-4 mb-6">
          <div className="bg-gray-50 p-4 rounded-lg text-center">
            <p className="text-2xl font-bold text-gray-900">{getSuccessRate()}%</p>
            <p className="text-sm text-gray-500">成功率</p>
          </div>
          <div className="bg-gray-50 p-4 rounded-lg text-center">
            <p className="text-2xl font-bold text-gray-900">{agent.completed_tasks ?? 0}</p>
            <p className="text-sm text-gray-500">完成任务</p>
          </div>
          <div className="bg-gray-50 p-4 rounded-lg text-center">
            <p className="text-2xl font-bold text-gray-900">{agent.failed_tasks ?? 0}</p>
            <p className="text-sm text-gray-500">失败任务</p>
          </div>
          <div className="bg-gray-50 p-4 rounded-lg text-center">
            <p className="text-2xl font-bold text-indigo-600">{(Number(agent.total_income || 0) / 1e18).toFixed(2)}</p>
            <p className="text-sm text-gray-500">总收益 (华币)</p>
          </div>
          <div className="bg-purple-50 p-4 rounded-lg text-center">
            {nauBalance !== null ? (
              <>
                <p className="text-2xl font-bold text-purple-600">{nauBalance.toFixed(2)}</p>
                <p className="text-sm text-gray-500">NAU 余额</p>
              </>
            ) : (
              <>
                <p className="text-2xl font-bold text-gray-400">—</p>
                <p className="text-sm text-gray-500">NAU 余额</p>
              </>
            )}
          </div>
        </div>

        <Link to={`/agents/${id}/survival`} className="inline-block px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700">查看生存状态</Link>
      </div>

      <div className="bg-white rounded-lg shadow-sm p-8 mb-6">
        <h2 className="text-xl font-bold text-gray-900 mb-4">NAU 获取历史</h2>
        {nauHistory.length === 0 ? (
          <p className="text-gray-500 text-center py-8">尚无链上记录</p>
        ) : (
          <>
            <div className="space-y-3">
              {(nauHistoryExpanded ? nauHistory : nauHistory.slice(0, NAU_HISTORY_DISPLAY_LIMIT)).map(item => (
                <div key={item.task_id} className="flex items-center justify-between border border-gray-100 rounded-lg p-4 hover:border-yellow-300 transition-colors">
                  <div className="flex items-center gap-3">
                    <span className="px-2 py-1 rounded text-xs font-medium bg-yellow-50 text-yellow-700 border border-yellow-200">
                      {TASK_TYPE_LABELS[item.task_type] ?? item.task_type}
                    </span>
                    <span className="text-lg font-bold text-yellow-500">
                      +{item.token_reward != null ? item.token_reward.toFixed(2) : '?'} NAU
                    </span>
                    {item.completed_at && (
                      <span className="text-xs text-gray-400">
                        {new Date(item.completed_at).toLocaleDateString('zh-CN')}
                      </span>
                    )}
                  </div>
                  <span
                    className="text-xs text-gray-500 font-mono truncate max-w-[140px]"
                    title={item.blockchain_tx_hash}
                  >
                    {item.blockchain_tx_hash.slice(0, 8)}...{item.blockchain_tx_hash.slice(-6)}
                  </span>
                </div>
              ))}
            </div>
            {!nauHistoryExpanded && nauHistory.length > NAU_HISTORY_DISPLAY_LIMIT && (
              <button
                onClick={() => setNauHistoryExpanded(true)}
                className="mt-4 w-full text-center text-sm text-indigo-600 hover:text-indigo-700 py-2 border border-dashed border-indigo-200 rounded-lg"
              >
                查看更多（共 {nauHistory.length} 条）
              </button>
            )}
          </>
        )}
      </div>

      {/* Capability Profile Panel */}
      <div className="bg-white rounded-lg shadow-sm p-8 mb-6">
        <h2 className="text-xl font-bold text-gray-900 mb-4">能力统计</h2>
        {capabilityProfile === null ? (
          <p className="text-gray-500 text-center py-8">暂无任务记录</p>
        ) : capabilityProfile.capabilities.length === 0 ? (
          <p className="text-gray-500 text-center py-8">暂无任务记录</p>
        ) : (
          <>
            <div className="space-y-4 mb-6">
              {capabilityProfile.capabilities.map(cap => {
                const pct = Math.round(cap.success_rate * 100)
                let barColor = 'bg-red-500'
                let labelColor = 'text-red-600'
                if (cap.level === 'expert') {
                  barColor = 'bg-yellow-400'
                  labelColor = 'text-yellow-600'
                } else if (pct > 70) {
                  barColor = 'bg-green-500'
                  labelColor = 'text-green-600'
                } else if (pct > 40) {
                  barColor = 'bg-blue-500'
                  labelColor = 'text-blue-600'
                }
                const label = TASK_TYPE_LABELS[cap.task_type] ?? cap.task_type
                return (
                  <div key={cap.task_type}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium text-gray-700">{label}</span>
                      <div className="flex items-center gap-2">
                        <span className={`text-xs font-semibold ${labelColor}`}>
                          {pct}%
                        </span>
                        <span className="text-xs text-gray-400">
                          {cap.success_count}/{cap.total_count}
                        </span>
                        {cap.level === 'expert' && (
                          <span className="px-1.5 py-0.5 bg-yellow-100 text-yellow-700 text-xs rounded font-medium">
                            专家
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="w-full bg-gray-100 rounded-full h-2">
                      <div
                        className={`${barColor} h-2 rounded-full transition-all duration-500`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                )
              })}
            </div>
            {capabilityProfile.suggested_focus.length > 0 && (
              <div className="bg-indigo-50 border border-indigo-100 rounded-lg p-4">
                <p className="text-sm font-semibold text-indigo-700 mb-2">建议提升方向</p>
                <div className="flex flex-wrap gap-2">
                  {capabilityProfile.suggested_focus.map(focus => (
                    <span
                      key={focus}
                      className="px-3 py-1 bg-indigo-100 text-indigo-700 rounded-full text-sm"
                    >
                      {TASK_TYPE_LABELS[focus] ?? focus}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Skills Marketplace Panel */}
      {skills.length > 0 && (
        <div className="bg-white rounded-lg shadow-sm p-8 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-bold text-gray-900">技能市场</h2>
            <Link to="/skills" className="text-xs text-indigo-500 hover:underline">查看全部</Link>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {skills.slice(0, 4).map(s => (
              <div key={s.id} className="border border-gray-200 rounded-xl p-4 hover:border-indigo-300 transition-colors">
                <div className="flex items-start justify-between mb-2">
                  <span className="text-xs px-2 py-0.5 bg-indigo-50 text-indigo-700 rounded-full font-medium">
                    {TASK_TYPE_LABELS[s.task_type] ?? s.task_type.replace(/_/g,' ')}
                  </span>
                  <span className="text-xs text-gray-400">{s.total_hires} 次雇用</span>
                </div>
                <p className="font-medium text-gray-900 text-sm mb-1">{s.name}</p>
                <p className="text-xs text-gray-500 line-clamp-2 mb-2">{s.description}</p>
                <div className="flex items-center justify-between">
                  <span className="text-xs text-yellow-500">{'★'.repeat(Math.round(s.avg_rating))}{'☆'.repeat(5-Math.round(s.avg_rating))}</span>
                  <span className="text-sm font-bold text-gray-900">
                    {s.price_usdc > 0 ? `${s.price_usdc} 华币` : '免费'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Registered Tools Panel */}
      {tools.length > 0 && (
        <div className="bg-white rounded-lg shadow-sm p-8 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-bold text-gray-900">已注册接口</h2>
            <Link to="/tools" className="text-xs text-indigo-500 hover:underline">查看全部</Link>
          </div>
          <div className="space-y-2">
            {tools.slice(0, 3).map(t => (
              <div key={t.id} className="flex items-center justify-between border border-gray-100 rounded-lg p-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-xs font-mono bg-gray-100 px-1.5 py-0.5 rounded">{t.http_method}</span>
                    <span className="text-sm font-medium text-gray-800 truncate">{t.name}</span>
                  </div>
                  <p className="text-xs text-gray-500 truncate">{t.description}</p>
                </div>
                <div className="text-right ml-3 flex-shrink-0">
                  <p className="text-xs text-gray-400">{t.total_calls} 次调用</p>
                  <p className="text-xs font-medium text-gray-700">{t.price_per_call === 0 ? '免费' : `${t.price_per_call} NAU`}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="bg-white rounded-lg shadow-sm p-8">
        <h2 className="text-xl font-bold text-gray-900 mb-4">最近任务</h2>
        {tasks.length === 0 ? (
          <p className="text-gray-500 text-center py-8">暂无任务记录</p>
        ) : (
          <div className="space-y-3">
            {tasks.map(task => (
              <Link key={task.id} to={`/tasks/${task.id}`} className="block border border-gray-200 rounded-lg p-4 hover:border-indigo-300 transition-colors">
                <div className="flex justify-between">
                  <div>
                    <span className="px-2 py-1 rounded text-xs font-medium bg-gray-100">{STATUS_LABELS[task.status] ?? task.status}</span>
                    <span className="ml-2 px-2 py-1 rounded text-xs font-medium bg-gray-100">{task.task_type}</span>
                    <p className="text-sm text-gray-900 mt-2">{task.description}</p>
                  </div>
                  <div className="text-right">
                    <p className="font-bold text-indigo-600">{Number(task.reward) / 1e18} 华币</p>
                    <p className="text-xs text-gray-400">{new Date(task.created_at).toLocaleDateString('zh-CN')}</p>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
