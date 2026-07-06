import { useState, useEffect, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Search, X, Filter, ChevronDown, Sparkles } from 'lucide-react'
import { useAuth } from '../context/AuthContext'

interface Task {
  id: number
  description: string
  status: string
  task_type: string
  reward: number
  publisher_id: number
  agent_id?: number
  created_at: string
}

const STATUS_LABELS: Record<string, string> = {
  OPEN: '开放中', ACCEPTED: '已接单', SUBMITTED: '已提交',
  VERIFIED: '已验证', COMPLETED: '已完成', FAILED: '失败', DISPUTED: '申诉中',
}
// 软件工程任务类型（与后端 TaskType 8 类对应）
const TYPE_LABELS: Record<string, string> = {
  REQUIREMENT_ANALYSIS: '需求分析', ARCHITECTURE_DESIGN: '架构设计',
  CODE_DEVELOPMENT: '代码开发', CODE_REVIEW: '代码评审',
  TEST_CASE_DESIGN: '测试用例设计', TEST_AUTOMATION: '自动化测试',
  DEPLOYMENT_OPS: '部署运维', DOCUMENTATION: '技术文档',
}

export default function TasksPage() {
  const { token } = useAuth()
  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false)
  const [filters, setFilters] = useState({ status: '', task_type: '', minReward: 0, maxReward: 10000 })
  const [page, setPage] = useState(0)
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const limit = 12

  useEffect(() => {
    const fetchTasks = async () => {
      setLoading(true)
      try {
        const params = new URLSearchParams()
        if (filters.status) params.append('status', filters.status)
        if (filters.task_type) params.append('task_type', filters.task_type)
        params.append('skip', String(page * limit))
        params.append('limit', String(limit))
        const response = await fetch(`/api/tasks?${params}`, {
          headers: {
            ...(token ? { 'Authorization': `Bearer ${token}` } : {})
          }
        })
        const data = await response.json()
        setTasks(Array.isArray(data) ? data : (data.data || []))
      } catch (error) {
        console.error('加载任务失败:', error)
      } finally {
        setLoading(false)
      }
    }
    fetchTasks()
  }, [page, filters.status, filters.task_type, token])

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(searchQuery), 300)
    return () => clearTimeout(timer)
  }, [searchQuery])

  const filteredTasks = useMemo(() => {
    let result = tasks
    if (debouncedSearch.trim()) {
      const q = debouncedSearch.toLowerCase()
      result = result.filter(t => t.description.toLowerCase().includes(q) || t.task_type.toLowerCase().includes(q))
    }
    result = result.filter(t => t.reward / 1e18 >= filters.minReward && t.reward / 1e18 <= filters.maxReward)
    return result
  }, [tasks, debouncedSearch, filters.minReward, filters.maxReward])

  const getStatusColor = (status: string) => {
    const colors: Record<string, string> = {
      OPEN: 'bg-green-500 text-white', ACCEPTED: 'bg-blue-500 text-white', SUBMITTED: 'bg-yellow-500 text-white',
      VERIFIED: 'bg-purple-500 text-white', COMPLETED: 'bg-gray-500 text-white', FAILED: 'bg-red-500 text-white',
    }
    return colors[status] || 'bg-gray-500 text-white'
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50 to-indigo-50 relative">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 relative z-10">
        <div className="flex justify-between items-center mb-8">
          <div>
            <h1 className="text-4xl font-bold bg-gradient-to-r from-indigo-600 to-purple-600 bg-clip-text text-transparent">任务市场</h1>
            <p className="text-gray-600 mt-2">发现并接取高价值任务</p>
          </div>
          <Link to="/tasks/create" className="px-6 py-3 bg-gradient-to-r from-indigo-600 to-purple-600 text-white rounded-xl hover:shadow-lg transition-all flex items-center gap-2">
            <Sparkles size={20} /> 发布任务
          </Link>
        </div>

        <div className="backdrop-blur-xl bg-white/70 rounded-2xl shadow-xl border border-white/20 p-6 mb-6">
          <div className="relative mb-4">
            <Search size={24} className="absolute left-4 top-1/2 -translate-y-1/2 text-indigo-400" />
            <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)} placeholder="搜索任务描述、类型…" className="w-full pl-14 pr-14 py-4 text-lg border-2 border-indigo-100 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white/50" />
            {searchQuery && <button onClick={() => { setSearchQuery(''); setDebouncedSearch('') }} className="absolute right-4 top-1/2 -translate-y-1/2"><X size={24} className="text-gray-400" /></button>}
          </div>
          <button onClick={() => setShowAdvancedFilters(!showAdvancedFilters)} className="flex items-center gap-2 text-indigo-600 font-medium">
            <Filter size={18} /> 高级筛选 <ChevronDown size={18} className={`transition-transform ${showAdvancedFilters ? 'rotate-180' : ''}`} />
          </button>
          {showAdvancedFilters && (
            <div className="grid grid-cols-2 gap-4 mt-4 pt-4 border-t border-indigo-100">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">状态</label>
                <select value={filters.status} onChange={e => setFilters({ ...filters, status: e.target.value })} className="w-full px-4 py-2 border border-indigo-100 rounded-lg">
                  <option value="">全部状态</option>
                  {['OPEN','ACCEPTED','SUBMITTED','VERIFIED','COMPLETED','FAILED'].map(s => <option key={s} value={s}>{STATUS_LABELS[s] || s}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">任务类型</label>
                <select value={filters.task_type} onChange={e => setFilters({ ...filters, task_type: e.target.value })} className="w-full px-4 py-2 border border-indigo-100 rounded-lg">
                  <option value="">全部类型</option>
                  {Object.keys(TYPE_LABELS).map(t => <option key={t} value={t}>{TYPE_LABELS[t]}</option>)}
                </select>
              </div>
            </div>
          )}
        </div>

        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {[...Array(6)].map((_, i) => (
              <div key={i} className="bg-white/70 rounded-2xl shadow-xl p-6 animate-pulse">
                <div className="h-4 bg-gray-300 rounded w-3/4 mb-4"></div>
                <div className="h-20 bg-gray-300 rounded mb-4"></div>
                <div className="h-8 bg-gray-300 rounded w-24"></div>
              </div>
            ))}
          </div>
        ) : filteredTasks.length === 0 ? (
          <div className="bg-white/70 rounded-2xl shadow-xl p-12 text-center">
            <div className="w-24 h-24 mx-auto mb-4 bg-gray-100 rounded-full flex items-center justify-center">
              <Search size={40} className="text-gray-400" />
            </div>
            <p className="text-gray-600 mt-4">{debouncedSearch ? '未找到匹配的任务' : '暂无任务'}</p>
          </div>
        ) : (
          <>
            {debouncedSearch && <div className="mb-4 text-sm text-gray-600">找到 <span className="font-semibold text-indigo-600">{filteredTasks.length}</span> 个匹配任务</div>}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {filteredTasks.map(task => (
                <Link key={task.id} to={`/tasks/${task.id}`} className="group bg-white/70 rounded-2xl shadow-xl border-2 border-transparent hover:border-indigo-300 p-6 hover:shadow-2xl transition-all block">
                  <div className="flex items-center gap-2 mb-3">
                    <span className={`px-3 py-1 rounded-full text-xs font-bold ${getStatusColor(task.status)}`}>{STATUS_LABELS[task.status] || task.status}</span>
                    <span className="px-3 py-1 rounded-full text-xs font-medium bg-gray-100 text-gray-700">{TYPE_LABELS[task.task_type] || task.task_type}</span>
                  </div>
                  <p className="text-gray-900 font-medium line-clamp-3 mb-4">{task.description}</p>
                  <div className="flex justify-between items-end pt-4 border-t border-gray-200">
                    <div>
                      <p className="text-xs text-gray-500">奖励</p>
                      <p className="text-2xl font-bold text-indigo-600">{Number(task.reward) / 1e18} <span className="text-xs text-gray-500">华币</span></p>
                    </div>
                    <div className="text-right text-xs text-gray-400">
                      <p>任务 #{task.id}</p>
                      <p>{new Date(task.created_at).toLocaleDateString()}</p>
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          </>
        )}

        {!loading && filteredTasks.length > 0 && (
          <div className="mt-8 flex justify-center gap-2">
            <button onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0} className="px-6 py-3 bg-white border border-indigo-100 rounded-xl disabled:opacity-50 font-medium">上一页</button>
            <span className="px-6 py-3 bg-white border border-indigo-200 rounded-xl font-medium">第 {page + 1} 页</span>
            <button onClick={() => setPage(page + 1)} disabled={tasks.length < limit} className="px-6 py-3 bg-white border border-indigo-100 rounded-xl disabled:opacity-50 font-medium">下一页</button>
          </div>
        )}
      </div>
    </div>
  )
}
