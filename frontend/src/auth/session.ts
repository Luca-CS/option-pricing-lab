// The signed-in user: GET /auth/me (roles, permissions, CSRF token), the login and the logout. A 401 means no session.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type Me, ok, setCsrf } from "../api/client";

export const ME = ["auth", "me"] as const;

export async function fetchMe(): Promise<Me | null> {
  try {
    const me = await ok(api.GET("/api/v1/auth/me"));
    setCsrf(me.csrf_token);
    return me;
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      setCsrf(null);
      return null;
    }
    throw e;
  }
}

export function useMe() {
  return useQuery({ queryKey: ME, queryFn: fetchMe, staleTime: 60_000, retry: false });
}

export function useSignIn() {
  const queries = useQueryClient();
  return useMutation({
    mutationFn: async (body: { name: string; password: string }) => ok(api.POST("/api/v1/auth/login", { body })),
    onSuccess: (me) => {
      setCsrf(me.csrf_token);
      queries.setQueryData(ME, me);
    },
  });
}

export function useSignOut() {
  const queries = useQueryClient();
  return useMutation({
    mutationFn: async () => ok(api.POST("/api/v1/auth/logout", { body: null })),
    onSettled: () => {
      setCsrf(null);
      queries.clear();
      queries.setQueryData(ME, null);
    },
  });
}

/** May this user call that operation? The server refuses anyway; the front hides what it would refuse. */
export function can(me: Me | null | undefined, operation: string): boolean {
  return me?.permissions.includes(operation) ?? false;
}
