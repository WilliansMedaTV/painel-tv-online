"""
SYNC DIARIO - Painel de Imoveis (Robust CRM -> imoveis.json)
==============================================================
O que este script faz:
- Uma vez por dia, as 13h, busca no CRM Robust os imoveis ATIVOS que foram
  lancados ou reativados nos ultimos 7 dias.
- Para cada imovel, busca a primeira foto. Se o imovel nao tiver foto,
  ele NAO entra no painel (conforme definido).
- Gera o arquivo imoveis.json na mesma pasta, que e lido pelo painel.html.
- Fica rodando continuamente em segundo plano, "dormindo" entre uma
  execucao e outra. Nao precisa ficar reiniciando manualmente.

Como rodar: de preferencia, use o arquivo "iniciar_sync.bat" (basta dar
duplo clique). Deixe a janela preta aberta e minimizada - e ela quem
mantem o script vivo. Se fechar a janela, o script para.

Tudo que acontece fica registrado no arquivo sync_log.txt, na mesma
pasta, para conferencia caso algo de errado.
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timedelta

try:
    import requests
    from dotenv import load_dotenv
except ImportError:
    print("Faltam bibliotecas. Rode: pip install -r requirements.txt")
    sys.exit(1)

# ----------------------------------------------------------------------
# CONFIGURACAO
# ----------------------------------------------------------------------

PASTA_SCRIPT = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_ENV = os.path.join(PASTA_SCRIPT, ".env")
ARQUIVO_SAIDA = os.path.join(PASTA_SCRIPT, "imoveis.json")
ARQUIVO_LOG = os.path.join(PASTA_SCRIPT, "sync_log.txt")

HORA_EXECUCAO = 13   # 13h - horario configurado
MINUTO_EXECUCAO = 0

DIAS_JANELA = 7       # janela movel de 7 dias (lancamentos)
BASE_URL = "https://api.robustcrm.io/v1"
PER_PAGE = 500         # maximo permitido pela API para listagens

# ----------------------------------------------------------------------
# LOG (grava tanto na tela quanto no arquivo sync_log.txt)
# ----------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(ARQUIVO_LOG, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("sync")

# ----------------------------------------------------------------------
# CREDENCIAIS
# ----------------------------------------------------------------------

load_dotenv(ARQUIVO_ENV)
NICKNAME = os.getenv("ROBUST_NICKNAME")
API_KEY = os.getenv("ROBUST_API_KEY")

if not NICKNAME or not API_KEY:
    log.error("ROBUST_NICKNAME ou ROBUST_API_KEY nao encontrados no .env. Encerrando.")
    sys.exit(1)

HEADERS = {
    "X-Nickname": NICKNAME,
    "X-API-Key": API_KEY,
    "Content-Type": "application/json",
}


# ----------------------------------------------------------------------
# FUNCOES
# ----------------------------------------------------------------------

def buscar_todos_imoveis_recentes():
    """Busca, com paginacao, todos os imoveis ativos lancados/reativados
    nos ultimos DIAS_JANELA dias. Usa o filtro de fallback da API que
    considera reativacao -> ativacao -> criacao."""

    hoje = datetime.now().date()
    data_inicio = hoje - timedelta(days=DIAS_JANELA)

    params_base = {
        "status": 1,  # somente ativos
        "reativados_ativados_criados_de": data_inicio.isoformat(),
        "reativados_ativados_criados_ate": hoje.isoformat(),
        "per_page": PER_PAGE,
    }

    todos = []
    pagina = 1
    while True:
        params = dict(params_base, page=pagina)
        resp = requests.get(f"{BASE_URL}/imoveis", headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        corpo = resp.json()
        lote = corpo.get("data", [])
        todos.extend(lote)

        meta = corpo.get("meta", {})
        total_paginas = meta.get("pages", 1)
        log.info(f"  Pagina {pagina}/{total_paginas} - {len(lote)} imoveis nesta pagina.")

        if pagina >= total_paginas:
            break
        pagina += 1

    return todos


def buscar_primeira_foto(imovel_id):
    """Retorna a URL (variante 'small') da primeira foto do imovel,
    ou None se nao houver nenhuma foto visivel."""

    params = {
        "filetype": "media",
        "category": "Fotos",
        "per_page": 20,
        "page": 1,
    }
    resp = requests.get(
        f"{BASE_URL}/imoveis/{imovel_id}/files",
        headers=HEADERS,
        params=params,
        timeout=20,
    )
    if resp.status_code != 200:
        log.warning(f"  Imovel {imovel_id}: erro {resp.status_code} ao buscar fotos.")
        return None

    fotos = resp.json().get("data", [])
    fotos_visiveis = [f for f in fotos if f.get("visible", True)]
    if not fotos_visiveis:
        return None

    # Ordena pelo campo "order" e pega a primeira
    fotos_visiveis.sort(key=lambda f: f.get("order", 9999))
    primeira = fotos_visiveis[0]
    return primeira.get("urls", {}).get("small") or primeira.get("urls", {}).get("full")


def buscar_leads_periodo(data_inicio, data_fim):
    """Busca, com paginacao, todos os leads criados no periodo informado."""

    params_base = {
        "criado_de": data_inicio.isoformat(),
        "criado_ate": data_fim.isoformat(),
        "per_page": PER_PAGE,
    }

    todos = []
    pagina = 1
    while True:
        params = dict(params_base, page=pagina)
        resp = requests.get(f"{BASE_URL}/leads", headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        corpo = resp.json()
        lote = corpo.get("data", [])
        todos.extend(lote)

        meta = corpo.get("meta", {})
        total_paginas = meta.get("pages", 1)
        log.info(f"  [leads] Pagina {pagina}/{total_paginas} - {len(lote)} registros.")

        if pagina >= total_paginas:
            break
        pagina += 1

    return todos


def buscar_atendimentos_periodo(data_inicio, data_fim):
    """Busca, com paginacao, todos os atendimentos da jornada de negociacao
    (jornada=1, ou seja compra/aluguel - nao inclui captacao) criados no
    periodo informado."""

    params_base = {
        "criado_de": data_inicio.isoformat(),
        "criado_ate": data_fim.isoformat(),
        "jornada": 1,
        "per_page": PER_PAGE,
    }

    todos = []
    pagina = 1
    while True:
        params = dict(params_base, page=pagina)
        resp = requests.get(f"{BASE_URL}/atendimentos", headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        corpo = resp.json()
        lote = corpo.get("data", [])
        todos.extend(lote)

        meta = corpo.get("meta", {})
        total_paginas = meta.get("pages", 1)
        log.info(f"  [atendimentos] Pagina {pagina}/{total_paginas} - {len(lote)} registros.")

        if pagina >= total_paginas:
            break
        pagina += 1

    return todos


def contar_ocorrencias_por_imovel(lista_registros):
    """Recebe uma lista de leads ou atendimentos e conta quantas vezes
    cada imovel_id aparece (registros sem imovel vinculado sao ignorados)."""

    contagem = {}
    for registro in lista_registros:
        imovel_id = registro.get("imovel")
        if imovel_id:
            contagem[imovel_id] = contagem.get(imovel_id, 0) + 1
    return contagem


def buscar_registros_por_ids(endpoint, lista_ids):
    """Resolve uma lista de IDs em lote (usando ?ids=1,2,3) para qualquer
    endpoint de listagem (imoveis, atendimentos, usuarios...), em vez de
    uma chamada por registro. Retorna um dicionario {id: registro}."""

    resultado = {}
    lista_ids = list(lista_ids)
    tamanho_lote = 100

    for i in range(0, len(lista_ids), tamanho_lote):
        lote = lista_ids[i:i + tamanho_lote]
        if not lote:
            continue
        ids_str = ",".join(str(x) for x in lote)
        params = {"ids": ids_str, "per_page": tamanho_lote}
        resp = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        corpo = resp.json()
        for registro in corpo.get("data", []):
            resultado[registro.get("id")] = registro

    return resultado


def buscar_imoveis_por_ids(lista_ids):
    return buscar_registros_por_ids("imoveis", lista_ids)


def normalizar_negociacao_imovel(imovel):
    """Classifica o imovel em 'venda' ou 'locacao' com base no campo
    tipo_negociacao. Imoveis de Temporada (ou outro tipo) retornam None,
    pois esses indicadores so cobrem Venda e Locacao."""

    tipo = (imovel.get("tipo_negociacao") or "").strip().lower()
    if tipo == "venda":
        return "venda"
    if tipo in ("locação", "locacao"):
        return "locacao"
    return None


def calcular_campeao_e_tipologia(candidatos, pontuacao, rotulo):
    """Recebe um dicionario {id: imovel} ja filtrado (por exemplo, so os
    imoveis de Venda) e a pontuacao de buscas de cada um, e retorna
    (campeao, tipologia_mais_buscada) para esse grupo. 'rotulo' e so
    usado nas mensagens de log (ex: 'Venda', 'Locação')."""

    if not candidatos:
        log.info(f"[{rotulo}] Nenhum imovel ativo buscado neste periodo.")
        return None, None

    # ---------- Campeao ----------
    campeao_id = max(candidatos, key=lambda id_: pontuacao[id_])
    imovel_campeao_bruto = candidatos[campeao_id]

    try:
        foto_campeao = buscar_primeira_foto(campeao_id)
    except requests.exceptions.RequestException:
        foto_campeao = None

    campeao = montar_registro_painel(imovel_campeao_bruto, foto_campeao)
    campeao["buscas"] = pontuacao[campeao_id]
    log.info(f"[{rotulo}] Campeao: imovel {campeao_id} ({pontuacao[campeao_id]} buscas).")

    # ---------- Tipologia mais buscada (agrupada por tipo + bairro) ----------
    grupos = {}
    for id_, imovel in candidatos.items():
        chave = (imovel.get("tipo") or "Não informado", imovel.get("endereco_bairro") or "Não informado")
        grupo = grupos.setdefault(chave, {"pontuacao": 0, "imoveis": []})
        grupo["pontuacao"] += pontuacao[id_]
        grupo["imoveis"].append(imovel)

    melhor_chave = max(grupos, key=lambda k: grupos[k]["pontuacao"])
    tipo, bairro = melhor_chave
    grupo_vencedor = grupos[melhor_chave]

    valores = []
    for imovel in grupo_vencedor["imoveis"]:
        _, valor = obter_valor_negocio(imovel)
        if valor:
            valores.append(valor)
    valor_medio = round(sum(valores) / len(valores)) if valores else None

    tipologia_mais_buscada = {
        "tipo": tipo,
        "bairro": bairro,
        "valor_medio": valor_medio,
        "quantidade_imoveis": len(grupo_vencedor["imoveis"]),
        "buscas": grupo_vencedor["pontuacao"],
    }
    log.info(
        f"[{rotulo}] Tipologia mais buscada: {tipo} em {bairro} "
        f"({grupo_vencedor['pontuacao']} buscas, {len(grupo_vencedor['imoveis'])} imoveis)."
    )

    return campeao, tipologia_mais_buscada


def calcular_insights_mensais(data_inicio, data_fim):
    """Calcula os indicadores da janela mensal: tipologia mais buscada
    e imovel campeao, separados por Venda e Locacao. Retorna um
    dicionario pronto para entrar no imoveis.json."""

    try:
        leads = buscar_leads_periodo(data_inicio, data_fim)
        atendimentos = buscar_atendimentos_periodo(data_inicio, data_fim)
    except requests.exceptions.RequestException as e:
        log.error(f"Falha ao buscar leads/atendimentos para os indicadores mensais: {e}")
        leads, atendimentos = [], []

    contagem_leads = contar_ocorrencias_por_imovel(leads)
    contagem_atendimentos = contar_ocorrencias_por_imovel(atendimentos)
    todos_ids = set(contagem_leads) | set(contagem_atendimentos)

    pontuacao = {
        id_: contagem_leads.get(id_, 0) + contagem_atendimentos.get(id_, 0)
        for id_ in todos_ids
    }

    candidatos_ativos_venda = {}
    candidatos_ativos_locacao = {}

    if todos_ids:
        try:
            imoveis_resolvidos = buscar_imoveis_por_ids(todos_ids)
        except requests.exceptions.RequestException as e:
            log.error(f"Falha ao resolver imoveis buscados no mes: {e}")
            imoveis_resolvidos = {}

        for id_ in pontuacao:
            imovel = imoveis_resolvidos.get(id_)
            if not imovel or imovel.get("status") != 1:
                continue
            negociacao = normalizar_negociacao_imovel(imovel)
            if negociacao == "venda":
                candidatos_ativos_venda[id_] = imovel
            elif negociacao == "locacao":
                candidatos_ativos_locacao[id_] = imovel

    campeao_venda, tipologia_venda = calcular_campeao_e_tipologia(candidatos_ativos_venda, pontuacao, "Venda")
    campeao_locacao, tipologia_locacao = calcular_campeao_e_tipologia(candidatos_ativos_locacao, pontuacao, "Locação")

    return {
        "campeao_venda": campeao_venda,
        "campeao_locacao": campeao_locacao,
        "tipologia_mais_buscada_venda": tipologia_venda,
        "tipologia_mais_buscada_locacao": tipologia_locacao,
    }



def montar_endereco(imovel):
    partes_linha1 = []
    if imovel.get("endereco_logradouro"):
        partes_linha1.append(imovel["endereco_logradouro"])
    if imovel.get("endereco_numero"):
        partes_linha1.append(f", {imovel['endereco_numero']}")
    if imovel.get("endereco_complemento"):
        partes_linha1.append(f" - {imovel['endereco_complemento']}")

    linha1 = "".join(partes_linha1).strip(", ")

    partes_linha2 = []
    if imovel.get("endereco_bairro"):
        partes_linha2.append(imovel["endereco_bairro"])
    cidade_uf = ""
    if imovel.get("endereco_cidade"):
        cidade_uf = imovel["endereco_cidade"]
        if imovel.get("endereco_estado"):
            cidade_uf += f" - {imovel['endereco_estado']}"
    if cidade_uf:
        partes_linha2.append(cidade_uf)

    linha2 = ", ".join(partes_linha2)

    if linha1 and linha2:
        return f"{linha1}, {linha2}"
    return linha1 or linha2 or "Endereco nao informado"


def montar_titulo_tipo(imovel):
    tipo = imovel.get("tipo") or "Imovel"
    quartos = imovel.get("quartos")
    if quartos:
        sufixo = "quarto" if quartos == 1 else "quartos"
        return f"{tipo} {quartos} {sufixo}"
    return tipo


def obter_valor_negocio(imovel):
    """Retorna (tipo_negociacao, valor) de acordo com o tipo de negociacao
    do imovel, cobrindo Venda, Locacao e Temporada."""

    tipo_negociacao = imovel.get("tipo_negociacao") or ""
    mapa_campos = {
        "Venda": "valor_venda",
        "Locação": "valor_locacao",
        "Locacao": "valor_locacao",
        "Temporada": "valor_temporada",
    }
    campo = mapa_campos.get(tipo_negociacao)
    valor = imovel.get(campo) if campo else None
    return tipo_negociacao, valor


def obter_nome_captador(imovel):
    detalhes = imovel.get("captadores_detalhes") or []
    if detalhes:
        return detalhes[0].get("nome", "Nao informado")
    return "Nao informado"


def montar_registro_painel(imovel, foto_url):
    tipo_negociacao, valor = obter_valor_negocio(imovel)
    return {
        "id": imovel.get("id"),
        "codigo": str(imovel.get("id")),
        "titulo_tipo": montar_titulo_tipo(imovel),
        "endereco": montar_endereco(imovel),
        "captador": obter_nome_captador(imovel),
        "tipo_negociacao": tipo_negociacao,
        "valor": valor,
        "quartos": imovel.get("quartos"),
        "foto_url": foto_url,
    }


def executar_sincronizacao():
    log.info("=" * 60)
    log.info("Iniciando sincronizacao...")

    hoje = datetime.now().date()
    data_inicio_semana = hoje - timedelta(days=DIAS_JANELA)
    data_inicio_mes = hoje.replace(day=1)  # primeiro dia do mes calendario atual

    try:
        imoveis_brutos = buscar_todos_imoveis_recentes()
    except requests.exceptions.RequestException as e:
        log.error(f"Falha ao buscar imoveis: {e}")
        return False

    log.info(f"Total de imoveis candidatos (ultimos {DIAS_JANELA} dias): {len(imoveis_brutos)}")

    imoveis_finais = []
    for imovel in imoveis_brutos:
        imovel_id = imovel.get("id")
        try:
            foto_url = buscar_primeira_foto(imovel_id)
        except requests.exceptions.RequestException as e:
            log.warning(f"  Imovel {imovel_id}: erro ao buscar foto ({e}). Pulando.")
            continue

        if not foto_url:
            log.info(f"  Imovel {imovel_id}: sem foto - NAO sera publicado.")
            continue

        imoveis_finais.append(montar_registro_painel(imovel, foto_url))
        log.info(f"  Imovel {imovel_id}: OK, incluido no painel.")

    # ---------- Indicadores mensais: campeoes, tipologias, top corretores ----------
    log.info(f"Calculando indicadores do mes calendario atual ({data_inicio_mes} a {hoje}, Venda e Locação)...")
    insights = calcular_insights_mensais(data_inicio_mes, hoje)

    # Se algum campeao tambem for um lancamento da semana, remove da lista
    # normal para nao aparecer duplicado (ele ja ganha um slide proprio).
    ids_campeoes = {
        insights["campeao_venda"]["id"] if insights["campeao_venda"] else None,
        insights["campeao_locacao"]["id"] if insights["campeao_locacao"] else None,
    }
    imoveis_finais = [im for im in imoveis_finais if im["id"] not in ids_campeoes]

    saida = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "total": len(imoveis_finais),
        "imoveis": imoveis_finais,
        "campeao_venda": insights["campeao_venda"],
        "campeao_locacao": insights["campeao_locacao"],
        "tipologia_mais_buscada_venda": insights["tipologia_mais_buscada_venda"],
        "tipologia_mais_buscada_locacao": insights["tipologia_mais_buscada_locacao"],
    }

    # Escrita "atomica": grava num arquivo temporario e so depois renomeia,
    # para o painel nunca ler um arquivo pela metade.
    tmp_path = ARQUIVO_SAIDA + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, ARQUIVO_SAIDA)

    log.info(f"Concluido. {len(imoveis_finais)} imoveis publicados em imoveis.json.")
    log.info("=" * 60)
    return True


def segundos_ate_proxima_execucao():
    agora = datetime.now()
    proxima = agora.replace(hour=HORA_EXECUCAO, minute=MINUTO_EXECUCAO, second=0, microsecond=0)
    if proxima <= agora:
        proxima += timedelta(days=1)
    return (proxima - agora).total_seconds(), proxima


def loop_principal():
    log.info("Script de sincronizacao iniciado.")
    log.info(f"Configurado para rodar todos os dias as {HORA_EXECUCAO:02d}:{MINUTO_EXECUCAO:02d}.")

    # Roda uma vez imediatamente ao iniciar, para nao esperar ate 13h
    # na primeira vez que o script for ligado.
    log.info("Rodando uma sincronizacao inicial agora, para nao esperar o proximo horario...")
    executar_sincronizacao()

    while True:
        segundos, proxima = segundos_ate_proxima_execucao()
        log.info(f"Proxima sincronizacao agendada para: {proxima.strftime('%d/%m/%Y %H:%M')}")
        time.sleep(segundos)
        executar_sincronizacao()


if __name__ == "__main__":
    if "--uma-vez" in sys.argv:
        # Modo nuvem (GitHub Actions): roda uma sincronizacao e encerra.
        # Sai com erro (codigo 1) se falhar, para o GitHub avisar por e-mail
        # e NAO publicar dados vazios por cima dos ultimos dados bons.
        sys.exit(0 if executar_sincronizacao() else 1)

    try:
        loop_principal()
    except KeyboardInterrupt:
        log.info("Script encerrado manualmente.")
