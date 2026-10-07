<script setup lang="ts">
// Ícone da plataforma no Atendimento (28/09/2026) — a barra de lojas, o canto
// do avatar e o cabeçalho da conversa usam este desenho, como o Duoke faz com
// os logos. Desenho próprio e simples em SVG (nada baixado da internet, nada
// de arquivo de logo): só precisa ser reconhecível em 12–20 px —
// Shopee = sacolinha laranja com "S"; TikTok = nota musical com a sombra
// ciano/vermelha; Mercado Livre = o logo de verdade (imagem em public/logos/,
// 07/10/2026 — a única exceção ao "desenho próprio"); Amazon = "a"
// com o sorriso laranja; Instagram = câmera no quadrado degradê; Temu = "T"
// branco no quadrado laranja; AliExpress = "Ae" branco no quadrado vermelho
// (30/09/2026 — só a cor e a inicial, nada do logo das marcas); Magalu = "M"
// branco no quadrado azul (30/09/2026, idem: o azul da marca e a inicial).
// Facebook = "f" branco no círculo azul; Site = globo branco no quadrado
// verde-azulado (02/10/2026: os sites Charlots e Uranyx — não é marca).
import { computed, useId } from 'vue'

// Nome para o leitor de tela. Cópia curta de propósito: importar de
// AtendimentoPlataforma.vue faria um ciclo (o chip de lá usa este ícone).
const NOMES: Record<string, string> = {
  shopee: 'Shopee',
  ml: 'Mercado Livre',
  tiktok: 'TikTok',
  amazon: 'Amazon',
  magalu: 'Magalu',
  temu: 'Temu',
  aliexpress: 'AliExpress',
  instagram: 'Instagram',
  facebook: 'Facebook',
  site: 'Site',
}

const props = withDefaults(defineProps<{
  plataforma: string | null | undefined
  tamanho?: number
  // Ao lado de um texto que já diz a plataforma: o leitor de tela pula o ícone.
  decorativo?: boolean
}>(), { tamanho: 16, decorativo: false })

const cod = computed(() => (props.plataforma || '').trim().toLowerCase())
const nome = computed(() => NOMES[cod.value] || cod.value || 'plataforma')
// O degradê do Instagram precisa de id único na página (vários ícones juntos).
const gradId = `atd-ig-${useId()}`
const letra = computed(() => (cod.value[0] || '?').toUpperCase())
</script>

