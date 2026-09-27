from __future__ import annotations

"""Reorganisation intelligente du document entier (--smart-merge).

Contrairement a claude_structuring.py (une image de page envoyee a Claude,
en isolation), ce module fait une passe SUPPLEMENTAIRE, apres l'extraction
page par page (locale ou Claude, peu importe) : on envoie a Claude un resume
texte (pas d'images - beaucoup moins cher) de tous les tableaux deja extraits,
et on lui demande de decider :

- quels tableaux sont en realite la continuation d'un seul et meme tableau
  sur plusieurs pages consecutives (a fusionner en un tableau unique) ;
- quels tableaux, sans etre la suite les uns des autres, portent sur le
  meme sujet et gagneraient a etre presentes ensemble ;
- un titre et une categorie parlants pour chaque groupe.

C'est une passe volontairement additive et optionnelle : si elle echoue ou
n'est pas activee, le resultat page par page (deja fiable) n'est en rien
modifie. Les groupes obtenus s'ajoutent dans une feuille Excel dediee, sans
remplacer les feuilles par page existantes.
"""

import json
import re
from pathlib import Path

from .claude_structuring import MODEL_PRICING_PER_MTOK  # noqa: F401 (reexporte pour cli.py)
from .models import ConsolidatedGroup, ExtractedRow, ExtractedTable, ExtractionResult


DEFAULT_REORG_MODEL = "claude-sonnet-5"

MAX_SAMPLE_ROWS = 2
MAX_TABLES_IN_SUMMARY = 400  # garde-fou sur des documents extremes

REORGANIZATION_PROMPT = """Tu recois un resume des tableaux deja extraits d'un document PDF (un tableau par page, potentiellement plusieurs tableaux par page). Etudie leur structure (en-tetes, nombre de colonnes) et leur contenu pour proposer une meilleure organisation.

Deux types de regroupements possibles :
1. "merged": true -> ces tableaux sont en realite LE MEME tableau qui continue sur des pages consecutives (memes colonnes / meme sujet, la suite logique des lignes). Ils doivent etre fusionnes en un seul tableau continu.
2. "merged": false -> ces tableaux sont de nature similaire (meme sujet/domaine) mais restent des tableaux distincts (colonnes differentes ou donnees independantes). Ils gagnent juste a etre presentes ensemble, sous un meme titre de section.

Ne force aucun regroupement : si un tableau est isole ou ambigu, laisse-le dans "ungrouped_table_ids" plutot que de forcer un rapprochement hasardeux. Un groupe "merged": true doit avoir une tres grande confiance de continuite reelle (memes en-tetes ou structure quasi identique, ordre de pages consecutif).

Pour chaque groupe, ajoute aussi "reason" : une phrase courte (une quinzaine de mots maximum) qui explique CONCRETEMENT pourquoi ces tableaux sont regroupes - le lecteur du fichier Excel final doit comprendre ton raisonnement d'un coup d'oeil, pas juste voir un resultat brut. Exemples : "Meme en-tete (Nom, Poste, Montant) et suite logique des lignes entre les pages 3 et 4." ou "Deux glossaires d'abreviations sur des sujets techniques proches, presentes separement dans le document source."

Reponds UNIQUEMENT avec un objet JSON (aucun texte avant/apres, aucun bloc markdown), de cette forme exacte :

{
  "groups": [
    {
      "title": "Titre court et parlant du groupe",
      "category": "categorie du domaine (ex: financier, juridique, sportif, glossaire, planning...)",
      "merged": true,
      "reason": "Explication courte et concrete du regroupement",
      "table_ids": ["page_3_claude_1", "page_4_claude_1"]
    }
  ],
  "ungrouped_table_ids": ["page_10_claude_1"]
}

Chaque table_id du resume doit apparaitre exactement une fois au total, soit dans un groupe, soit dans ungrouped_table_ids. Le JSON doit etre strictement valide."""


def get_reorg_model_pricing_note() -> str:
    return "Cout indicatif uniquement, base sur les tarifs connus au moment de l'ecriture du module."


