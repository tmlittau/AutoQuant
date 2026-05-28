/**
 * SPA-side auth: a tiny store + helpers to login, logout, check status, and
 * prime the CSRF cookie. The backend uses Django session cookies; the SPA
 * just needs to read ``csrftoken`` from ``document.cookie`` and send it as
 * ``X-CSRFToken`` on mutating requests (handled by lib/api.ts middleware).
 */

import { writable, get } from 'svelte/store';

export type AuthState = { user: string | null; checked: boolean };

export const authStore = writable<AuthState>({ user: null, checked: false });

/** Read a cookie value by name. */
export function getCookie(name: string): string {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return m ? decodeURIComponent(m[1]) : '';
}

let csrfPrimed = false;

/** Hit ``/api/csrf`` once on boot so the ``csrftoken`` cookie is set before
 *  the first mutation. Safe to call multiple times -- second call is a no-op. */
export async function ensureCsrf(): Promise<void> {
  if (csrfPrimed) return;
  await fetch('/api/csrf', { credentials: 'same-origin' });
  csrfPrimed = true;
}

/** Probe the backend for auth status; update the store. */
export async function fetchMe(): Promise<boolean> {
  const r = await fetch('/api/auth/me', { credentials: 'same-origin' });
  if (!r.ok) {
    authStore.set({ user: null, checked: true });
    return false;
  }
  const data = (await r.json()) as { authenticated: boolean; username?: string };
  authStore.set({
    user: data.authenticated ? data.username ?? null : null,
    checked: true,
  });
  return !!data.authenticated;
}

/** POST /auth/login. Throws on failure. */
export async function login(username: string, password: string): Promise<void> {
  await ensureCsrf();
  const r = await fetch('/api/auth/login', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCookie('csrftoken'),
    },
    credentials: 'same-origin',
    body: JSON.stringify({ username, password }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error((body as any).detail ?? `login failed (HTTP ${r.status})`);
  }
  // After a successful login Django rotates the csrftoken cookie.
  csrfPrimed = true;
  await fetchMe();
}

/** POST /auth/logout. Best-effort; always clears the store. */
export async function logout(): Promise<void> {
  try {
    await fetch('/api/auth/logout', {
      method: 'POST',
      headers: { 'X-CSRFToken': getCookie('csrftoken') },
      credentials: 'same-origin',
    });
  } catch {
    /* ignore */
  }
  authStore.set({ user: null, checked: true });
}

/** Convenience for non-Svelte consumers. */
export function currentUser(): string | null {
  return get(authStore).user;
}
