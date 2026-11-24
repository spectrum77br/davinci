// Cópia CONGELADA da regra do cartão vermelho como estava em
// pages/pricing/[tab].vue antes da M-22 (commit 5f58dbf4, 23/11/2026), já em JS.
// Serve só de referência para tests/pricing-cartao-compartilhado.cjs provar que
// mover a regra para lib/cartaoVermelho.ts não mudou o resultado da grade.
// NÃO EDITAR: se a regra mudar de propósito, o teste de igualdade deixa de valer
// e precisa ser revisto junto.

const MARGEM_PADRAO = 5;

const MOTIVO_SEM_INTEGRACAO = 'conta sem integração';

const MOTIVO_SEM_VINCULO = 'sem anúncio vinculado';

const PREFIXOS_SEM_INTEGRACAO = ['Conta de kit sem integração'];

const PREFIXOS_SEM_VINCULO = ['Sem anúncio de catálogo vinculado'];

const PREFIXOS_CATALOGO_NAO_LIDO = ['Marca de catálogo ainda não lida'];

const MOTIVO_ANUNCIO = {
    mesmo_anuncio: 'outra linha também manda preço',
    sincronizado: 'sincronizado',
    pausado: 'pausado',
    em_revisao: 'em revisão',
    encerrado: 'encerrado',
    so_catalogo: 'vai pela coluna Catálogo',
};

const CARTAO_SEM_INTEGRACAO = 'Conta sem integração no DaVinci';

const CARTAO_SEM_ANUNCIO = 'Sem anúncio vinculado nesta conta';

const CARTAO_ERRO_ENVIO = 'O último envio de preço deu erro';

const CARTAO_INATIVOS_NO_BALAO = 3;

const BLOQUEIOS_ANUNCIO_INATIVO = new Set(['pausado', 'em_revisao', 'encerrado']);

const BLOQUEIO_POR_STATUS = {
    paused: 'pausado',
    inactive: 'pausado',
    under_review: 'em_revisao',
    closed: 'encerrado',
};

const PADRAO_ANUNCIO_INATIVO = /pausad|em revis|encerrad|exclu[íi]d|modera/i;

function margemDaResposta(bruta, dep) {
    const produtos = {};
    for (const [id, v] of Object.entries(bruta?.produtos ?? {})) {
        const n = Number(v);
        if (v != null && Number.isFinite(n))
            produtos[id] = n;
    }
    const pct = Number(bruta?.margem_pct);
    return {
        department: bruta?.department ?? dep,
        margem_pct: bruta?.margem_pct != null && Number.isFinite(pct) ? pct : MARGEM_PADRAO,
        geral_ligada: bruta?.geral_ligada ?? true,
        por_produto_ligada: bruta?.por_produto_ligada ?? true,
        produtos,
        pode_editar: bruta?.pode_editar ?? false,
    };
}

function margemDaLinha(m, pricingProductId) {
    if (!m)
        return null;
    if (m.por_produto_ligada) {
        const propria = m.produtos?.[pricingProductId];
        if (propria != null && Number.isFinite(Number(propria)))
            return Number(propria);
    }
    if (!m.geral_ligada)
        return null;
    const geral = Number(m.margem_pct);
    return Number.isFinite(geral) ? geral : null;
}

function anunciosForaDaMargem(anuncios, tabela, margemPct) {
    if (!(tabela > 0))
        return [];
    return anuncios.filter((a) => a.preco != null && (Math.abs(a.preco - tabela) / tabela) * 100 > margemPct + 1e-9);
}

function celulaComparavel(prodId, accId) {
    const c = cellOf(prodId, accId);
    if (!c || c.price == null || c.cell_status === 'NA' || c.cell_status === 'SV' || c.source === 'disabled') {
        return null;
    }
    return c;
}

function anunciosNaoLidos(conf) {
    return conf.anuncios.filter((a) => a.preco == null);
}

function tipoSemAnuncio(motivo) {
    const m = motivo ?? '';
    if (m === MOTIVO_SEM_INTEGRACAO || PREFIXOS_SEM_INTEGRACAO.some((p) => m.startsWith(p)))
        return 'sem_integracao';
    if (!m || m === MOTIVO_SEM_VINCULO || PREFIXOS_SEM_VINCULO.some((p) => m.startsWith(p)))
        return 'sem_anuncio';
    if (PREFIXOS_CATALOGO_NAO_LIDO.some((p) => m.startsWith(p)))
        return 'catalogo_nao_lido';
    return 'fora_do_envio';
}

