type AuthScopedCacheClearer = () => void;

const authScopedCacheClearers = new Set<AuthScopedCacheClearer>();

export function registerAuthScopedCacheClearer(
  clearer: AuthScopedCacheClearer,
): () => void {
  authScopedCacheClearers.add(clearer);
  return () => {
    authScopedCacheClearers.delete(clearer);
  };
}

export function clearAuthScopedCaches(): void {
  for (const clearer of authScopedCacheClearers) {
    try {
      clearer();
    } catch (error) {
      console.warn("Failed to clear auth-scoped cache:", error);
    }
  }
}
