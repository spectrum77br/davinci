<script setup lang="ts">
// Marca do DaVinci (logo nova, 14/09/2026): "D" azul com o check branco.
// Desenhada em SVG a partir da arte enviada pelo Eduardo — a mesma forma
// alimenta o menu lateral, a tela de login e os ícones em /public.
</script>

<template>
  <svg viewBox="0 0 100 100" role="img" aria-label="DaVinci" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <clipPath id="davinci-logo-d">
        <path d="M12 6H50A44 44 0 0 1 50 94H12Z" />
      </clipPath>
    </defs>
    <path d="M12 6H50A44 44 0 0 1 50 94H12Z" fill="#2B5BE8" />
    <polyline
      points="19,37 49,73 82,36"
      fill="none"
      stroke="#FFFFFF"
      stroke-width="14"
      stroke-linecap="round"
      stroke-linejoin="round"
      clip-path="url(#davinci-logo-d)"
    />
  </svg>
</template>
