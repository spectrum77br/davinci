<script setup lang="ts">
import { X } from 'lucide-vue-next'

const { toasts, dismiss } = useToasts()
</script>

<template>
  <div
    class="fixed top-4 right-4 z-[60] flex flex-col gap-2 w-[min(420px,calc(100vw-2rem))] pointer-events-none"
  >
    <TransitionGroup
      enter-active-class="transition duration-150 ease-out"
      enter-from-class="opacity-0 translate-x-4"
      enter-to-class="opacity-100 translate-x-0"
      leave-active-class="transition duration-100 ease-in"
      leave-from-class="opacity-100"
      leave-to-class="opacity-0"
    >
      <div
        v-for="t in toasts"
        :key="t.id"
        class="rounded-lg border-2 shadow-lg px-3 py-2 text-sm bg-background pointer-events-auto"
        :class="
          t.kind === 'success'
            ? 'border-emerald-400 bg-emerald-50'
            : t.kind === 'error'
              ? 'border-red-400 bg-red-50'
              : t.kind === 'warning'
                ? 'border-amber-400 bg-amber-50'
                : 'border-blue-400 bg-blue-50'
        "
      >
        <div class="flex items-start justify-between gap-2">
          <div class="flex-1 min-w-0">
            <div
              class="font-semibold"
              :class="
                t.kind === 'success'
                  ? 'text-emerald-800'
                  : t.kind === 'error'
                    ? 'text-red-800'
                    : t.kind === 'warning'
                      ? 'text-amber-800'
                      : 'text-blue-800'
              "
            >{{ t.title }}</div>
            <ul v-if="t.lines.length" class="mt-0.5 space-y-0.5 text-xs">
              <li
                v-for="(ln, i) in t.lines"
                :key="i"
                :class="
                  t.kind === 'success'
                    ? 'text-emerald-700'
                    : t.kind === 'error'
                      ? 'text-red-700'
                      : t.kind === 'warning'
                        ? 'text-amber-700'
                        : 'text-blue-700'
                "
              >{{ ln }}</li>
            </ul>
          </div>
          <button class="text-muted-foreground hover:text-foreground" @click="dismiss(t.id)">
            <X class="size-3.5" />
          </button>
        </div>
      </div>
    </TransitionGroup>
  </div>
</template>