function motivosDoCartao(e) {
    const motivos = [];
    const somar = (m) => {
        if (!motivos.includes(m))
            motivos.push(m);
    };
    const cat = e.celula?.catalogo ?? null;
    const conf = e.conferida ?? null;
    // a) Conta sem integração (inclusive a coluna de catálogo de uma conta de
    // kit sem integração).
    const semIntegracao = (!!e.conta && e.conta.integration_id == null)
        || (cat?.bloqueio === 'sem_anuncio' && tipoSemAnuncio(cat.texto) === 'sem_integracao');
    if (semIntegracao)
        somar(CARTAO_SEM_INTEGRACAO);
    // b) Sem anúncio vinculado. Célula com `catalogo` (coluna de catálogo, ou
    // de kit que só tem anúncio de catálogo): quem diz é o /grid. Coluna de kit:
    // a conferência, ou o envio que gravou no_link.
    if (!semIntegracao) {
        const semVinculo = e.celula?.cell_status === 'no_link' || (cat
            ? cat.bloqueio === 'sem_anuncio' && tipoSemAnuncio(cat.texto) === 'sem_anuncio'
            : conf?.situacao === 'sem_anuncio' && tipoSemAnuncio(conf.motivo) === 'sem_anuncio');
        if (semVinculo)
            somar(CARTAO_SEM_ANUNCIO);
    }
    // c) Anúncio vinculado inativo.
    const inativos = [];
    for (const an of cat?.anuncios ?? []) {
        // 'so_catalogo': o anúncio é da coluna Catálogo, não desta.
        if (an.bloqueio === 'so_catalogo')
            continue;
        const bloqueio = an.bloqueio && BLOQUEIOS_ANUNCIO_INATIVO.has(an.bloqueio)
            ? an.bloqueio
            : BLOQUEIO_POR_STATUS[(an.status ?? '').toLowerCase()];
        if (bloqueio)
            inativos.push(`Anúncio de catálogo ${an.external_id} ${MOTIVO_ANUNCIO[bloqueio] ?? bloqueio}`);
    }
    if (!inativos.length && cat?.bloqueio && BLOQUEIOS_ANUNCIO_INATIVO.has(cat.bloqueio)) {
        inativos.push(`Anúncio de catálogo ${MOTIVO_ANUNCIO[cat.bloqueio] ?? cat.bloqueio}`);
    }
    // O que a conferência leu ao vivo: inativo, ou sem preço porque foi
    // encerrado/excluído/moderado.
    for (const a of conf?.anuncios ?? []) {
        const motivoInativo = a.motivo && PADRAO_ANUNCIO_INATIVO.test(a.motivo) ? a.motivo : null;
        if (a.ativo !== false && !(a.preco == null && motivoInativo))
            continue;
        const variacao = a.variacao ? ` (variação ${a.variacao})` : '';
        const detalhe = motivoInativo
            ? motivoInativo.replace(/\s*—\s*o envio pula$/, '')
            : 'inativo no marketplace (pausado, em revisão ou fora do ar)';
        inativos.push(`Anúncio ${a.anuncio}${variacao}: ${detalhe}`);
    }
    // "Fora do envio" da conferência: só pausado/em revisão/encerrado é motivo
    // (sincronizado, mesmo anúncio, só catálogo... são regra do envio).
    if (!cat && conf?.situacao === 'sem_anuncio' && tipoSemAnuncio(conf.motivo) === 'fora_do_envio'
        && PADRAO_ANUNCIO_INATIVO.test(conf.motivo ?? '')) {
        inativos.push(conf.motivo);
    }
    for (const m of inativos.slice(0, CARTAO_INATIVOS_NO_BALAO))
        somar(m);
    if (inativos.length > CARTAO_INATIVOS_NO_BALAO) {
        somar(`… e mais ${inativos.length - CARTAO_INATIVOS_NO_BALAO} anúncio(s) inativo(s)`);
    }
    // d) O último envio deu erro (o texto do erro não fica guardado).
    if (e.celula?.cell_status === 'error')
        somar(CARTAO_ERRO_ENVIO);
    // e) Fora da margem da linha.
    const d = e.divergencia;
    if (d && d.fora.length) {
        const a = d.fora[0];
        const dif = ((a.preco - d.tabela) / d.tabela) * 100;
        const mais = d.fora.length > 1 ? ` (e mais ${d.fora.length - 1})` : '';
        somar(`Preço do anúncio fora da margem de ${fmtPct(d.margem)}%: anúncio ${Math.round(a.preco)} × Tabela ${Math.round(d.tabela)}`
            + ` (${dif > 0 ? '+' : ''}${dif.toFixed(1).replace('.', ',')}%)${mais}`);
    }
    return motivos;
}

