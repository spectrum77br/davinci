<script setup lang="ts">
// Manual prático da aba Margem (Eduardo, 02/09: "manual pratico de como
// funciona margem... so uma explicação para nao esquecermos"). Janela
// estática — só texto, nada de chamada à API. Mantenha em dia quando as
// regras mudarem (mínimas, robô, alertas).
import { X } from 'lucide-vue-next'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
    @click.self="emit('close')"
  >
    <div class="bg-background border rounded-lg w-full max-w-2xl p-5 space-y-4 max-h-[90vh] overflow-y-auto">
      <div class="flex items-start">
        <div>
          <h2 class="text-lg font-semibold">Manual da Margem</h2>
          <p class="text-xs text-muted-foreground">Como esta aba funciona — pra não esquecermos.</p>
        </div>
        <Button class="ml-auto" size="sm" variant="ghost" @click="emit('close')">
          <X class="size-4" />
        </Button>
      </div>

      <div class="space-y-4 text-sm leading-relaxed">
        <section class="space-y-1">
          <h3 class="font-semibold">O que a aba faz</h3>
          <p class="text-muted-foreground">
            Mostra a margem real de cada pedido dos últimos 30 dias, cruzando o Bling com o
            repasse do marketplace. A aba <span class="font-medium text-foreground">Pendentes</span> lista só o que precisa
            de um olho humano; a aba <span class="font-medium text-foreground">Buscar pedido</span> acha qualquer pedido,
            inclusive os já aprovados ou reprovados.
          </p>
        </section>

        <section class="space-y-1">
          <h3 class="font-semibold">Por que um pedido fica Pendente</h3>
          <ul class="list-disc pl-5 space-y-1 text-muted-foreground">
            <li><span class="font-medium text-foreground">Margem baixa</span> — a margem ficou abaixo da mínima do produto.</li>
            <li><span class="font-medium text-foreground">Saldo divergente</span> — o saldo do Bling e o da plataforma não batem (o Efetivo aparece R$ 0,00 até conciliar).</li>
            <li><span class="font-medium text-foreground">Aguardando saldo da plataforma</span> — ML, Shopee e TikTok só contam com o repasse REAL; enquanto ele não chega, o Efetivo fica em branco e o pedido espera.</li>
            <li><span class="font-medium text-foreground">Frete divergente</span> — o frete cobrado fugiu da regra da conta.</li>
          </ul>
        </section>

        <section class="space-y-1">
          <h3 class="font-semibold">De onde vem a margem mínima</h3>
          <ul class="list-disc pl-5 space-y-1 text-muted-foreground">
            <li>Do <span class="font-medium text-foreground">cadastro do produto</span> na Precificação (segmento).</li>
            <li>SKU começando com <span class="font-medium text-foreground">"z"</span> sem cadastro = queima de estoque → usa a mínima do segmento <span class="font-medium text-foreground">Queima de estoque</span> (hoje −15%).</li>
            <li>Sem cadastro nenhum → padrão de <span class="font-medium text-foreground">9%</span>.</li>
            <li><span class="font-medium text-foreground">Data Especial</span> ativa e margem dentro da regra especial → margem baixa não trava o pedido (aparece o selo na coluna Margem Mín.).</li>
          </ul>
        </section>

        <section class="space-y-1">
          <h3 class="font-semibold">O que o robô faz sozinho (a cada 30 min)</h3>
          <ul class="list-disc pl-5 space-y-1 text-muted-foreground">
            <li>Pedido pendente por <span class="font-medium text-foreground">margem baixa ou problema de saldo</span> → move no Bling pra <span class="font-medium text-foreground">Aguardando Cancelamento</span>, escreve o recado nas Observações da venda e manda aviso no Threema com link de aprovar pelo celular.</li>
            <li>Margem <span class="font-medium text-foreground">negativa</span> (abaixo de zero e abaixo da mínima) → já grava <span class="font-medium text-foreground">Reprovado</span> direto; o aviso do Threema diz "reprovado automaticamente" e o link desfaz na hora, se quisermos manter a venda.</li>
            <li>Margem <span class="font-medium text-foreground">acima de 60%</span> → só manda um alerta no Threema (geralmente é custo errado no cadastro do produto). Não mexe no pedido e avisa uma única vez.</li>
            <li>O robô <span class="font-medium text-foreground">não segura</span>: divergência de <span class="font-medium text-foreground">frete</span> e o "aguardando saldo" de <span class="font-medium text-foreground">ML, Shopee e TikTok</span> — esses só esperam o repasse; quando ele chega, o pedido aprova sozinho ou vira pendente de margem.</li>
          </ul>
        </section>

        <section class="space-y-1">
          <h3 class="font-semibold">Aprovar e Reprovar</h3>
          <ul class="list-disc pl-5 space-y-1 text-muted-foreground">
            <li><span class="font-medium text-foreground">Aprovar</span> um pedido segurado (ou auto-reprovado) devolve a venda ao fluxo normal no Bling — sai de Aguardando Cancelamento.</li>
            <li>O <span class="font-medium text-foreground">link do Threema</span> faz exatamente a mesma coisa que aprovar aqui na aba.</li>
            <li><span class="font-medium text-foreground">Reprovado</span> some da lista de pendentes; pra achar depois, use a aba Buscar pedido.</li>
          </ul>
        </section>

        <section class="space-y-1">
          <h3 class="font-semibold">Botões do topo</h3>
          <ul class="list-disc pl-5 space-y-1 text-muted-foreground">
            <li><span class="font-medium text-foreground">Informar</span> — escolhe quem recebe no Threema os avisos automáticos do robô (pedido segurado, reprovado automático e margem alta). O envio é sempre do robô.</li>
            <li><span class="font-medium text-foreground">planilha</span> (admins) — baixa a rentabilidade por pedido no período escolhido.</li>
            <li><span class="font-medium text-foreground">atualizar</span> — força a atualização agora. A aba já se atualiza sozinha a cada 1 minuto.</li>
          </ul>
        </section>
      </div>
    </div>
  </div>
</template>
