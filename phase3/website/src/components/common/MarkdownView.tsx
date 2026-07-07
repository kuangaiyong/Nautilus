import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * 将 markdown 文本渲染为格式化内容（标题/列表/加粗/表格/代码等），而非展示源码。
 * 用 Tailwind 逐元素设样式（项目未装 typography 插件）。node 在 rest 前，TS 豁免未用。
 */
const components: Components = {
  h1: ({ node, ...p }) => <h1 className="text-lg font-bold mt-4 mb-2 first:mt-0" {...p} />,
  h2: ({ node, ...p }) => <h2 className="text-base font-bold mt-4 mb-2 first:mt-0" {...p} />,
  h3: ({ node, ...p }) => <h3 className="text-sm font-semibold mt-3 mb-1.5" {...p} />,
  h4: ({ node, ...p }) => <h4 className="text-sm font-semibold mt-2 mb-1" {...p} />,
  p: ({ node, ...p }) => <p className="mb-2" {...p} />,
  ul: ({ node, ...p }) => <ul className="list-disc pl-5 mb-2 space-y-1" {...p} />,
  ol: ({ node, ...p }) => <ol className="list-decimal pl-5 mb-2 space-y-1" {...p} />,
  li: ({ node, ...p }) => <li {...p} />,
  strong: ({ node, ...p }) => <strong className="font-semibold text-gray-900" {...p} />,
  em: ({ node, ...p }) => <em className="italic" {...p} />,
  a: ({ node, ...p }) => <a className="text-indigo-600 hover:underline" target="_blank" rel="noopener noreferrer" {...p} />,
  blockquote: ({ node, ...p }) => <blockquote className="border-l-4 border-gray-300 pl-3 text-gray-600 my-2" {...p} />,
  hr: ({ node, ...p }) => <hr className="my-3 border-gray-200" {...p} />,
  table: ({ node, ...p }) => (
    <div className="overflow-x-auto my-2">
      <table className="min-w-full border-collapse text-xs" {...p} />
    </div>
  ),
  thead: ({ node, ...p }) => <thead className="bg-gray-100" {...p} />,
  th: ({ node, ...p }) => <th className="border border-gray-200 px-2 py-1 text-left font-semibold" {...p} />,
  td: ({ node, ...p }) => <td className="border border-gray-200 px-2 py-1 align-top" {...p} />,
  pre: ({ node, ...p }) => <pre className="bg-gray-800 text-gray-100 rounded p-3 overflow-x-auto text-xs my-2" {...p} />,
  code: ({ node, className, ...p }) =>
    className?.includes('language-')
      ? <code className={className} {...p} />            // 代码块：由 pre 统一深色底
      : <code className="px-1 py-0.5 bg-gray-200 rounded text-xs font-mono" {...p} />,  // 行内代码
}

export default function MarkdownView({ children }: { children: string }) {
  return (
    <div className="text-sm text-gray-800 leading-relaxed break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  )
}
