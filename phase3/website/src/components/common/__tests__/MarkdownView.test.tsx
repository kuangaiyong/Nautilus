import { render } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import MarkdownView from '../MarkdownView'

describe('MarkdownView', () => {
  it('将 markdown 渲染为格式化 HTML 元素，而非源码文本', () => {
    const md = '### 测试标题\n\n这是一段 **加粗文字** 说明。\n\n- 列表项一\n- 列表项二'
    const { container } = render(<MarkdownView>{md}</MarkdownView>)

    // 标题渲染为 <h3>（而非把 "### 测试标题" 当纯文本）
    expect(container.querySelector('h3')?.textContent).toBe('测试标题')
    // 加粗渲染为 <strong>
    expect(container.querySelector('strong')?.textContent).toBe('加粗文字')
    // 列表渲染为 <ul><li>×2
    const items = container.querySelectorAll('li')
    expect(items.length).toBe(2)
    expect(items[0].textContent).toBe('列表项一')

    // 输出中不应残留原始 markdown 语法符号
    expect(container.textContent).not.toContain('###')
    expect(container.textContent).not.toContain('**')
  })

  it('渲染 GFM 表格', () => {
    const md = '| 用例 | 预期 |\n|---|---|\n| TC-01 | 成功 |'
    const { container } = render(<MarkdownView>{md}</MarkdownView>)
    expect(container.querySelector('table')).toBeTruthy()
    expect(container.querySelectorAll('td').length).toBe(2)
  })
})