def build_document_summary(result: ExtractionResult) -> list[dict]:
    """Construit un resume compact (pas les donnees completes) de tous les
    tableaux du document, pour rester bon marche a envoyer meme sur un tres
    gros document : en-tetes + quelques lignes d'exemple suffisent a Claude
    pour juger de la continuite/similitude, pas besoin du contenu integral."""
    summary: list[dict] = []
    for page in result.pages:
        for table in page.tables:
            if len(summary) >= MAX_TABLES_IN_SUMMARY:
                return summary
            headers = table.rows[0].values if (table.has_header and table.rows) else []
            data_rows = table.rows[1:] if (table.has_header and table.rows) else table.rows
            sample = [row.values for row in data_rows[:MAX_SAMPLE_ROWS]]
            summary.append(
                {
                    "page": page.page_number,
                    "table_id": table.table_id,
                    "headers": headers,
                    "column_count": table.column_count,
                    "row_count": table.row_count,
                    "sample_rows": sample,
                }
            )
    return summary


def reorganize_document_with_claude(
    result: ExtractionResult,
    api_key: str,
    model: str = DEFAULT_REORG_MODEL,
    max_retries: int = 1,
) -> tuple[dict | None, list[str], dict | None]:
    """Envoie le resume du document a Claude pour decider des regroupements.

    Retourne (plan_ou_None, avertissements, usage_tokens_ou_None). Comme pour
    claude_structuring.py, aucune exception ne doit jamais remonter : en cas
    d'echec, on retourne None et le resultat page par page reste inchange."""
    warnings: list[str] = []

    summary = build_document_summary(result)
    if len(summary) < 2:
        return None, ["Reorganisation ignoree : moins de deux tableaux detectes dans le document."], None

    try:
        import anthropic
    except ImportError:
        return None, ["Module 'anthropic' non installe - lancez : pip install anthropic"], None

    try:
        client = anthropic.Anthropic(api_key=api_key)
    except Exception as exc:
        return None, [f"Client Anthropic non initialisable : {type(exc).__name__} - {exc}"], None

    payload_text = json.dumps({"tables": summary}, ensure_ascii=False)

    last_error: str | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": REORGANIZATION_PROMPT},
                            {"type": "text", "text": payload_text},
                        ],
                    }
                ],
            )
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue

        try:
            raw_text = "".join(block.text for block in response.content if hasattr(block, "text"))
            usage = {
                "input_tokens": getattr(response.usage, "input_tokens", 0),
                "output_tokens": getattr(response.usage, "output_tokens", 0),
                "model": model,
            }
            plan = parse_reorganization_json(raw_text)
            if plan is None:
                last_error = "Reponse Claude non-JSON ou malformee."
                continue
        except Exception as exc:
            last_error = f"Reponse Claude exploitee incorrectement : {type(exc).__name__} - {exc}"
            continue

        return plan, warnings, usage

    warnings.append(f"Reorganisation echouee apres {max_retries + 1} tentative(s) : {last_error}")
    return None, warnings, None


def parse_reorganization_json(raw_text: str) -> dict | None:
    text = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    if not text.startswith("{"):
        brace_index = text.find("{")
        if brace_index == -1:
            return None
        text = text[brace_index:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(text)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict) or "groups" not in data:
        return None
    return data