<template>
  <svg
    :width="tamanho"
    :height="tamanho"
    viewBox="0 0 24 24"
    class="inline-block shrink-0"
    :role="decorativo ? undefined : 'img'"
    :aria-hidden="decorativo ? 'true' : undefined"
    :aria-label="decorativo ? undefined : nome"
  >
    <title v-if="!decorativo">{{ nome }}</title>

    <!-- Shopee: sacolinha laranja com o "S" -->
    <template v-if="cod === 'shopee'">
      <path d="M8.2 7.6V6.6a3.8 3.8 0 0 1 7.6 0v1" fill="none" stroke="#EE4D2D" stroke-width="1.9" stroke-linecap="round" />
      <path d="M3.9 7.4h16.2l-1.05 12.5a2 2 0 0 1-2 1.85H6.95a2 2 0 0 1-2-1.85z" fill="#EE4D2D" />
      <text x="12" y="18.4" text-anchor="middle" font-size="10.5" font-weight="700" font-family="Arial, Helvetica, sans-serif" fill="#fff">S</text>
    </template>

    <!-- TikTok: nota musical com a "sombra" ciano e vermelha -->
    <template v-else-if="cod === 'tiktok'">
      <g fill="none" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <g stroke="#25F4EE" transform="translate(-0.9 -0.7)">
          <path d="M12.6 3.6v11.9a3.3 3.3 0 1 1-3.3-3.3" />
          <path d="M12.6 3.6c.5 2.5 2.3 4.1 4.9 4.3" />
        </g>
        <g stroke="#FE2C55" transform="translate(0.9 0.7)">
          <path d="M12.6 3.6v11.9a3.3 3.3 0 1 1-3.3-3.3" />
          <path d="M12.6 3.6c.5 2.5 2.3 4.1 4.9 4.3" />
        </g>
        <g class="text-neutral-900 dark:text-white" stroke="currentColor">
          <path d="M12.6 3.6v11.9a3.3 3.3 0 1 1-3.3-3.3" />
          <path d="M12.6 3.6c.5 2.5 2.3 4.1 4.9 4.3" />
        </g>
      </g>
    </template>

    <!-- Mercado Livre: o LOGO DE VERDADE (07/10/2026, Eduardo mandou a imagem:
         "coloque essa logo aqui" — os desenhos próprios não pareciam o logo).
         PNG com fundo transparente em public/logos/, mesma proporção do
         original (72×51), centrado no quadrado de 24. -->
    <template v-else-if="cod === 'ml'">
      <image href="/logos/mercado-livre.png" x="0" y="3.5" width="24" height="17" preserveAspectRatio="xMidYMid meet" />
    </template>

    <!-- Amazon: "a" com o sorriso laranja -->
    <template v-else-if="cod === 'amazon'">
      <text x="12" y="13.6" text-anchor="middle" font-size="14" font-weight="700" font-family="Arial, Helvetica, sans-serif" class="fill-neutral-900 dark:fill-white">a</text>
      <path d="M4.2 16.3c4.7 3 10.6 3 14.9.2" fill="none" stroke="#FF9900" stroke-width="1.9" stroke-linecap="round" />
      <path d="M16.5 15.1l2.8.9-.6 2.7" fill="none" stroke="#FF9900" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
    </template>

    <!-- Magalu: "M" branco no quadrado azul -->
    <template v-else-if="cod === 'magalu'">
      <rect x="2" y="2" width="20" height="20" rx="5" fill="#0086FF" />
      <text x="12" y="17.2" text-anchor="middle" font-size="14" font-weight="800" font-family="Arial, Helvetica, sans-serif" fill="#fff">M</text>
    </template>

    <!-- Temu: "T" branco no quadrado laranja -->
    <template v-else-if="cod === 'temu'">
      <rect x="2" y="2" width="20" height="20" rx="5" fill="#FB7701" />
      <text x="12" y="17.2" text-anchor="middle" font-size="14" font-weight="800" font-family="Arial, Helvetica, sans-serif" fill="#fff">T</text>
    </template>

    <!-- AliExpress: "Ae" branco no quadrado vermelho -->
    <template v-else-if="cod === 'aliexpress'">
      <rect x="2" y="2" width="20" height="20" rx="5" fill="#E62E04" />
      <text x="12" y="16.3" text-anchor="middle" font-size="10.5" font-weight="700" font-family="Arial, Helvetica, sans-serif" fill="#fff">Ae</text>
    </template>

    <!-- Instagram: câmera no quadrado degradê -->
    <template v-else-if="cod === 'instagram'">
      <defs>
        <radialGradient :id="gradId" cx="28%" cy="105%" r="140%">
          <stop offset="0" stop-color="#FDF497" />
          <stop offset="0.08" stop-color="#FDF497" />
          <stop offset="0.45" stop-color="#FD5949" />
          <stop offset="0.62" stop-color="#D6249F" />
          <stop offset="0.9" stop-color="#285AEB" />
        </radialGradient>
      </defs>
      <rect x="2" y="2" width="20" height="20" rx="6" :fill="`url(#${gradId})`" />
      <rect x="6.4" y="6.4" width="11.2" height="11.2" rx="3.4" fill="none" stroke="#fff" stroke-width="1.7" />
      <circle cx="12" cy="12" r="2.7" fill="none" stroke="#fff" stroke-width="1.7" />
      <circle cx="15.5" cy="8.5" r="0.95" fill="#fff" />
    </template>

    <!-- Facebook: "f" branco no círculo azul -->
    <template v-else-if="cod === 'facebook'">
      <circle cx="12" cy="12" r="10" fill="#1877F2" />
      <path d="M13.4 21.9v-7h2.3l.4-2.8h-2.7v-1.8c0-.8.3-1.4 1.4-1.4h1.4V6.4a17 17 0 0 0-2.1-.1c-2.1 0-3.5 1.3-3.5 3.6v2.2H8.3v2.8h2.3v7" fill="#fff" />
    </template>

    <!-- Site (Charlots, Uranyx): globo branco no quadrado verde-azulado -->
    <template v-else-if="cod === 'site'">
      <rect x="2" y="2" width="20" height="20" rx="5" fill="#0D9488" />
      <g fill="none" stroke="#fff" stroke-width="1.5">
        <circle cx="12" cy="12" r="6.2" />
        <ellipse cx="12" cy="12" rx="2.6" ry="6.2" />
        <path d="M5.8 12h12.4" stroke-linecap="round" />
      </g>
    </template>

    <!-- Desconhecida: bolinha cinza com a inicial -->
    <template v-else>
      <circle cx="12" cy="12" r="10" class="fill-muted-foreground/50" />
      <text x="12" y="16" text-anchor="middle" font-size="11" font-weight="700" font-family="Arial, Helvetica, sans-serif" fill="#fff">{{ letra }}</text>
    </template>
  </svg>
</template>
