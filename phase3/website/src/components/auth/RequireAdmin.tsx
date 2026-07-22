import { Navigate, useLocation } from 'react-router-dom'
import { useAuthCheck } from '../../hooks/useAuth'

interface RequireAdminProps {
  children: React.ReactNode
}

// 管理员路由守卫。isLoading 期间的 spinner 必须保留：user 来自异步的 /auth/me，
// 少了它，管理员一刷新页面会在 user 到位之前被当成非管理员踢走。
const RequireAdmin = ({ children }: RequireAdminProps) => {
  const { user, isAuthenticated, isLoading } = useAuthCheck()
  const location = useLocation()

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  if (!user?.is_admin) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}

export default RequireAdmin
