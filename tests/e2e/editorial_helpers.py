"""Shared helpers for multi-mode seasonal MOFU editorial E2E tests."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
import time
import urllib.request
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
import uvicorn
from mcp import ClientSession
from mcp.types import TextContent
from psycopg import sql

from engine.mcp.server import create_mcp_server
from engine.migrations import run_startup_migrations
from scripts.post_deploy_smoke import (
    _extract_mcp_tool_payload,
    _parse_mcp_rpc_body,
    _post_mcp_json,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_LOCAL_TOKEN = "e2e-all-modes-mcp-bearer-token-32c"
SEASONAL_PRIMARY_KEYWORD = "Black Friday e fim de ano"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _swap_db_name(db_url: str, db_name: str) -> str:
    parsed = urlsplit(db_url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{db_name}", parsed.query, parsed.fragment))


@contextlib.contextmanager
def create_isolated_modes_db() -> Generator[str, None, None]:
    """Create a blank migrated temporary database for multi-mode E2E tests."""
    base_owner_url = os.getenv(
        "OWNER_DATABASE_URL",
        "postgres://owner:secret@localhost:5432/macy?sslmode=disable",
    )
    tmp_db_name = f"macy_modes_{uuid.uuid4().hex[:8]}"
    tmp_url = _swap_db_name(base_owner_url, tmp_db_name)

    with psycopg.connect(base_owner_url, autocommit=True) as admin_conn:
        admin_conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(tmp_db_name)))

    try:
        run_startup_migrations(env={"AUTO_MIGRATE": "true", "MIGRATION_DATABASE_URL": tmp_url})
        yield tmp_url
    finally:
        with psycopg.connect(base_owner_url, autocommit=True) as admin_conn:
            admin_conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(tmp_db_name)
                )
            )


def _wait_for_http(url: str, *, timeout_sec: float = 40.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:  # noqa: S310
                if resp.status < 500:
                    return
        except Exception:
            time.sleep(0.3)
    msg = f"Server did not become ready at {url} within {timeout_sec}s"
    raise TimeoutError(msg)


def _post_mcp_json_resilient(
    mcp_url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, str], str]:
    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            return _post_mcp_json(
                mcp_url,
                payload=payload,
                insecure=False,
                headers=headers,
                timeout=timeout,
            )
        except Exception as exc:
            last_exc = exc
            if attempt < 3:
                time.sleep(0.25)
    assert last_exc is not None
    raise last_exc


def _mcp_http_tool(
    mcp_url: str,
    session_headers: dict[str, str],
    *,
    req_id: int,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    status, _hdrs, body = _post_mcp_json_resilient(
        mcp_url,
        payload={
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=session_headers,
        timeout=30.0,
    )
    assert status == 200, f"{name} returned HTTP {status}: {body}"
    return _extract_mcp_tool_payload(_parse_mcp_rpc_body(body))


@contextlib.contextmanager
def _ephemeral_http_mcp_server(
    *,
    db_url: str,
    enc_key: str,
    token: str,
    actor_email: str,
) -> Generator[str, None, None]:
    env_cfg = {
        "APP_ENV": "test",
        "DATABASE_URL": db_url,
        "APP_ENCRYPTION_KEY": enc_key,
        "MCP_AUTH_TOKEN": token,
        "MCP_ACTOR_EMAIL": actor_email,
    }
    bundle = create_mcp_server(mock=False, transport="streamable-http", env=env_cfg)
    app = bundle.create_http_app(host="127.0.0.1")
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_http(f"{base_url}/healthz", timeout_sec=15.0)
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _build_seasonal_mofu_markdown() -> str:
    """Build a >850-word pt-BR seasonal MOFU post passing all deterministic checks."""
    return (
        "---\n"
        'title: "Black Friday e fim de ano no varejo: checklist de estoque e metas"\n'
        'meta_description: "'
        "Prepare estoque, atendimento e prazos na Black Friday e fim de ano para crescer vendas "
        'no varejo sem rupturas nem atrasos."\n'
        "slug: black-friday-e-fim-de-ano-no-varejo-checklist\n"
        "---\n\n"
        "# Black Friday e fim de ano no varejo: checklist de estoque e metas\n\n"
        "Na reta final de novembro, a Black Friday e fim de ano elevam muito o fluxo de clientes "
        "nas lojas. Equipes de atendimento enfrentam pedidos simultâneos e prazos curtos de "
        "entrega. Sem planejamento claro, o pico comercial vira atraso no balcão. "
        "Por isso, revisar estoque, catálogo e metas diárias evita rupturas logo na abertura.\n\n"
        "## Por que a Black Friday e fim de ano exigem controle rigoroso de estoque\n\n"
        "No último bimestre, o comportamento de compra muda rápido nas operações de varejo. "
        "Consumidores pesquisam ofertas antes da data oficial e comparam prazos de entrega no "
        "mesmo dia. Quando o saldo de itens campeões fica desatualizado em planilhas soltas, "
        "a loja promete um produto indisponível. Esse erro gera estorno, frustração e perda de "
        "margem.\n\n"
        "Separar o mix por curva de giro durante a Black Friday e fim de ano protege o capital. "
        "Produtos de saída rápida precisam de estoque de segurança e leitura ágil por código de "
        "barras. Já linhas sazonais anteriores podem formar kits promocionais bem sinalizados "
        "para liberar espaço físico antes de dezembro.\n\n"
        "## Como alinhar estoque, catálogo e prazos de entrega\n\n"
        "Cada pedido fechado aciona etapas de separação, conferência e entrega ao comprador. "
        "Se o catálogo online e o sistema interno mostram números diferentes, o time perde tempo "
        "conferindo prateleiras. Com inventário rotativo semanal, a equipe acompanha faltas com "
        "antecedência e aciona fornecedores antes da semana de pico.\n\n"
        "Durante a Black Friday e fim de ano, uma falha simples atrasa dezenas de encomendas. "
        "Prazos prometidos sem margem técnica sobrecarregam o time operacional e geram filas no "
        "pós-venda. Definir janelas reais de expedição mantém a confiança do cliente mesmo nos "
        "dias de maior movimento.\n\n"
        "## Metas diárias por turno e saúde da margem\n\n"
        "Campanhas promocionais pedem clareza sobre custo médio de reposição e teto de desconto. "
        "Antes de divulgar tabelas sazonais, o gestor precisa calcular o ponto de equilíbrio por "
        "categoria. Descontos sem critério trazem volume, mas reduzem o lucro líquido da "
        "operação.\n\n"
        "Definir metas por turno ajuda os vendedores a acompanhar ticket médio e peças por "
        "atendimento em tempo real. Quando um item gira acima do esperado já na sexta-feira, a "
        "equipe consegue remanejar lotes entre unidades no mesmo dia. Essa agilidade preserva a "
        "experiência no salão.\n\n"
        "## Padronização do atendimento e fechamento de caixa\n\n"
        "Filas longas reduzem a conversão em horários de pico. Quando os vendedores consultam "
        "preços e disponibilidade direto pelo dispositivo móvel, o cliente segue ao pagamento "
        "com o pedido pronto. Reservas e pedidos sob encomenda também exigem prazos claros de "
        "retirada para não travar o balcão.\n\n"
        "Na conferência financeira, conciliar PIX, cartões, transferências e notas fiscais "
        "emitidas evita horas extras após o expediente. Gestores que observam indicadores "
        "diários corrigem gargalos ainda durante a campanha, garantindo previsibilidade até o "
        "Natal.\n\n"
        "## Checklist prático antes do pico de vendas\n\n"
        "Antes de ativar as ações de Black Friday e fim de ano, revise cinco passos objetivos "
        "com gerentes, compradores e equipe de frente de caixa.\n\n"
        "1. Realize contagem rotativa dos vinte itens de maior giro na loja.\n"
        "2. Confira notas fiscais de entrada para atualizar custos e saldos.\n"
        "3. Alinhe prazos realistas de separação e entrega no balcão.\n"
        "4. Padronize avisos de pedido pronto para retirada sem filas.\n"
        "5. Feche o caixa por meio de pagamento ao fim de cada turno.\n\n"
        "Seguir essa rotina simples diminui cancelamentos e dá segurança ao time de vendas nos "
        "dias mais intensos do calendário comercial.\n\n"
        "## Pós-venda e retenção para o primeiro trimestre\n\n"
        "O relacionamento comercial continua depois que o cliente retira sua compra em loja. "
        "Registrar corretamente o histórico de preferências e prazos de garantia prepara ações "
        "direcionadas para o início do ano seguinte. Lembretes úteis de manutenção ou reposição "
        "trazem clientes de volta sem depender só de novos anúncios.\n\n"
        "Empresas que unem controle de estoque, metas claras e pós-venda organizado atravessam a "
        "Black Friday e fim de ano com operação estável. Em vez de corrigir falhas em janeiro, "
        "a gestão começa o novo ciclo com caixa equilibrado e clientes satisfeitos.\n\n"
        "## Acompanhamento de indicadores durante a operação sazonal\n\n"
        "Acompanhar indicadores em tempo real evita desvios que só aparecem no fechamento do mês. "
        "Observe diariamente a taxa de conversão por turno, o índice de ruptura na vitrine e o "
        "tempo médio de separação de pedidos em espera. Quando a equipe identifica gargalos no "
        "mesmo dia, consegue redistribuir tarefas e ajustar escalas antes da próxima onda de "
        "clientes. Essa gestão ativa transforma a Black Friday e fim de ano em receita previsível "
        "com margem preservada.\n\n"
        "## Perguntas frequentes sobre campanhas sazonais de fim de ano\n\n"
        "- **Como evitar vender produtos sem saldo em promoção?** Faça contagens rotativas antes "
        "da campanha e integre o saldo do estoque ao ponto de venda em tempo real.\n"
        "- **Qual o maior erro ao definir prazos de entrega sazonais?** Prometer prazos padrão "
        "sem considerar o volume extra de pedidos no fornecedor e na expedição.\n"
        "- **Como proteger a margem durante a Black Friday?** Defina descontos por faixa de "
        "giro e monitore o lucro bruto diário da campanha.\n\n"
        "Quer estruturar suas rotinas antes do próximo pico comercial? Baixe o checklist "
        "prático de auditoria de estoque e organize a operação da sua equipe hoje mesmo.\n"
    )


async def _call_stdio_tool(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    res = await session.call_tool(name, arguments=arguments)
    first = res.content[0]
    assert isinstance(first, TextContent), f"Expected TextContent for {name}, got {type(first)}"
    parsed = json.loads(first.text)
    assert isinstance(parsed, dict)
    return parsed
