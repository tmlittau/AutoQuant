<script lang="ts">
  import { link } from 'svelte-spa-router';
</script>

<div class="space-y-3 text-center mt-12">
  <h1 class="text-3xl font-semibold text-slate-900">Not found</h1>
  <p class="text-sm text-slate-500">No such route.</p>
  <a use:link href="/" class="inline-block text-sm text-blue-600 hover:underline">
    → Back to Dashboard
  </a>
</div>
