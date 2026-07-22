import { useCallback, useEffect, useRef, useState } from 'react'
import { tokenUtils } from '../utils/token'
import ConfirmDialog from '../components/common/ConfirmDialog'

interface NodeState {
  index: number
  datadir: string
  p2p: number
  http: number
  ws: number | null
  authrpc: number
  signer: string
  pid: number | null
  running: boolean
  block_number: number | null
  peer_count: number | null
  mining: boolean | null
  isolated: boolean
  is_signer: boolean
  is_rpc_entry: boolean
}

interface ChainStatus {
  running: boolean
  producing: boolean
  latest_block: number | null
  seconds_since_block: number | null
  signers: string[]
  quorum: number
  online_signers: number
  proposals: Record<string, boolean>
  rpc_entry_port: number
  nodes: NodeState[]
}

interface Operation {
  op_id: string
  action: string
  state: 'running' | 'done' | 'failed'
  log: string[]
  error: { code: string; message: string } | null
}

interface Pending {
  title: string
  message: string
  confirmLabel: string
  danger: boolean
  run: () => void
}

const short = (addr: string) => `${addr.slice(0, 8)}…${addr.slice(-6)}`

// 与后端 _assert_quorum_preserved 同构的本地预判，只用于把按钮置灰并说明原因。
// 真正的拦截在后端（409），这里只是不让管理员白点一下。
function whyCannotStop(st: ChainStatus, n: NodeState): string | null {
  if (!n.running) return null
  if (n.is_rpc_entry) {
    return '这是后端结算的 RPC 入口节点（PRIVATE_RPC）。停掉它，链还在出块，但后端所有华币结算和任务完成接口会立刻失败。要下线它请用「停止整条链」。'
  }
  if (n.is_signer && st.online_signers >= st.quorum && st.online_signers - 1 < st.quorum) {
    return `停掉它会让在线签名者降到 ${st.online_signers - 1} 个，低于出块下限 ${st.quorum} 个，整条链会停止出块、后端所有华币结算随之失败。`
  }
  return null
}

function whyCannotRemove(st: ChainStatus, n: NodeState): string | null {
  if (n.is_rpc_entry) return '这是后端结算的 RPC 入口节点，不能删除。'
  if (n.is_signer) return '它还在签名者集合里。请先「罢免」再删除。'
  return whyCannotStop(st, n)
}

// 出块下限 = floor(N/2)+1。N-quorum 就是能同时坏掉几个：5→2、6→2、7→3。
// 所以 5 升到 6 不增加容错（仍只能坏 2 个），却要多一台常开；要提升容错得直接上 7。
const tolerated = (signerCount: number) => signerCount - (Math.floor(signerCount / 2) + 1)

