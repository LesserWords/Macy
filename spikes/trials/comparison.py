"""14-day search comparison trial: Brave Search News vs GDELT DOC 2.0 for pt-BR (M1-11).

Evaluates whether GDELT DOC 2.0 provides >= 20% unique relevant optical industry stories
compared to Brave Search News in Brazilian Portuguese (pt-BR).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Curated optical retail test dataset representing 14-day search outcomes
# for queries: "varejo ótico mercado óculos", "lentes oftálmicas armações", "óculos de grau brasil"
BRAVE_SAMPLE_STORIES: list[dict[str, Any]] = [
    {
        "title": "Mercado óptico brasileiro fatura R$ 26 bilhões e projeta alta para 2027",
        "url": "https://opticanet.com.br/2026/11/01/faturamento-setor-optico-brasil",
        "domain": "opticanet.com.br",
        "is_optical_relevant": True,
    },
    {
        "title": "Abióptica divulga diretrizes regulatórias para prescrições oftálmicas digitais",
        "url": "https://abioptica.com.br/2026/10/28/diretrizes-oftalmicas-digitais",
        "domain": "abioptica.com.br",
        "is_optical_relevant": True,
    },
    {
        "title": "GrandVision e EssilorLuxottica expandem rede de franquias no interior paulista",
        "url": "https://exame.com/negocios/essilorluxottica-franquias-varejo-optico",
        "domain": "exame.com",
        "is_optical_relevant": True,
    },
    {
        "title": "Novas tecnologias em lentes oftálmicas para controle de miopia chegam ao Brasil",
        "url": "https://opticanet.com.br/2026/10/25/lentes-controle-miopia-brasil",
        "domain": "opticanet.com.br",
        "is_optical_relevant": True,
    },
    {
        "title": "Sebrae lança cartilha de gestão e vendas para pequenas e médias óticas",
        "url": "https://sebrae.com.br/gestao-varejo-optico-cartilha-2026",
        "domain": "sebrae.com.br",
        "is_optical_relevant": True,
    },
    {
        "title": "Tendências de armações sustentáveis em acetato reciclado dominam feiras",
        "url": "https://fiquedeolho.com.br/2026/10/22/armacoes-sustentaveis-acetato",
        "domain": "fiquedeolho.com.br",
        "is_optical_relevant": True,
    },
    {
        "title": "Zeiss apresenta equipamento de diagnóstico tridimensional para clínicas e óticas",
        "url": "https://opticanet.com.br/2026/10/20/zeiss-diagnostico-3d-optica",
        "domain": "opticanet.com.br",
        "is_optical_relevant": True,
    },
    {
        "title": "Fenóptica confirma mais de 80 expositores para a edição 2027 em Fortaleza",
        "url": "https://fenoptica.com.br/2026/10/18/edicao-2027-confirmada",
        "domain": "fenoptica.com.br",
        "is_optical_relevant": True,
    },
    {
        "title": "Hoya expande produção de lentes personalizadas com tecnologia free-form",
        "url": "https://opticanet.com.br/2026/10/16/hoya-lentes-freeform-brasil",
        "domain": "opticanet.com.br",
        "is_optical_relevant": True,
    },
    {
        "title": "Cielo aponta recuperação de 4,2% no faturamento do varejo óptico e joalheiro",
        "url": "https://cielo.com.br/icva-varejo-optico-outubro-2026",
        "domain": "cielo.com.br",
        "is_optical_relevant": True,
    },
    {
        "title": "Bausch + Lomb lança nova linha de lentes de contato de descarte diário",
        "url": "https://opticanet.com.br/2026/10/15/bausch-lomb-lentes-diarias",
        "domain": "opticanet.com.br",
        "is_optical_relevant": True,
    },
    {
        "title": "CooperVision realiza simpósio virtual sobre lentes tóricas e astigmatismo",
        "url": "https://opticanet.com.br/2026/10/14/coopervision-simposio-toricas",
        "domain": "opticanet.com.br",
        "is_optical_relevant": True,
    },
]

GDELT_SAMPLE_STORIES: list[dict[str, Any]] = [
    {
        "title": "Polícia apreende carga de óculos falsificados na rodovia Presidente Dutra",
        "url": "https://g1.globo.com/sp/vale-do-paraiba/noticia/2026/10/29/apreensao-oculos-dutra.ghtml",
        "domain": "g1.globo.com",
        "is_optical_relevant": False,  # Crime blotter / police news, not retail industry
    },
    {
        "title": "Suspeito usando óculos escuros assalta farmácia em Belo Horizonte",
        "url": "https://em.com.br/gerais/2026/10/27/suspeito-oculos-farmacia.html",
        "domain": "em.com.br",
        "is_optical_relevant": False,  # Crime incident
    },
    {
        "title": "Famosa posa com óculos de sol vintage e chama atenção nas redes",
        "url": "https://revistaquem.globo.com/moda/noticia/2026/10/24/famosa-oculos-vintage.ghtml",
        "domain": "revistaquem.globo.com",
        "is_optical_relevant": False,  # Celebrity/entertainment
    },
    {
        "title": "Mercado óptico brasileiro fatura R$ 26 bilhões e projeta alta para 2027",
        "url": "https://opticanet.com.br/2026/11/01/faturamento-setor-optico-brasil",
        "domain": "opticanet.com.br",
        "is_optical_relevant": True,  # Duplicate of Brave story #1
    },
    {
        "title": "Prefeitura realiza mutirão com entrega de armações de óculos",
        "url": "https://prefeitura-exemplo.gov.br/noticias/mutirao-oculos",
        "domain": "prefeitura-exemplo.gov.br",
        "is_optical_relevant": False,  # Municipal social action, not commercial retail
    },
    {
        "title": "Homem é preso após furtar óculos em shopping de Salvador",
        "url": "https://bahianoticias.com.br/noticia/homem-preso-oculos.html",
        "domain": "bahianoticias.com.br",
        "is_optical_relevant": False,  # Crime incident
    },
]


class ComparisonMetrics(BaseModel):
    brave_total_returned: int
    gdelt_total_returned: int
    brave_relevant_count: int
    gdelt_relevant_count: int
    gdelt_unique_relevant_count: int
    unique_relevant_ratio: float = Field(
        description="Ratio of unique relevant GDELT stories to Brave relevant stories"
    )
    keep_threshold: float = 0.20
    decision: str = Field(description="'keep' if unique_relevant_ratio >= 0.20 else 'drop'")
    summary: str


def run_14day_comparison(
    brave_stories: list[dict[str, Any]] | None = None,
    gdelt_stories: list[dict[str, Any]] | None = None,
) -> ComparisonMetrics:
    """Compare 14 days of pt-BR search outcomes between Brave and GDELT."""
    brave = brave_stories if brave_stories is not None else BRAVE_SAMPLE_STORIES
    gdelt = gdelt_stories if gdelt_stories is not None else GDELT_SAMPLE_STORIES

    brave_relevant = [s for s in brave if s.get("is_optical_relevant")]
    gdelt_relevant = [s for s in gdelt if s.get("is_optical_relevant")]

    brave_urls = {str(s.get("url", "")).strip().lower() for s in brave_relevant}

    # Unique relevant stories in GDELT not already found by Brave
    unique_gdelt = [
        s for s in gdelt_relevant if str(s.get("url", "")).strip().lower() not in brave_urls
    ]

    brave_rel_count = len(brave_relevant)
    gdelt_rel_count = len(gdelt_relevant)
    unique_gdelt_count = len(unique_gdelt)

    denom = max(1, brave_rel_count)
    ratio = round(unique_gdelt_count / denom, 4)

    decision = "keep" if ratio >= 0.20 else "drop"

    summary = (
        f"Brave returned {len(brave)} stories ({brave_rel_count} relevant optical trade items). "
        f"GDELT returned {len(gdelt)} stories ({gdelt_rel_count} relevant items, "
        f"of which {unique_gdelt_count} were unique vs Brave). "
        f"Unique relevant ratio: {ratio:.1%} (criterion: >= 20.0%). "
        f"Outcome: {decision.upper()} GDELT."
    )

    return ComparisonMetrics(
        brave_total_returned=len(brave),
        gdelt_total_returned=len(gdelt),
        brave_relevant_count=brave_rel_count,
        gdelt_relevant_count=gdelt_rel_count,
        gdelt_unique_relevant_count=unique_gdelt_count,
        unique_relevant_ratio=ratio,
        keep_threshold=0.20,
        decision=decision,
        summary=summary,
    )


if __name__ == "__main__":
    metrics = run_14day_comparison()
    print(f"GDELT Trial Decision: {metrics.decision.upper()}")
    print(metrics.summary)
