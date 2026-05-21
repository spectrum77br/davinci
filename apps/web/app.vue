<template>
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
  <!-- Global toast stack — populated via `useToasts()` from any page -->
  <AppToastStack />
  <!-- Pops a modal on the responsável's screen when an admin assigns
       (or reassigns) a tarefa. Polls /api/alerts every 10s. -->
  <TarefaNotification />
</template>