export default function AdminChainPage() {
  const [status, setStatus] = useState<ChainStatus | null>(null)
  const [op, setOp] = useState<Operation | null>(null)
  const [opId, setOpId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [pending, setPending] = useState<Pending | null>(null)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const authHeaders = () => ({ Authorization: `Bearer ${tokenUtils.get()}` })

  const fetchStatus = useCallback(async () => {
    try {
      const resp = await fetch('/api/admin/chain/status', { headers: authHeaders() })
      if (!resp.ok) {
        const data = await resp.json()
        setError(data.detail?.error?.message || data.detail || '拉取链状态失败')
        return
      }
      setStatus(await resp.json())
      setError('')
    } catch {
      setError('网络错误，请确认后端服务已启动')
    }
  }, [])

  useEffect(() => {
    fetchStatus()
    timer.current = setInterval(fetchStatus, 3000)
    return () => {
      if (timer.current) clearInterval(timer.current)
    }
  }, [fetchStatus])

  // 有操作在跑时单独轮询它的进度与日志（起链要 30~45 秒，投票要等出块）
  useEffect(() => {
    if (!opId) return
    let cancelled = false
    const poll = async () => {
      try {
        const resp = await fetch(`/api/admin/chain/operations/${opId}`, { headers: authHeaders() })
        if (!resp.ok || cancelled) return
        const data: Operation = await resp.json()
        setOp(data)
        if (data.state !== 'running') {
          setOpId(null)
          fetchStatus()
        }
      } catch {
        /* 网络抖动，下个 tick 再试 */
      }
    }
    const id = setInterval(poll, 2000)
    poll()
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [opId, fetchStatus])

  const runOp = async (action: string, path: string, method = 'POST', body?: unknown) => {
    setError('')
    setPending(null)
    try {
      const resp = await fetch(`/api/admin/chain${path}`, {
        method,
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      const data = await resp.json()
      if (!resp.ok) {
        setError(data.detail?.error?.message || data.detail || `${action}失败`)
        return
      }
      setOp({ op_id: data.op_id, action, state: 'running', log: [], error: null })
      setOpId(data.op_id)
    } catch {
      setError('网络错误，请确认后端服务已启动')
    }
  }

  const busy = opId !== null
  const btn = 'rounded-lg px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40'

  if (!status) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 to-indigo-100 px-4 py-12">
        <div className="mx-auto max-w-6xl text-center text-gray-500">
          {error || '读取私链状态中…'}
        </div>
      </div>
    )
  }

  const signerCount = status.signers.length
  const proposalList = Object.entries(status.proposals || {})

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-indigo-100 px-4 py-12">
      <div className="mx-auto max-w-6xl space-y-6">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">私链管理</h1>
          <p className="mt-1 text-gray-600">
            geth Clique POA · 启停链与节点 · 增删节点 · 签名者投票 · 仅管理员可见
          </p>
        </div>

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-red-700">{error}</div>
        )}

        {/* 总览 */}
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Stat
            label="出块状态"
            value={status.producing ? '出块中' : status.running ? '已停摆' : '已停止'}
            tone={status.producing ? 'good' : status.running ? 'warn' : 'idle'}
            sub={
              status.seconds_since_block !== null
                ? `上个块 ${status.seconds_since_block} 秒前`
                : '无数据'
            }
          />
          <Stat label="最新块高" value={status.latest_block?.toLocaleString() ?? '—'} tone="idle" />
          <Stat
            label="在线签名者"
            value={`${status.online_signers} / ${signerCount}`}
            tone={status.online_signers >= status.quorum ? 'good' : 'bad'}
            sub={`出块下限 ${status.quorum}`}
          />
          <Stat
            label="容错余量"
            value={
              signerCount > 0 ? `还能坏 ${Math.max(0, status.online_signers - status.quorum)} 个` : '—'
            }
            tone={status.online_signers - status.quorum > 0 ? 'good' : 'warn'}
            sub={signerCount > 0 ? `满编可坏 ${tolerated(signerCount)} 个` : ''}
          />
        </div>

        {/* 链级操作 */}
        <div className="flex flex-wrap gap-3">
          <button
            disabled={busy}
            onClick={() => runOp('启动私链', '/start')}
            className={`${btn} bg-green-600 px-4 py-2 text-sm text-white hover:bg-green-700`}
          >
            启动私链
          </button>
          <button
            disabled={busy || !status.running}
            onClick={() =>
              setPending({
                title: '停止整条私链？',
                message:
                  '所有节点都会被停掉，链立刻停止出块。\n\n后端的华币结算（任务完成发奖、管理员铸币、钱包转账）在此期间会全部失败。这是唯一能停掉 RPC 入口节点的操作。',
                confirmLabel: '确认停止',
                danger: true,
                run: () => runOp('停止私链', '/stop', 'POST', { confirm: 'STOP' }),
              })
            }
            className={`${btn} bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-700`}
          >
            停止私链
          </button>
          <button
            disabled={busy}
            onClick={() => runOp('新增节点', '/nodes', 'POST', { make_signer: false })}
            className={`${btn} bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-700`}
          >
            新增节点（不出块）
          </button>
          <button
            disabled={busy || !status.running}
            onClick={() =>
              setPending({
                title: '新增节点并选为签名者？',
                message:
                  `新节点会先加入网络，再由现有签名者通过 clique 投票选入出块集合（要等出块才生效）。\n\n` +
                  `当前 ${signerCount} 个签名者：需 ${status.quorum} 个在线，能坏 ${tolerated(signerCount)} 个。\n` +
                  `选入后 ${signerCount + 1} 个签名者：需 ${Math.floor((signerCount + 1) / 2) + 1} 个在线，能坏 ${tolerated(signerCount + 1)} 个。\n\n` +
                  (tolerated(signerCount + 1) === tolerated(signerCount)
                    ? '注意：容错余量没有提升，但要求常开的节点多了一台。想真正提升容错，应该一次加到 7 个签名者。'
                    : '容错余量会提升。'),
                confirmLabel: '确认新增并投票',
                danger: false,
                run: () => runOp('新增签名节点', '/nodes', 'POST', { make_signer: true }),
              })
            }
            className={`${btn} border border-indigo-300 bg-white px-4 py-2 text-sm text-indigo-700 hover:bg-indigo-50`}
          >
            新增节点（出块）
          </button>
        </div>

        {/* 操作进度 */}
        {op && (
          <div className="rounded-xl bg-white p-4 shadow-lg">
            <div className="flex items-center gap-2">
              {op.state === 'running' && (
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-indigo-500 border-b-transparent" />
              )}
              <span className="font-semibold text-gray-900">{op.action}</span>
              <span
                className={`rounded px-2 py-0.5 text-xs ${
                  op.state === 'done'
                    ? 'bg-green-100 text-green-800'
                    : op.state === 'failed'
                      ? 'bg-red-100 text-red-800'
                      : 'bg-indigo-100 text-indigo-800'
                }`}
              >
                {op.state === 'done' ? '完成' : op.state === 'failed' ? '失败' : '执行中'}
              </span>
            </div>
            {op.log.length > 0 && (
              <pre className="mt-3 max-h-52 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-3 font-mono text-xs text-gray-700">
                {op.log.join('\n')}
              </pre>
            )}
            {op.error && <div className="mt-2 text-sm text-red-700">{op.error.message}</div>}
          </div>
        )}

        {/* 待生效的签名者提案 */}
        {proposalList.length > 0 && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
            <div className="text-sm font-semibold text-amber-900">投票进行中（要等提案方出块才生效）</div>
            <div className="mt-2 space-y-1">
              {proposalList.map(([addr, authorize]) => (
                <div key={addr} className="flex items-center gap-3 text-sm text-amber-800">
                  <span className="font-mono">{short(addr)}</span>
                  <span>{authorize ? '选入签名者' : '罢免签名者'}</span>
                  <button
                    disabled={busy}
                    onClick={() => runOp('撤销提案', `/signers/proposals/${addr}`, 'DELETE')}
                    className={`${btn} border border-amber-300 bg-white text-amber-800 hover:bg-amber-100`}
                  >
                    撤销
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 节点表格 */}
        <div className="overflow-x-auto rounded-xl bg-white shadow-lg">
          <table className="w-full text-sm">
            <thead className="border-b border-gray-200 bg-gray-50 text-left text-xs uppercase text-gray-500">
              <tr>
                <th className="px-4 py-3">节点</th>
                <th className="px-4 py-3">状态</th>
                <th className="px-4 py-3">peers</th>
                <th className="px-4 py-3">块高</th>
                <th className="px-4 py-3">签名者地址</th>
                <th className="px-4 py-3">端口</th>
                <th className="px-4 py-3 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {status.nodes.map((n) => {
                const stopReason = whyCannotStop(status, n)
                const removeReason = whyCannotRemove(status, n)
                const badge = !n.running
                  ? { label: '已停止', cls: 'bg-gray-200 text-gray-700' }
                  : n.isolated
                    ? { label: '运行中 · 孤立', cls: 'bg-amber-100 text-amber-800' }
                    : { label: '运行中 · 已同步', cls: 'bg-green-100 text-green-800' }
                return (
                  <tr key={n.index} className="text-gray-700">
                    <td className="px-4 py-3">
                      <div className="font-semibold text-gray-900">node{n.index}</div>
                      <div className="text-xs text-gray-400">{n.datadir}\</div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`rounded px-2 py-0.5 text-xs ${badge.cls}`}>{badge.label}</span>
                      {n.isolated && (
                        <div className="mt-1 text-xs text-amber-700">0 peer，已掉出 quorum</div>
                      )}
                    </td>
                    <td className="px-4 py-3">{n.running ? n.peer_count : '—'}</td>
                    <td className="px-4 py-3">{n.block_number?.toLocaleString() ?? '—'}</td>
                    <td className="px-4 py-3">
                      <div className="font-mono text-xs">{short(n.signer)}</div>
                      <div className="mt-1 flex gap-1">
                        {n.is_signer && (
                          <span className="rounded bg-purple-100 px-1.5 py-0.5 text-xs text-purple-800">
                            签名者
                          </span>
                        )}
                        {n.is_rpc_entry && (
                          <span className="rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-800">
                            后端 RPC 入口
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-500">
                      p2p {n.p2p} · http {n.http}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap justify-end gap-1.5">
                        {!n.running ? (
                          <button
                            disabled={busy}
                            onClick={() => runOp(`启动 node${n.index}`, `/nodes/${n.index}/start`)}
                            className={`${btn} bg-green-600 text-white hover:bg-green-700`}
                          >
                            启动
                          </button>
                        ) : (
                          <button
                            disabled={busy || stopReason !== null}
                            title={stopReason ?? ''}
                            onClick={() =>
                              setPending({
                                title: `停止 node${n.index}？`,
                                message: n.is_signer
                                  ? `它是签名者。停掉后在线签名者变为 ${status.online_signers - 1} 个（出块下限 ${status.quorum}）。`
                                  : '它不是签名者，停掉对出块没有影响。',
                                confirmLabel: '确认停止',
                                danger: true,
                                run: () => runOp(`停止 node${n.index}`, `/nodes/${n.index}/stop`),
                              })
                            }
                            className={`${btn} bg-gray-700 text-white hover:bg-gray-800`}
                          >
                            停止
                          </button>
                        )}

                        {n.is_signer ? (
                          <button
                            disabled={busy || !status.running}
                            onClick={() =>
                              setPending({
                                title: `罢免 node${n.index} 的签名者身份？`,
                                message: `通过 clique 投票把它移出出块集合，要等出块才生效。\n\n签名者会从 ${signerCount} 个变成 ${signerCount - 1} 个，出块下限从 ${status.quorum} 变成 ${Math.floor((signerCount - 1) / 2) + 1}。`,
                                confirmLabel: '确认罢免',
                                danger: true,
                                run: () =>
                                  runOp(`罢免 node${n.index}`, '/signers', 'POST', {
                                    address: n.signer,
                                    authorize: false,
                                  }),
                              })
                            }
                            className={`${btn} border border-gray-300 bg-white text-gray-700 hover:bg-gray-50`}
                          >
                            罢免
                          </button>
                        ) : (
                          <button
                            disabled={busy || !n.running || !status.running}
                            onClick={() =>
                              setPending({
                                title: `把 node${n.index} 选为签名者？`,
                                message:
                                  `通过 clique 投票把它加入出块集合，要等出块才生效。\n\n` +
                                  `签名者 ${signerCount} → ${signerCount + 1} 个，出块下限 ${status.quorum} → ${Math.floor((signerCount + 1) / 2) + 1} 个。\n` +
                                  (tolerated(signerCount + 1) === tolerated(signerCount)
                                    ? `容错余量不变（仍只能坏 ${tolerated(signerCount)} 个），但要多一台常开。想提升容错应加到 7 个签名者。`
                                    : `容错余量 ${tolerated(signerCount)} → ${tolerated(signerCount + 1)} 个。`),
                                confirmLabel: '确认选入',
                                danger: false,
                                run: () =>
                                  runOp(`选入 node${n.index}`, '/signers', 'POST', {
                                    address: n.signer,
                                    authorize: true,
                                  }),
                              })
                            }
                            className={`${btn} border border-gray-300 bg-white text-gray-700 hover:bg-gray-50`}
                          >
                            选为签名者
                          </button>
                        )}

                        <button
                          disabled={busy || removeReason !== null}
                          title={removeReason ?? ''}
                          onClick={() =>
                            setPending({
                              title: `删除 node${n.index}？`,
                              message: `停掉进程、从其余节点摘除 peer、移出注册表。\n\n磁盘上的 ${n.datadir}\\ 目录会保留（可以再加回来恢复），不会删数据。`,
                              confirmLabel: '确认删除',
                              danger: true,
                              run: () => runOp(`删除 node${n.index}`, `/nodes/${n.index}`, 'DELETE'),
                            })
                          }
                          className={`${btn} border border-red-200 bg-white text-red-600 hover:bg-red-50`}
                        >
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {pending && (
        <ConfirmDialog
          title={pending.title}
          message={pending.message}
          confirmLabel={pending.confirmLabel}
          danger={pending.danger}
          onConfirm={pending.run}
          onCancel={() => setPending(null)}
        />
      )}
    </div>
  )
}

function Stat({
  label,
  value,
  tone,
  sub,
}: {
  label: string
  value: string
  tone: 'good' | 'warn' | 'bad' | 'idle'
  sub?: string
}) {
  const color = {
    good: 'text-green-600',
    warn: 'text-amber-600',
    bad: 'text-red-600',
    idle: 'text-gray-900',
  }[tone]
  return (
    <div className="rounded-xl bg-white p-4 shadow-lg">
      <div className="text-xs uppercase text-gray-500">{label}</div>
      <div className={`mt-1 text-2xl font-bold ${color}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-gray-400">{sub}</div>}
    </div>
  )
}
