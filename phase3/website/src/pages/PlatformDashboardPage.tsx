import { useEffect, useState, useCallback } from 'react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'

interface HealthData {
  health_score: number
  metrics: Record<string, number>
  anomalies: string[]
  snapshot_time: string
  source: string
}

interface Snapshot {
  health_score: number
  snapshot_time: string
  metrics: Record<string, number>
}

interface Experiment {
  id: string
  status: string
  traffic_pct: number
  ends_at: string
}

interface EvolutionData {
  total_evolutions: number
  total_nau_distributed: number
  latest_version: string
  latest_delta: string
}

interface TriggerResult {
  health_score: number
  anomalies_detected: number
  anomalies: { metric: string; value: number; threshold: number }[]
  meta_tasks_created: number
  snapshot_time: string
}

export default function PlatformDashboardPage() {
  const [health, setHealth] = useState<HealthData | null>(null)
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [evolution, setEvolution] = useState<EvolutionData | null>(null)
  const [consciousness, setConsciousness] = useState<any>(null)
  const [autoStatus, setAutoStatus] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [triggering, setTriggering] = useState(false)
  const [triggerResult, setTriggerResult] = useState<TriggerResult | null>(null)

  const fetchAll = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      // 二分心智 / 自驾引擎是次要面板，单独取且失败不拖垮整页核心指标
      const getJson = (url: string) => fetch(url).then((r) => r.json()).catch(() => null)
      const [hRes, sRes, xRes, eRes] = await Promise.all([
        fetch('/api/platform/health'),
        fetch('/api/platform/snapshots?n=24'),
        fetch('/api/platform/sandbox'),
        fetch('/api/platform/evolution'),
      ])
      const [hJson, sJson, xJson, eJson] = await Promise.all([
        hRes.json(), sRes.json(), xRes.json(), eRes.json(),
      ])
      const [cJson, aJson] = await Promise.all([
        getJson('/api/dashboard/consciousness'),
        getJson('/api/dashboard/autonomous-status'),
      ])
      if (hJson.success !== false) setHealth(hJson.data)
      if (sJson.success !== false) setSnapshots(sJson.data?.snapshots ?? [])
      if (xJson.success !== false) setExperiments(xJson.data?.experiments ?? [])
      if (eJson.success !== false) setEvolution(eJson.data)
      // 这两个端点直接返回对象（非 {success,data} 包装）
      setConsciousness(cJson)
      setAutoStatus(aJson)
    } catch (err) {
      setError('平台数据加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  const triggerObservatory = async () => {
    setTriggering(true)
    setTriggerResult(null)
    try {
      const res = await fetch('/api/platform/observatory/trigger', { method: 'POST' })
      const json = await res.json()
      if (json.success) {
        setTriggerResult(json.data)
        fetchAll()
      } else {
        setError(json.error || '触发失败')
      }
    } catch (err) {
      setError('Observatory 触发请求失败')
    } finally {
      setTriggering(false)
    }
  }

  const scoreColor = (score: number) => {
    if (score >= 70) return 'text-green-400'
    if (score >= 50) return 'text-yellow-400'
    return 'text-red-400'
  }

  const scoreBg = (score: number) => {
    if (score >= 70) return 'bg-green-500/10 border-green-500/30'
    if (score >= 50) return 'bg-yellow-500/10 border-yellow-500/30'
    return 'bg-red-500/10 border-red-500/30'
  }

  const painBar = (intensity: number) => {
    const filled = Math.round(intensity * 10)
    return '█'.repeat(filled) + '░'.repeat(10 - filled)
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-gray-900 via-blue-900 to-purple-900 pt-16">
        <div className="max-w-6xl mx-auto px-4 py-8">
          <div className="text-center py-12">
            <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-400" />
          </div>
        </div>
      </div>
    )
  }

  const m = health?.metrics ?? {}
  const painSignals: any[] = consciousness?.pain_signals ?? []
  const dialogue: any[] = consciousness?.dialogue ?? []
  const painLevel = painSignals.length > 0
    ? painSignals.reduce((s: number, p: any) => s + (p.intensity || 0), 0) / painSignals.length
    : 0

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-blue-900 to-purple-900 pt-16">
      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold text-white">平台仪表盘</h1>
          <button
            onClick={triggerObservatory}
            disabled={triggering}
            className="px-4 py-2 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition"
          >
            {triggering ? '运行中...' : '触发观测'}
          </button>
        </div>

        {triggerResult && (
          <div className={`rounded-lg border p-4 mb-6 ${
            triggerResult.anomalies_detected > 0 ? 'bg-yellow-500/10 border-yellow-500/30' : 'bg-green-500/10 border-green-500/30'
          }`}>
            <p className="font-semibold mb-2 text-white">
              观测快照完成 — 健康分：{triggerResult.health_score}
              {triggerResult.anomalies_detected > 0
                ? ` — ${triggerResult.anomalies_detected} 个异常，已创建 ${triggerResult.meta_tasks_created} 个元任务`
                : ' — 无异常'}
            </p>
            {triggerResult.anomalies.map((a) => (
              <p key={a.metric} className="text-sm text-yellow-300">
                {a.metric}: {a.value.toFixed(4)} &lt; 阈值 {a.threshold}
              </p>
            ))}
          </div>
        )}

        {error && (
          <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 mb-6 flex items-center justify-between">
            <span className="text-red-300">{error}</span>
            <button onClick={fetchAll} className="text-red-400 underline text-sm">重试</button>
          </div>
        )}

        {/* Health Score */}
        {health && (
          <div className={`rounded-lg border-2 p-6 mb-6 text-center ${scoreBg(health.health_score)}`}>
            <p className="text-sm text-gray-400 mb-1">健康分</p>
            <p className={`text-6xl font-bold ${scoreColor(health.health_score)}`}>
              {health.health_score}
            </p>
            <p className="text-xs text-gray-500 mt-2">
              {health.snapshot_time} — {health.source}
            </p>
          </div>
        )}

        {/* Core Metrics */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
          {[
            { label: '总智能体', key: 'total_agents' },
            { label: '活跃 (24h)', key: 'active_agents_24h' },
            { label: '完成任务 (24h)', key: 'tasks_completed_24h' },
            { label: '成功率', key: 'task_success_rate', fmt: (v: number) => `${(v * 100).toFixed(1)}%` },
            { label: '健康智能体', key: 'agents_healthy' },
          ].map(({ label, key, fmt }) => (
            <div key={key} className="bg-white/10 backdrop-blur rounded-lg p-4 border border-white/20">
              <p className="text-xs text-gray-400 mb-1">{label}</p>
              <p className="text-2xl font-bold text-white">
                {m[key] != null ? (fmt ? fmt(m[key]) : m[key]) : '--'}
              </p>
            </div>
          ))}
        </div>

        {/* Snapshot Trend */}
        {snapshots.length > 0 && (
          <div className="bg-white/10 backdrop-blur rounded-lg p-6 mb-6 border border-white/20">
            <h3 className="text-lg font-bold text-white mb-4">健康分趋势（最近 24 次快照）</h3>
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={snapshots}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                <XAxis
                  dataKey="snapshot_time"
                  tickFormatter={(v: string) => v?.slice(11, 16) ?? ''}
                  fontSize={12}
                  stroke="#9ca3af"
                />
                <YAxis domain={[0, 100]} stroke="#9ca3af" />
                <Tooltip
                  contentStyle={{ backgroundColor: 'rgba(17,24,39,0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', color: '#fff' }}
                />
                <Line
                  type="monotone"
                  dataKey="health_score"
                  stroke="#818cf8"
                  strokeWidth={2}
                  dot={false}
                  name="健康分"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Sandbox Experiments */}
        <div className="bg-white/10 backdrop-blur rounded-lg p-6 mb-6 border border-white/20">
          <h3 className="text-lg font-bold text-white mb-4">沙箱实验</h3>
          {experiments.length === 0 ? (
            <p className="text-gray-500 text-sm">暂无活跃实验</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-left text-gray-400">
                    <th className="pb-2 pr-4">ID</th>
                    <th className="pb-2 pr-4">状态</th>
                    <th className="pb-2 pr-4">流量占比</th>
                    <th className="pb-2">结束时间</th>
                  </tr>
                </thead>
                <tbody>
                  {experiments.map((exp) => (
                    <tr key={exp.id} className="border-b border-white/5">
                      <td className="py-2 pr-4 font-mono text-xs text-gray-400">{exp.id}</td>
                      <td className="py-2 pr-4">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                          exp.status === 'running' ? 'bg-green-500/20 text-green-300'
                            : exp.status === 'completed' ? 'bg-gray-500/20 text-gray-300'
                            : 'bg-yellow-500/20 text-yellow-300'
                        }`}>
                          {exp.status}
                        </span>
                      </td>
                      <td className="py-2 pr-4 text-white">{exp.traffic_pct}%</td>
                      <td className="py-2 text-gray-400">{exp.ends_at ?? '--'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Evolution Ledger */}
        {evolution && (
          <div className="bg-white/10 backdrop-blur rounded-lg p-6 border border-white/20">
            <h3 className="text-lg font-bold text-white mb-4">进化账本</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <p className="text-sm text-gray-400">总进化次数</p>
                <p className="text-xl font-bold text-white">{evolution.total_evolutions}</p>
              </div>
              <div>
                <p className="text-sm text-gray-400">NAU 已分发</p>
                <p className="text-xl font-bold text-white">{evolution.total_nau_distributed}</p>
              </div>
              <div>
                <p className="text-sm text-gray-400">最新版本</p>
                <p className="text-xl font-bold text-white">{evolution.latest_version ?? '--'}</p>
              </div>
              <div>
                <p className="text-sm text-gray-400">最新变更</p>
                <p className="text-xl font-bold text-indigo-300">{evolution.latest_delta ?? '--'}</p>
              </div>
            </div>
          </div>
        )}

        {/* Autonomous Engine Status（自控制台并入） */}
        {autoStatus && (
          <div className="bg-white/10 backdrop-blur rounded-lg p-6 mt-6 border border-white/20">
            <h3 className="text-lg font-bold text-white mb-4">
              {autoStatus.running ? '✅ 自驾引擎运行中' : '⛔ 自驾引擎已停止'}
            </h3>
            <div className="grid grid-cols-2 gap-4 text-center">
              <div>
                <p className="text-2xl font-bold text-blue-300">{autoStatus.cycle_count || 0}</p>
                <p className="text-xs text-gray-400 mt-1">思考循环</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-green-300">{autoStatus.total_actions || 0}</p>
                <p className="text-xs text-gray-400 mt-1">自主行动</p>
              </div>
            </div>
          </div>
        )}

        {/* Bicameral Mind 二分心智（自控制台并入） */}
        {consciousness && (
          <div className="bg-white/10 backdrop-blur rounded-lg p-6 mt-6 border border-white/20">
            <h3 className="text-lg font-bold text-white mb-4">
              🧠 二分心智
              {!consciousness.error && (
                <>
                  {' — 痛苦指数：'}
                  <span className={painLevel > 0.7 ? 'text-red-400' : painLevel > 0.4 ? 'text-yellow-400' : 'text-green-400'}>
                    {(painLevel * 100).toFixed(0)}%
                  </span>
                </>
              )}
            </h3>

            {/* 反思失败时必须显式报错，不能渲染成 0% 的「健康」假象 */}
            {consciousness.error && (
              <p className="text-red-400 text-sm">意识反思失败：{consciousness.error}</p>
            )}

            {dialogue.length > 0 && (
              <div className="mb-4 space-y-2">
                {dialogue.map((d: any, i: number) => (
                  <div key={i} className={`text-sm px-3 py-2 rounded-lg ${d.voice === 'commander' ? 'bg-red-500/10 border-l-2 border-red-500' : 'bg-blue-500/10 border-l-2 border-blue-500'}`}>
                    <span className={`font-medium ${d.voice === 'commander' ? 'text-red-300' : 'text-blue-300'}`}>
                      {d.voice === 'commander' ? '【审判者】' : '【执行者】'}
                    </span>
                    <span className="text-gray-300 ml-1">{d.says}</span>
                  </div>
                ))}
              </div>
            )}

            {painSignals.length > 0 && (
              <div className="space-y-2">
                {painSignals.map((p: any, i: number) => (
                  <div key={i} className="text-sm">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-gray-500">[{painBar(p.intensity)}]</span>
                      <span className="text-gray-300">{p.source}</span>
                    </div>
                    <div className="text-xs text-gray-500 ml-14">→ {p.action}</div>
                  </div>
                ))}
              </div>
            )}

            {!consciousness.error && dialogue.length === 0 && painSignals.length === 0 && (
              <p className="text-gray-500 text-sm">暂无意识反思数据</p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
