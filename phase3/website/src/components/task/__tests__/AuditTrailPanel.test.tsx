import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import AuditTrailPanel, { type AuditRecordItem } from '../AuditTrailPanel'

const rec = (action: string, seq: number, content_match = true, actor_verified = true): AuditRecordItem => ({
  action, actor: `0x${action.toLowerCase()}00000000000000000000000000000000000`,
  onchain_hash: '0x1', recomputed_hash: content_match ? '0x1' : '0x2',
  content_match, actor_verified, matched_review_id: null,
  seq, timestamp: 1700000000, tx_hash: `0xtx${seq}00000000000000000000000000000000`,
})

const verified = {
  task_id: 1, onchain_records: 4, verified: true, tampered_actions: [],
  records: [rec('PUBLISH', 1), rec('ACCEPT', 2), rec('SUBMIT', 3), rec('COMPLETE', 4)],
}

const tampered = {
  task_id: 1, onchain_records: 4, verified: false, tampered_actions: ['SUBMIT'],
  records: [rec('PUBLISH', 1), rec('ACCEPT', 2), rec('SUBMIT', 3, false), rec('COMPLETE', 4)],
}

const empty = { task_id: 1, onchain_records: 0, verified: false, tampered_actions: [], records: [] }

function stubFetch(json: any, ok = true) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok, json: async () => json }))
}

describe('AuditTrailPanel', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('全部一致时显示绿色结论 + 动作中文 + 全部记录', async () => {
    stubFetch(verified)
    render(<AuditTrailPanel taskId={1} />)
    const verdict = await screen.findByTestId('audit-verdict')
    expect(verdict).toHaveTextContent('全部一致')
    expect(screen.getByText('发布')).toBeInTheDocument()
    expect(screen.getByText('抢单')).toBeInTheDocument()
    expect(screen.getByText('完成')).toBeInTheDocument()
    expect(screen.getAllByTestId('audit-record')).toHaveLength(4)
  })

  it('检测到篡改时显示红色结论 + 被篡改动作 + 该条标记', async () => {
    stubFetch(tampered)
    render(<AuditTrailPanel taskId={1} />)
    const verdict = await screen.findByTestId('audit-verdict')
    expect(verdict).toHaveTextContent('检测到篡改')
    expect(verdict).toHaveTextContent('SUBMIT')
    expect(screen.getByText('✗ 被篡改')).toBeInTheDocument()
  })

  it('无存证记录时显示占位文案', async () => {
    stubFetch(empty)
    render(<AuditTrailPanel taskId={1} />)
    expect(await screen.findByText(/暂无链上存证记录/)).toBeInTheDocument()
  })

  it('加载失败时显示后端错误信息', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: 'boom' }) }))
    render(<AuditTrailPanel taskId={1} />)
    expect(await screen.findByText('boom')).toBeInTheDocument()
  })
})