def apply_reorganization_plan(result: ExtractionResult, plan: dict) -> tuple[list[ConsolidatedGroup], list[str]]:
    """Applique le plan de Claude aux tableaux reellement extraits.

    Reste defensif : un table_id mentionne par Claude mais introuvable dans
    le resultat reel est simplement ignore (avec avertissement), un groupe
    a moins de deux tableaux valides est ignore (regrouper un seul tableau
    n'a pas de sens), et les colonnes de largeur differente au sein d'une
    fusion sont completees plutot que de faire echouer la fusion."""
    warnings: list[str] = []
    table_lookup: dict[str, ExtractedTable] = {
        table.table_id: table for page in result.pages for table in page.tables
    }

    groups: list[ConsolidatedGroup] = []
    seen_ids: set[str] = set()

    for raw_group in plan.get("groups") or []:
        table_ids = [tid for tid in (raw_group.get("table_ids") or []) if isinstance(tid, str)]
        resolved: list[ExtractedTable] = []
        missing: list[str] = []
        for tid in table_ids:
            table = table_lookup.get(tid)
            if table is None:
                missing.append(tid)
                continue
            resolved.append(table)
            seen_ids.add(tid)

        if missing:
            warnings.append(
                f"Groupe '{raw_group.get('title', '?')}' : {len(missing)} tableau(x) reference(s) introuvable(s), ignore(s)."
            )

        if len(resolved) < 2:
            continue

        title = str(raw_group.get("title") or "Groupe de tableaux").strip()
        category = str(raw_group.get("category") or "").strip()
        reason = str(raw_group.get("reason") or "").strip()
        merged = bool(raw_group.get("merged", False))

        if merged:
            merged_table, merge_warnings = merge_tables(resolved, title)
            groups.append(
                ConsolidatedGroup(
                    title=title,
                    category=category,
                    merged=True,
                    tables=[merged_table],
                    source_table_ids=table_ids,
                    warnings=merge_warnings,
                    reason=reason,
                )
            )
        else:
            groups.append(
                ConsolidatedGroup(
                    title=title,
                    category=category,
                    merged=False,
                    tables=resolved,
                    source_table_ids=table_ids,
                    warnings=[],
                    reason=reason,
                )
            )

    return groups, warnings


SOURCE_PAGE_COLUMN_LABEL = "Page source"


def merge_tables(tables: list[ExtractedTable], title: str) -> tuple[ExtractedTable, list[str]]:
    """Fusionne plusieurs tableaux en un seul, ET ajoute une colonne "Page
    source" a la fin de chaque ligne : la fusion doit rester tracable a
    l'oeil nu (quelle ligne venait de quelle page d'origine), pas juste
    fondue silencieusement dans un tableau anonyme."""
    warnings: list[str] = []
    column_counts = {table.column_count for table in tables}
    if len(column_counts) > 1:
        warnings.append(
            f"Structures legerement differentes entre les tableaux fusionnes ({sorted(column_counts)} colonnes) - verifier l'alignement."
        )
    max_cols = max(column_counts) if column_counts else 0

    first = tables[0]
    merged_rows: list[ExtractedRow] = []

    header_values: list[str] | None = None
    if first.has_header and first.rows:
        header_values = list(first.rows[0].values) + [""] * (max_cols - len(first.rows[0].values))
        header_with_source = header_values + [SOURCE_PAGE_COLUMN_LABEL]
        merged_rows.append(ExtractedRow(values=header_with_source, source_line=first.rows[0].source_line, kind="table"))

    for table in tables:
        data_rows = table.rows[1:] if (table.has_header and table.rows) else table.rows
        for row in data_rows:
            # Ignore une ligne qui reproduit simplement l'en-tete (frequent
            # quand un tableau scanne/rendu par page repete son en-tete a
            # chaque page) pour ne pas la dupliquer dans le tableau fusionne.
            if header_values is not None and row.values == header_values[: len(row.values)]:
                continue
            padded = list(row.values) + [""] * (max_cols - len(row.values))
            padded_with_source = padded + [f"p.{table.page_number}"]
            merged_rows.append(ExtractedRow(values=padded_with_source, source_line=row.source_line, kind=row.kind))

    merged_confidence = round(sum(t.confidence for t in tables) / len(tables), 2) if tables else 0.0
    merged_id = f"consolide_{'_'.join(t.table_id for t in tables)}"[:120]

    merged_table = ExtractedTable(
        table_id=merged_id,
        page_number=first.page_number,
        rows=merged_rows,
        confidence=merged_confidence,
        warnings=warnings,
        has_header=first.has_header,
        detection_method="claude_merge",
    )
    return merged_table, warnings
