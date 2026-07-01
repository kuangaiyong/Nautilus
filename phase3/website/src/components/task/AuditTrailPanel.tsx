import { useState, useEffect, useCallback } from 'react'

/** 单条链上存证记录（对应合约 AuditRecord 事件 + 后端校验结果）。 */
export interface AuditRecordItem {
  action: string
  actor: string
  onchain_hash: string
  recomputed_hash: string
  content_match: boolean
  actor_verified: boolean
  matched_review_id: number | null
  seq: number
  timestamp: number
  tx_hash: string
}

export interface AuditResult {
  task_id: number
  onchain_records: number
  verified: boolean
  tampered_actions: string[]
  records: AuditRecordItem[]
}

const ACTION_LABELS: Record<string, string> = {
  PUBLISH: '发布', BID: '竞价', AWARD: '派单', ACCEPT: '抢单',
  SUBMIT: '提交', REVIEW: '评审', COMPLETE: '完成',
}

const short = (s: string) => (s && s.length > 16 ? `${s.slice(0, 8)}…${s.slice(-6)}` : s)

/**
 * 任务「链上可信追踪」面板：拉取 GET /api/audit/{id}，展示每个生命周期动作的链上存证，
 * 由后端用源表重算哈希与链上事件比对，逐条给出「内容一致 / 被篡改」与「主体已验证」。
 * 只读，公开可验证；提供「重新校验」以直观演示防篡改（改动链下数据后重查即变红）。
 */
export default function AuditTrailPanel({ taskId }: { taskId: string | number }) {
  const [data, setData] = useState<AuditResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`/api/audit/${taskId}`)
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(typeof d?.detail === 'string' ? d.detail : '加载链上可信追踪失败')
      }
      setData(await res.json())
    } catch (e: any) {
      setError(e?.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [taskId])

  useEffect(() => { load() }, [load])

  return (
    <div className="bg-white rounded-lg shadow-sm p-8 mt-6" data-testid="audit-panel">
      <div className="flex justify-between items-center mb-4 gap-4">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">链上可信追踪</h2>
          <p className="text-sm text-gray-500">
            每个动作由其主体的托管钱包亲自签名上链存证，可验证防篡改与主体不可抵赖
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="shrink-0 px-4 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
        >
          {loading ? '校验中…' : '重新校验'}
        </button>
      </div>

      {loading && !data && (
        <div className="text-center py-8">
          <div className="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-indigo-600"></div>
        </div>
      )}

      {error && <p className="text-sm text-red-600 py-2">{error}</p>}

      {!loading && !error && data && data.onchain_records === 0 && (
        <p className="text-sm text-gray-500 py-4">暂无链上存证记录（任务较新或尚未产生可追踪动作）。</p>
      )}

      {data && data.onchain_records > 0 && (
        <>
          <div
            data-testid="audit-verdict"
            className={`mb-4 px-4 py-3 rounded-lg text-sm font-medium ${
              data.verified ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-800'
            }`}
          >
            {data.verified
              ? `✓ 全部一致：${data.onchain_records} 条链上存证均与当前数据吻合，未检测到篡改`
              : `✗ 检测到篡改：${data.tampered_actions.join('、')} 的链下数据与链上锚点不一致`}
          </div>

          <div className="space-y-2">
            {data.records.map((r) => (
              <div
                key={r.seq}
                data-testid="audit-record"
                className="border border-gray-100 rounded-lg p-3 text-sm"
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="px-2 py-0.5 rounded bg-indigo-100 text-indigo-800 text-xs font-medium">
                    {ACTION_LABELS[r.action] || r.action}
                  </span>
                  <span className={`text-xs font-medium ${r.content_match ? 'text-green-700' : 'text-red-700'}`}>
                    {r.content_match ? '✓ 内容一致' : '✗ 被篡改'}
                  </span>
                  <span className={`text-xs ${r.actor_verified ? 'text-green-700' : 'text-gray-400'}`}>
                    {r.actor_verified ? '✓ 主体已验证' : '主体未验证'}
                  </span>
                  <span className="text-xs text-gray-400 ml-auto">
                    {new Date(r.timestamp * 1000).toLocaleString()}
                  </span>
                </div>
                <div className="mt-1 text-xs text-gray-500 font-mono break-all">
                  主体 {short(r.actor)} · tx {short(r.tx_hash)}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
