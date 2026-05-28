<script lang="ts">
  /**
   * Standalone login screen. Rendered by App.svelte when authStore.user is
   * null. After a successful login, fetchMe() updates the store, App.svelte
   * unmounts this and mounts the main Layout + Router.
   */
  import { login } from '../lib/auth';

  let username = $state('');
  let password = $state('');
  let submitting = $state(false);
  let error = $state<string | null>(null);

  async function submit(e: Event) {
    e.preventDefault();
    submitting = true;
    error = null;
    try {
      await login(username, password);
      // App.svelte will swap us out via the auth store.
    } catch (err: any) {
      error = err?.message ?? 'login failed';
    } finally {
      submitting = false;
    }
  }
</script>

<div class="min-h-screen flex items-center justify-center bg-slate-50 p-4">
  <div class="w-full max-w-sm bg-white border border-slate-200 rounded-xl shadow-sm p-6">
    <h1 class="text-xl font-semibold text-slate-900 mb-1">AutoQuant</h1>
    <p class="text-sm text-slate-500 mb-5">Sign in to continue.</p>

    <form onsubmit={submit} class="space-y-3">
      <label class="block">
        <span class="block text-xs font-medium text-slate-700 mb-1">Username</span>
        <input
          type="text"
          bind:value={username}
          autocomplete="username"
          required
          class="w-full px-3 py-1.5 border border-slate-300 rounded-md"
        />
      </label>
      <label class="block">
        <span class="block text-xs font-medium text-slate-700 mb-1">Password</span>
        <input
          type="password"
          bind:value={password}
          autocomplete="current-password"
          required
          class="w-full px-3 py-1.5 border border-slate-300 rounded-md"
        />
      </label>

      {#if error}
        <div class="bg-red-50 border border-red-200 text-red-700 rounded p-2 text-xs">
          {error}
        </div>
      {/if}

      <button
        type="submit"
        disabled={submitting || !username || !password}
        class="w-full mt-2 px-3 py-2 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
      >
        {submitting ? 'Signing in…' : 'Sign in'}
      </button>
    </form>

    <p class="mt-6 text-xs text-slate-400">
      Single-user app. Create the user with
      <code class="font-mono">manage.py createsuperuser</code>.
    </p>
  </div>
</div>
