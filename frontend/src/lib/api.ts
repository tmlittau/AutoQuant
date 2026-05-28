/**
 * Typed API client.
 *
 * Generated paths/types live in ./api-types.ts; regenerate after a backend
 * schema change with `npm run gen:api` (snapshot openapi.json must be fresh).
 * The OpenAPI spec from django-ninja includes the `/api` prefix in its path
 * keys, so we leave baseUrl unset and pass full paths.
 *
 * Two middleware hooks bolt on session auth:
 *  - onRequest: add ``X-CSRFToken`` header on mutations
 *  - onResponse: if any response is 401, clear the auth store so App.svelte
 *    re-renders the login screen
 */

import createClient from 'openapi-fetch';
import type { paths } from './api-types';
import { authStore, getCookie } from './auth';

export const api = createClient<paths>({ credentials: 'same-origin' });

const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

api.use({
  onRequest({ request }) {
    if (UNSAFE_METHODS.has(request.method.toUpperCase())) {
      const token = getCookie('csrftoken');
      if (token) request.headers.set('X-CSRFToken', token);
    }
    return request;
  },
  onResponse({ response }) {
    if (response.status === 401) {
      // Session expired or never had one -- App.svelte will re-render the
      // login screen as soon as the auth store changes.
      authStore.set({ user: null, checked: true });
    }
    return response;
  },
});

/** Light wrapper that throws on error and returns just the data. */
export async function apiGet<P extends keyof paths>(
  path: P,
  init?: Parameters<typeof api.GET<P>>[1],
): Promise<NonNullable<Awaited<ReturnType<typeof api.GET<P>>>['data']>> {
  // @ts-expect-error - openapi-fetch's generic type is fine here
  const { data, error } = await api.GET(path, init);
  if (error) {
    const detail = (error as any)?.detail ?? `API ${path} failed`;
    throw new Error(detail);
  }
  return data as any;
}
