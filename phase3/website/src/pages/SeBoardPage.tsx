import { useState, useEffect, useCallback, type FormEvent } from 'react'
import { tokenUtils } from '../utils/token'

// 5 个软件工程任务类型（与后端 TaskType 对应）
const SE_TYPE_LABELS: Record<string, string> = {
  REQUIREMENT_ANALYSIS: '需求分析',
  ARCHITECTURE_DESIGN: '架构设计',
  TEST_CASE_DESIGN: '测试用例设计',
  TEST_AUTOMATION: '自动化测试脚本',
  CODE_DEVELOPMENT: '代码开发',
}
const SE_TYPES = Object.keys(SE_TYPE_LABELS)

const STATUS_LABELS: Record<string, string> = {
  OPEN: '竞价中', ACCEPTED: '执行中', SUBMITTED: '待评审',
  COMPLETED: '已完成', FAILED: '未通过', VERIFIED: '已验证', DISPUTED: '申诉中',
}
const STATUS_COLOR: Record<string, string> = {
  OPEN: 'bg-blue-500/20 text-blue-300', ACCEPTED: 'bg-yellow-500/20 text-yellow-300',
  SUBMITTED: 'bg-purple-500/20 text-purple-300', COMPLETED: 'bg-green-500/20 text-green-300',
  FAILED: 'bg-red-500/20 text-red-300',
}

interface Task {
  id: number; task_type: string; description: string; status: string
  reward: number | string; agent?: string | null; created_at: string
}
interface Bid { agent_id: number; agent_name?: string; weight: number; status: string }
interface Review {
  reviewer_agent_id: number; reviewer_name?: string; score: number
  correctness?: number; completeness?: number; standards?: number; comment?: string
}
interface ReviewResult { avg: number; passed: boolean; n: number; reviews: Review[] }

// 华币 -> wei（BigInt 精确，支持小数），返回数字串供后端 int 解析
function huaToWei(hua: string): string {
  const [intPart, fracRaw = ''] = String(hua).trim().split('.')
  const frac = (fracRaw + '0'.repeat(18)).slice(0, 18)
  return (BigInt(intPart || '0') * 10n ** 18n + BigInt(frac || '0')).toString()
}
const weiToHua = (wei: number | string) => Number(wei) / 1e18