function contaBase(acc) {
    if (!ehCatalogo(acc) || !acc.conta_base_id)
        return null;
    return contasPorId.value.get(acc.conta_base_id) ?? null;
}

function contaParametros(acc) {
    return contaBase(acc) ?? acc;
}

function ehCatalogo(a) {
    return a.canal === 'catalogo';
}

function aplicarSemVinculo(lista, dep) {
    if (!lista || !grid.value || gridDepartamento.value !== dep)
        return;
    const ainda = new Set(lista.map((c) => `${c.pricing_product_id}::${c.pricing_account_id}`));
    for (const c of grid.value.cells) {
        if (c.cell_status === 'no_link' && !ainda.has(`${c.pricing_product_id}::${c.pricing_account_id}`)) {
            tirarSemVinculoDaGrade(c.pricing_product_id, c.pricing_account_id);
        }
    }
}

function tirarSemVinculoDaGrade(prodId, accId) {
    const c = cellOf(prodId, accId);
    if (c?.cell_status === 'no_link')
        c.cell_status = 'auto';
    const ck = pushCellKey(prodId, accId);
    if (pushStates.value.get(ck) === 'no_link') {
        const m = new Map(pushStates.value);
        m.delete(ck);
        pushStates.value = m;
    }
}

const divergencias__corpo = () => {
    const m = new Map();
    const mg = margem.value;
    if (!mg || mg.department !== department.value || !gradePronta.value)
        return m;
    for (const conf of conferencia.value?.celulas ?? []) {
        if (conf.situacao !== 'conferida')
            continue;
        const margemLinha = margemDaLinha(mg, conf.pricing_product_id);
        if (margemLinha == null)
            continue;
        const c = celulaComparavel(conf.pricing_product_id, conf.pricing_account_id);
        if (!c)
            continue;
        const tabela = Number(c.price);
        const fora = anunciosForaDaMargem(conf.anuncios, tabela, margemLinha);
        if (fora.length) {
            m.set(`${conf.pricing_product_id}::${conf.pricing_account_id}`, {
                tabela,
                fora,
                conferidoEm: conf.conferido_em,
                naoLidos: anunciosNaoLidos(conf).length,
                anuncios: conf.anuncios.length,
                margem: margemLinha,
            });
        }
    }
    return m;
};

const cartoes__corpo = () => {
    const m = new Map();
    if (!gradePronta.value || !grid.value)
        return m;
    for (const acc of grid.value.accounts) {
        const conta = contaParametros(acc);
        for (const prod of grid.value.products) {
            const chave = `${prod.id}::${acc.id}`;
            const motivos = motivosDoCartao({
                conta,
                celula: cellMap.value.get(chave),
                conferida: conferenciaPorCelula.value.get(chave),
                divergencia: divergencias.value.get(chave),
            });
            if (motivos.length)
                m.set(chave, motivos.join('\n'));
        }
    }
    return m;
};

const cellMap__corpo = () => {
    const m = new Map();
    for (const c of grid.value?.cells ?? []) {
        m.set(`${c.pricing_product_id}::${c.pricing_account_id}`, c);
    }
    return m;
};

const conferenciaPorCelula__corpo = () => {
    const m = new Map();
    for (const c of conferencia.value?.celulas ?? []) {
        m.set(`${c.pricing_product_id}::${c.pricing_account_id}`, c);
    }
    return m;
};

const contasPorId__corpo = () => {
    const m = new Map();
    for (const a of accounts.value)
        m.set(a.id, a);
    for (const a of grid.value?.accounts ?? [])
        m.set(a.id, a);
    return m;
};