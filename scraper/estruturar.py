"""
Estruturação de DOCUMENTOS (programa / adendo / quadro de horário) SEM N8N.

Fluxo por doc: baixa o PDF → extrai o texto (pypdf) → manda pro Claude →
devolve JSON no schema que o APP e o CHATBOT já consomem:

  programa  → {"oficiais": {...}, "provas": [{numero,nome,altura,tabela,
               categoria,data,horario,premiacao}], "regulamento": "..."}
  horarios  → {"dias": [{data, dia_semana, "horarios": [{prova_numero,hora,pista}]}]}
  adendo    → só texto_extraido (alteração textual; sem schema fixo)

Campos batem com resultados.html (estr.provas[].numero/data/horario/tabela;
estr.dias[].horarios[].prova_numero/hora/pista) e com a função chat.
Requer ANTHROPIC_API_KEY no ambiente. Best-effort: erro num doc não derruba o lote.
"""
import io
import json
import re
import requests

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-5-20250929"   # mesmo modelo da função chat
UA = {"User-Agent": "Mozilla/5.0 (cavalaria-estruturar)"}

_PROMPTS = {
    "programa": (
        "Você recebe o TEXTO de um PROGRAMA de concurso de hipismo (salto). Extraia "
        "SOMENTE o que está no texto, em JSON válido:\n"
        '{"oficiais": {"presidente_juri": "", "desenhador": "", "veterinario": ""}, '
        '"provas": [{"numero": "01", "nome": "", "altura": "1,10m", "tabela": "", '
        '"categoria": "", "data": "DD/MM/AAAA", "horario": "HH:MM", "premiacao": ""}], '
        '"regulamento": "resumo curto"}\n'
        "Campo ausente = string vazia. Responda APENAS o JSON, sem texto antes/depois."
    ),
    "horarios": (
        "Você recebe o TEXTO de um QUADRO DE HORÁRIOS de concurso de hipismo (salto). "
        "Extraia em JSON válido, lendo SOMENTE o que está no texto:\n"
        '{"dias": [{"data": "DD/MM/AAAA", "dia_semana": "sexta-feira", '
        '"horarios": [{"prova_numero": "01", "hora": "HH:MM", "prova_nome": "", '
        '"altura": "1,20m", "categoria": "", "tabela": "", "pista": ""}]}]}\n'
        "Significado dos campos:\n"
        "- altura: altura da prova no formato '1,20m' (vazio se não houver).\n"
        "- categoria: categorias/séries da prova (ex.: AM, SR, PJR, MIRINS, JOVENS, "
        "PRE-MIRINS, JUVENIS); junte com ' / ' se houver mais de uma.\n"
        "- tabela: característica/tipo da prova (ex.: Cronômetro, Cronômetro com "
        "desempate, Duas Fases, Tempo Ideal, Dois Percursos).\n"
        "- pista: nome da pista/arena onde a prova ocorre.\n"
        "Campo ausente = string vazia. Responda APENAS o JSON, sem texto antes/depois."
    ),
    "adendo": (
        "Você recebe o TEXTO de um ADENDO de concurso de hipismo (salto). Adendo = "
        "alterações/correções ao programa ou horários já publicados. Estruture em "
        "JSON válido, lendo SOMENTE o que está no texto:\n"
        '{"numero_adendo": "", "data_publicacao": "DD/MM/AAAA", "resumo": "", '
        '"mudancas": [{"tipo": "", "prova_afetada": "", "descricao": "", '
        '"antes": "", "depois": ""}]}\n'
        "Significado dos campos:\n"
        "- resumo: 1 frase curta do que o adendo altera no geral.\n"
        "- mudancas: uma entrada por alteração descrita no texto.\n"
        "- tipo: classifique cada mudança em um destes (minúsculas, com _): "
        "alteracao_horario, inclusao_prova, cancelamento, alteracao_premiacao, "
        "alteracao_categoria, alteracao_altura, alteracao_pista, outro.\n"
        "- prova_afetada: prova(s) envolvida(s), ex.: 'PR 05' ou nome da prova.\n"
        "- descricao: o que mudou, em texto claro e curto.\n"
        "- antes/depois: só quando o texto disser o valor anterior e o novo "
        "(ex.: antes '14:00', depois '15:30'); senão deixe vazio.\n"
        "Campo ausente = string vazia. Responda APENAS o JSON, sem texto antes/depois."
    ),
}


def extrair_texto_pdf(url, timeout=60):
    """Baixa o PDF e extrai o texto (pypdf). Tolerante a Content-Length errado (stream)."""
    from pypdf import PdfReader
    r = requests.get(url, headers=UA, timeout=timeout, stream=True)
    r.raise_for_status()
    data = r.raw.read(decode_content=True)
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((pg.extract_text() or "") for pg in reader.pages).strip()


def estruturar(tipo, texto, api_key):
    """Manda o texto pro Claude e devolve o dict estruturado (ou None se o tipo não
    tem schema / texto vazio / resposta inválida)."""
    prompt = _PROMPTS.get(tipo)
    if not prompt or not texto:
        return None
    body = {
        "model": MODEL, "max_tokens": 8192,   # programas grandes (50+ provas) cabem
        "messages": [{"role": "user", "content": prompt + "\n\n=== TEXTO ===\n" + texto[:60000]}],
    }
    r = requests.post(ANTHROPIC_URL, timeout=180, data=json.dumps(body), headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    r.raise_for_status()
    txt = "".join(b.get("text", "") for b in r.json().get("content", []))
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None