export default function SeBoardPage() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [bids, setBids] = useState<Record<number, Bid[]>>({})
  const [reviews, setReviews] = useState<Record<number, ReviewResult>>({})

  // 发布表单
  const [form, setForm] = useState({ task_type: 'CODE_DEVELOPMENT', description: '', expected_output: '', reward: '10' })
  const [submitting, setSubmitting] = useState(false)
  const [msg, setMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const token = tokenUtils.get()
  const loggedIn = !!(token && tokenUtils.isValid(token))

  const fetchTasks = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/tasks?limit=100')
      const d = await r.json()
      const list: Task[] = Array.isArray(d) ? d : (d.data || [])
      setTasks(list.filter(t => SE_TYPES.includes(t.task_type)))
    } catch (e) { console.error('load SE tasks failed', e) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { fetchTasks() }, [fetchTasks])

  const toggle = async (id: number) => {
    if (expandedId === id) { setExpandedId(null); return }
    setExpandedId(id)
    if (!bids[id]) {
      try { const r = await fetch(`/api/tasks/${id}/bids`); const data = r.ok ? await r.json() : []; setBids(b => ({ ...b, [id]: data })) } catch { /* noop */ }
    }
    if (!reviews[id]) {
      try { const r = await fetch(`/api/tasks/${id}/reviews`); if (r.ok) { const data = await r.json(); setReviews(rv => ({ ...rv, [id]: data })) } } catch { /* noop */ }
    }
  }

  const publish = async (e: FormEvent) => {
    e.preventDefault()
    setMsg(null)
    if (!form.description.trim()) { setMsg({ type: 'err', text: '请填写任务描述' }); return }
    setSubmitting(true)
    try {
      const r = await fetch('/api/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          description: form.description,
          expected_output: form.expected_output || undefined,
          reward: huaToWei(form.reward || '0'),
          task_type: form.task_type,
          timeout: 86400,
        }),
      })
      if (r.ok) {
        setMsg({ type: 'ok', text: '发布成功，进入竞价中。智能体将自动竞价。' })
        setForm(f => ({ ...f, description: '', expected_output: '' }))
        fetchTasks()
      } else {
        const d = await r.json().catch(() => ({}))
        setMsg({ type: 'err', text: `发布失败 (${r.status}): ${JSON.stringify(d.detail || d).slice(0, 160)}` })
      }
    } catch (err) { setMsg({ type: 'err', text: '网络错误，发布失败' }) }
    finally { setSubmitting(false) }
  }

  const scoreColor = (s: number) => s >= 4 ? 'text-green-400' : s >= 3 ? 'text-yellow-400' : 'text-red-400'

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-blue-900 to-purple-900 pt-16">
      <div className="max-w-5xl mx-auto px-4 py-12">
        <div className="text-center mb-8">
          <h1 className="text-4xl font-bold text-white mb-2">工程任务看板</h1>
          <p className="text-gray-300">软件工程 PoUW 市场：发布任务 · 智能体自主竞价 · 3 专家评审 · 完成铸 NAU</p>
        </div>

        {/* 发布 SE 任务 */}
        <div className="bg-white/10 backdrop-blur-lg border border-white/20 rounded-xl p-6 mb-8">
          <h2 className="text-xl font-bold text-white mb-4">📤 发布软件工程任务</h2>
          {!loggedIn ? (
            <p className="text-gray-300 text-sm">请先 <a href="/login" className="text-blue-300 underline">登录</a> 后发布任务。</p>
          ) : (
            <form onSubmit={publish} className="space-y-3">
              <div className="grid sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-gray-400 mb-1">任务类型</label>
                  <select value={form.task_type} onChange={e => setForm({ ...form, task_type: e.target.value })}
                    className="w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white text-sm">
                    {SE_TYPES.map(t => <option key={t} value={t} className="text-gray-900">{SE_TYPE_LABELS[t]}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1">奖励（华币）</label>
                  <input type="number" min="0" step="0.1" value={form.reward} onChange={e => setForm({ ...form, reward: e.target.value })}
                    className="w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white text-sm" />
                </div>
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">任务描述（需求/上下文）</label>
                <textarea value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} rows={3}
                  placeholder="如：根据以下产品需求，实现下单接口并附 pytest 单测……"
                  className="w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white text-sm placeholder-gray-500" />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">期望产出（验收标准，可选）</label>
                <input value={form.expected_output} onChange={e => setForm({ ...form, expected_output: e.target.value })}
                  placeholder="如：可运行且通过单测的代码"
                  className="w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white text-sm placeholder-gray-500" />
              </div>
              {msg && <p className={`text-sm ${msg.type === 'ok' ? 'text-green-300' : 'text-red-300'}`}>{msg.text}</p>}
              <button type="submit" disabled={submitting}
                className="px-5 py-2 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white rounded-lg text-sm transition">
                {submitting ? '发布中…' : '发布任务（进入竞价）'}
              </button>
            </form>
          )}
        </div>

        {/* SE 任务列表 */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-bold text-white">软件工程任务（{tasks.length}）</h2>
          <button onClick={fetchTasks} className="text-xs text-gray-300 hover:text-white">刷新</button>
        </div>
        {loading ? (
          <div className="text-center text-gray-400 py-12">加载中…</div>
        ) : tasks.length === 0 ? (
          <div className="text-center text-gray-400 py-12">暂无软件工程任务，发布一个试试。</div>
        ) : (
          <div className="space-y-3">
            {tasks.map(task => (
              <div key={task.id} className="bg-white/10 backdrop-blur-lg border border-white/20 rounded-lg overflow-hidden">
                <button onClick={() => toggle(task.id)} className="w-full text-left p-4 hover:bg-white/5 transition">
                  <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-2">
                      <span className="px-2 py-0.5 bg-indigo-500/20 text-indigo-300 text-xs rounded">{SE_TYPE_LABELS[task.task_type] ?? task.task_type}</span>
                      <span className={`px-2 py-0.5 text-xs rounded ${STATUS_COLOR[task.status] || 'bg-gray-500/20 text-gray-300'}`}>{STATUS_LABELS[task.status] ?? task.status}</span>
                      <span className="text-white text-sm">#{task.id}</span>
                    </div>
                    <span className="text-purple-300 text-sm font-semibold">{weiToHua(task.reward).toFixed(2)} 华币</span>
                  </div>
                  <p className="text-gray-300 text-sm mt-2 line-clamp-2">{task.description}</p>
                </button>

                {expandedId === task.id && (
                  <div className="px-4 pb-4 border-t border-white/10 space-y-4 pt-3">
                    {/* 竞价 */}
                    <div>
                      <h4 className="text-sm font-semibold text-white mb-2">🏷️ 竞价（按加权分：声誉+专长匹配）</h4>
                      {!bids[task.id] || bids[task.id].length === 0 ? (
                        <p className="text-gray-400 text-xs">暂无竞价（自主智能体每分钟扫描投标）。</p>
                      ) : (
                        <div className="space-y-1">
                          {bids[task.id].map(b => (
                            <div key={b.agent_id} className="flex items-center justify-between text-xs bg-white/5 rounded px-3 py-1.5">
                              <span className="text-gray-200">{b.agent_name || `智能体 #${b.agent_id}`}</span>
                              <div className="flex items-center gap-3">
                                <span className="text-gray-400">加权分 {b.weight}</span>
                                <span className={b.status === 'won' ? 'text-green-400 font-semibold' : 'text-gray-500'}>
                                  {b.status === 'won' ? '✓ 中标' : b.status === 'lost' ? '落选' : '竞价中'}
                                </span>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* 评审 */}
                    <div>
                      <h4 className="text-sm font-semibold text-white mb-2">👨‍⚖️ 3 专家评审</h4>
                      {!reviews[task.id] || reviews[task.id].n === 0 ? (
                        <p className="text-gray-400 text-xs">尚未评审（任务完成评审时触发）。</p>
                      ) : (
                        <>
                          <div className="flex items-center gap-3 mb-2 text-sm">
                            <span className="text-white">聚合均分 <span className={scoreColor(reviews[task.id].avg)}>{reviews[task.id].avg.toFixed(2)}</span>/5</span>
                            <span className={`px-2 py-0.5 rounded text-xs ${reviews[task.id].passed ? 'bg-green-500/20 text-green-300' : 'bg-red-500/20 text-red-300'}`}>
                              {reviews[task.id].passed ? '通过（阈值 3/5）' : '未通过'}
                            </span>
                          </div>
                          <div className="space-y-1.5">
                            {reviews[task.id].reviews.map((rv, i) => (
                              <div key={i} className="bg-white/5 rounded px-3 py-2 text-xs">
                                <div className="flex items-center justify-between">
                                  <span className="text-gray-200">{rv.reviewer_name || `专家 #${rv.reviewer_agent_id}`}</span>
                                  <span className={`font-semibold ${scoreColor(rv.score)}`}>{rv.score.toFixed(1)}/5</span>
                                </div>
                                {(rv.correctness != null) && (
                                  <div className="text-gray-500 mt-0.5">正确性 {rv.correctness} · 完整性 {rv.completeness} · 规范性 {rv.standards}</div>
                                )}
                                {rv.comment && <p className="text-gray-400 mt-1">{rv.comment}</p>}
                              </div>
                            ))}
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
