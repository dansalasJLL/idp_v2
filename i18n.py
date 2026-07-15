"""
i18n.py — UI translation layer (English / Portuguese).

Design principle: translation is a DISPLAY concern only. Every obligation's
underlying data — category, priority, risk_level, risk_type, trigger_type,
responsible_party — stays in canonical English throughout the pipeline,
storage (portfolio.py, cache.py), and exports. Only what's rendered on screen
is translated. This means:
  - Filtering, sorting, coloring, and all business logic in app.py/risk.py/
    reduce_obligations.py are completely unaffected by language.
  - Power BI / exports stay in one consistent schema regardless of which
    language a user viewed the app in when they processed a contract.
  - Adding a new UI language never touches extraction or storage code —
    only this file.

Two lookup surfaces:
  - STRINGS[lang][key]   : static UI chrome (labels, buttons, captions)
  - ENUM_LABELS[lang][kind][canonical_value] : display label for a data value
    (e.g. ENUM_LABELS["pt"]["priority"]["High"] -> "Alto")

t(key, **kwargs)         -> translated + .format(**kwargs)'d string for the
                            current language (st.session_state.lang)
label(kind, value)       -> translated display label for a canonical enum
                            value; falls back to the value itself if no
                            translation exists (never raises, never blanks).
"""

from __future__ import annotations

SUPPORTED_LANGUAGES = {"en": "English", "pt": "Português (Brasil)"}
DEFAULT_LANGUAGE = "en"


# --------------------------------------------------------------------------- #
# Static UI strings
# --------------------------------------------------------------------------- #
STRINGS = {
    "en": {
        "brand": "IDP Agent",
        "app_title": "Intelligent Document Processing Agent",
        "sidebar_subtitle": "Master Service Agreement / Lease → compliance checklist",
        "language_label": "Language",
        "source_label": "Source",
        "source_demo": "Demo dataset",
        "source_upload": "Upload MSA (live)",
        "select_demo_contract": "Select demo contract",
        "document_type_label": "Document type",
        "document_type_question": "Which taxonomy should the agent extract against?",
        "profile_msa": "Master Service Agreement",
        "profile_lease": "Lease Administration",
        "data_classification_label": "Data classification",
        "synthetic_checkbox": "I confirm this is synthetic / sample data (required — the "
            "sandbox model is not cleared for real client contracts)",
        "real_data_caption": "🔒 Uploads are treated as **real client data** until confirmed "
            "synthetic above. Real data is blocked on this sandbox endpoint by design "
            "(section 5.3 / providers.assert_data_allowed) — swap to a cleared endpoint "
            "(e.g. Falcon) for real contracts.",
        "upload_label": "Upload an MSA or Lease (PDF)",
        "extracted_success_demo": "✅ Extracted {n} obligations from {name}",
        "extracted_success_live": "Extracted {n} obligations.",
        "live_unavailable_warning": "Live run unavailable ({e}). Showing the demo dataset.",
        "upload_prompt_info": "Upload a PDF to run the live pipeline, or switch to the demo dataset.",
        "risk_settings_header": "Risk settings",
        "risk_threshold_label": "High-risk $ threshold",
        "risk_threshold_help": "A monetary penalty at or above this amount is High risk; "
            "below it, Medium. Penalties with no stated amount stay High.",
        "filters_header": "Filters",
        "filter_priority": "Priority",
        "filter_risk_level": "Risk level",
        "filter_category": "Category",
        "filter_party": "Responsible party",
        "filter_only_review": "Only items needing review",
        "filter_hide_completed": "Hide completed",
        "sandbox_warning": "**Sandbox mode — Demo data.** Cleared for synthetic / sample "
            "contracts only. Do not upload real client MSAs here. Production runs the "
            "identical pipeline against a JLL-sanctioned, data-cleared endpoint (e.g. "
            "Falcon) so real contracts stay in the governed envelope.",
        "metric_obligations": "Obligations",
        "metric_high_risk": "🚨 High risk",
        "metric_high_priority": "High priority",
        "metric_needs_review": "Needs review",
        "metric_completed": "Completed",
        "metric_categories": "Categories",
        "checklist_progress": "Checklist {pct}% complete",
        "doc_subtitle": "{pages} pages · {n} obligations extracted",
        "cost_panel_header": "💵 Run cost & efficiency (live run)",
        "cost_compute": "Compute cost",
        "cost_clauses": "Clauses processed",
        "cost_cache_hits": "Cache hits",
        "cost_wall_clock": "Wall-clock",
        "cost_caption": "{tok:,} tokens · model {model}. Cached clauses cost nothing — "
            "re-running this contract, or processing another that shares boilerplate, "
            "reuses these results for free.",
        "pbi_panel_header": "📊 Power BI portfolio (auto-updated)",
        "pbi_contracts": "Contracts",
        "pbi_obligations": "Obligations",
        "pbi_high_risk": "High risk",
        "pbi_penalty_exposure": "Penalty exposure",
        "pbi_caption": "Every processed contract is appended to a Power BI-ready dataset — "
            "no manual export. Point Power BI at the store folder (Get Data → Folder) or "
            "the read-only API (Get Data → Web); it refreshes on Power BI's schedule. See "
            "POWERBI.md for the 5-minute setup.",
        "high_risk_banner_title": "🚨 {n} high-risk obligation{s} require attention",
        "high_risk_banner_body": "see the 🚨 High Risk tab for each item and its recommended mitigation.",
        "high_risk_banner_breakdown": "By risk type —",
        "cost_missed_title": "⚠️ Cost if missed",
        "cost_missed_body": "— {n} of {total} obligations carry a financial penalty if the "
            "detail is overlooked. For example:",
        "tab_high_risk": "🚨 High Risk",
        "tab_checklist": "✅ Checklist",
        "tab_table": "📋 Table",
        "tab_export": "⬇️ Export",
        "no_high_risk": "✅ No high-risk obligations in the current filter set.",
        "high_risk_summary": "**{n} high-risk obligation{s}** · {done}/{n} addressed. "
            "Each carries recommended mitigation below.",
        "high_risk_progress": "High-risk items addressed: {done}/{n}",
        "no_obligations_match": "No obligations match the current filters.",
        "no_rows_filters": "No rows for the current filters.",
        "priority_suffix": "priority",
        "risk_suffix": "risk",
        "party_label_short": "Party",
        "confidence_label": "Confidence",
        "low_confidence_warning": "⚠️ Low confidence — flagged for human review.",
        "trigger_label": "Trigger",
        "deadline_label": "Deadline",
        "frequency_label": "Frequency",
        "penalty_label": "Penalty if missed",
        "mitigation_header": "🛡️ Risk mitigation",
        "mitigation_model": "model-suggested",
        "mitigation_rules": "standard playbook",
        "source_clause_label": "Source clause",
        "page_label": "page",
        "mark_complete": "✓ Mark complete",
        "reopen": "↺ Reopen",
        "export_intro": "Export the **filtered** checklist for the CRE team.",
        "download_excel": "⬇️ Download Excel (Smartsheet-ready)",
        "download_csv": "⬇️ Download CSV",
        "export_count": "{n} obligations in current export (after filters).",
        "nothing_to_export": "Nothing to export with the current filters.",
    },
    "pt": {
        "brand": "Agente IDP",
        "app_title": "Agente de Processamento Inteligente de Documentos",
        "sidebar_subtitle": "Contrato de Prestação de Serviços / Locação → checklist de conformidade",
        "language_label": "Idioma",
        "source_label": "Origem",
        "source_demo": "Conjunto de demonstração",
        "source_upload": "Enviar contrato (ao vivo)",
        "select_demo_contract": "Selecionar contrato de demonstração",
        "document_type_label": "Tipo de documento",
        "document_type_question": "Contra qual taxonomia o agente deve extrair?",
        "profile_msa": "Contrato de Prestação de Serviços",
        "profile_lease": "Administração de Locação",
        "data_classification_label": "Classificação de dados",
        "synthetic_checkbox": "Confirmo que estes são dados sintéticos / de amostra "
            "(obrigatório — o modelo sandbox não está autorizado para contratos reais de clientes)",
        "real_data_caption": "🔒 Os envios são tratados como **dados reais de clientes** até "
            "que sejam confirmados como sintéticos acima. Dados reais são bloqueados neste "
            "endpoint sandbox por design (seção 5.3 / providers.assert_data_allowed) — use "
            "um endpoint autorizado (ex.: Falcon) para contratos reais.",
        "upload_label": "Enviar um Contrato ou Locação (PDF)",
        "extracted_success_demo": "✅ {n} obrigações extraídas de {name}",
        "extracted_success_live": "{n} obrigações extraídas.",
        "live_unavailable_warning": "Execução ao vivo indisponível ({e}). Exibindo o conjunto de demonstração.",
        "upload_prompt_info": "Envie um PDF para executar o pipeline ao vivo, ou alterne para o conjunto de demonstração.",
        "risk_settings_header": "Configurações de risco",
        "risk_threshold_label": "Limite de risco alto (US$)",
        "risk_threshold_help": "Uma penalidade monetária igual ou acima deste valor é risco "
            "Alto; abaixo, Médio. Penalidades sem valor informado permanecem Alto.",
        "filters_header": "Filtros",
        "filter_priority": "Prioridade",
        "filter_risk_level": "Nível de risco",
        "filter_category": "Categoria",
        "filter_party": "Parte responsável",
        "filter_only_review": "Somente itens que precisam de revisão",
        "filter_hide_completed": "Ocultar concluídos",
        "sandbox_warning": "**Modo sandbox — Dados de demonstração.** Autorizado apenas para "
            "contratos sintéticos / de amostra. Não envie contratos reais de clientes aqui. "
            "A produção executa o mesmo pipeline em um endpoint autorizado pela JLL e "
            "habilitado para dados reais (ex.: Falcon), mantendo os contratos reais no "
            "ambiente controlado.",
        "metric_obligations": "Obrigações",
        "metric_high_risk": "🚨 Risco alto",
        "metric_high_priority": "Prioridade alta",
        "metric_needs_review": "Precisa revisão",
        "metric_completed": "Concluído",
        "metric_categories": "Categorias",
        "checklist_progress": "Checklist {pct}% concluído",
        "doc_subtitle": "{pages} páginas · {n} obrigações extraídas",
        "cost_panel_header": "💵 Custo e eficiência da execução (execução ao vivo)",
        "cost_compute": "Custo computacional",
        "cost_clauses": "Cláusulas processadas",
        "cost_cache_hits": "Acertos de cache",
        "cost_wall_clock": "Tempo total",
        "cost_caption": "{tok:,} tokens · modelo {model}. Cláusulas em cache não custam nada — "
            "reprocessar este contrato, ou processar outro que compartilhe cláusulas padrão, "
            "reaproveita esses resultados gratuitamente.",
        "pbi_panel_header": "📊 Portfólio Power BI (atualização automática)",
        "pbi_contracts": "Contratos",
        "pbi_obligations": "Obrigações",
        "pbi_high_risk": "Risco alto",
        "pbi_penalty_exposure": "Exposição a penalidades",
        "pbi_caption": "Todo contrato processado é adicionado a um conjunto de dados pronto "
            "para o Power BI — sem exportação manual. Aponte o Power BI para a pasta de "
            "armazenamento (Obter Dados → Pasta) ou para a API somente leitura (Obter Dados "
            "→ Web); a atualização segue o agendamento do próprio Power BI. Veja o POWERBI.md "
            "para a configuração de 5 minutos.",
        "high_risk_banner_title": "🚨 {n} obrigaç{oes} de alto risco requer{em} atenção",
        "high_risk_banner_body": "veja a aba 🚨 Risco Alto para cada item e sua mitigação recomendada.",
        "high_risk_banner_breakdown": "Por tipo de risco —",
        "cost_missed_title": "⚠️ Custo em caso de descumprimento",
        "cost_missed_body": "— {n} de {total} obrigações têm penalidade financeira caso o "
            "detalhe seja negligenciado. Por exemplo:",
        "tab_high_risk": "🚨 Risco Alto",
        "tab_checklist": "✅ Checklist",
        "tab_table": "📋 Tabela",
        "tab_export": "⬇️ Exportar",
        "no_high_risk": "✅ Nenhuma obrigação de alto risco no conjunto de filtros atual.",
        "high_risk_summary": "**{n} obrigaç{oes} de alto risco** · {done}/{n} tratada{s}. "
            "Cada uma traz a mitigação recomendada abaixo.",
        "high_risk_progress": "Itens de alto risco tratados: {done}/{n}",
        "no_obligations_match": "Nenhuma obrigação corresponde aos filtros atuais.",
        "no_rows_filters": "Nenhuma linha para os filtros atuais.",
        "priority_suffix": "prioridade",
        "risk_suffix": "risco",
        "party_label_short": "Parte",
        "confidence_label": "Confiança",
        "low_confidence_warning": "⚠️ Confiança baixa — sinalizado para revisão humana.",
        "trigger_label": "Gatilho",
        "deadline_label": "Prazo",
        "frequency_label": "Frequência",
        "penalty_label": "Penalidade em caso de descumprimento",
        "mitigation_header": "🛡️ Mitigação de risco",
        "mitigation_model": "sugerido pelo modelo",
        "mitigation_rules": "playbook padrão",
        "source_clause_label": "Cláusula de origem",
        "page_label": "página",
        "mark_complete": "✓ Marcar como concluído",
        "reopen": "↺ Reabrir",
        "export_intro": "Exportar o checklist **filtrado** para a equipe de CRE.",
        "download_excel": "⬇️ Baixar Excel (pronto para Smartsheet)",
        "download_csv": "⬇️ Baixar CSV",
        "export_count": "{n} obrigações na exportação atual (após filtros).",
        "nothing_to_export": "Nada para exportar com os filtros atuais.",
    },
}


# --------------------------------------------------------------------------- #
# Enum value -> display label, per language. Underlying data stays canonical
# English (see module docstring) — this is display-only.
# --------------------------------------------------------------------------- #
ENUM_LABELS = {
    "en": {},  # English canonical values display as-is; no lookup needed.
    "pt": {
        "priority": {"High": "Alto", "Medium": "Médio", "Low": "Baixo"},
        "risk_level": {"High": "Alto", "Medium": "Médio", "Low": "Baixo"},
        "risk_type": {
            "Financial": "Financeiro", "Legal": "Jurídico", "Regulatory": "Regulatório",
            "Operational": "Operacional", "Reputational": "Reputacional",
            "Contractual": "Contratual", "None": "Nenhum",
        },
        "category": {
            "Financial": "Financeiro", "Insurance": "Seguro", "Reporting": "Relatórios",
            "Service Level (SLA)": "Nível de Serviço (SLA)",
            "Compliance & Regulatory": "Conformidade e Regulatório", "Notice": "Aviso",
            "Term & Renewal": "Prazo e Renovação", "Termination": "Rescisão",
            "Indemnity & Liability": "Indenização e Responsabilidade",
            "Confidentiality & Data": "Confidencialidade e Dados",
            "Payment": "Pagamento", "Maintenance": "Manutenção",
        },
        "trigger_type": {
            "Specific date": "Data específica", "Recurring": "Recorrente",
            "Event-driven": "Baseado em evento", "Conditional": "Condicional",
        },
        "responsible_party": {
            "JLL": "JLL", "Client": "Cliente", "Vendor": "Fornecedor", "Both": "Ambas as partes",
            "Landlord": "Locador", "Tenant": "Locatário",
        },
    },
}


def get_lang() -> str:
    """Current UI language from session state, defaulting safely."""
    try:
        import streamlit as st
        return st.session_state.get("lang", DEFAULT_LANGUAGE)
    except Exception:
        return DEFAULT_LANGUAGE


def t(key: str, lang: str = None, **kwargs) -> str:
    """Translated UI string for `key` in the current (or given) language.
    Falls back to English, then to the key itself, so a missing translation
    never crashes the UI or renders blank."""
    lang = lang or get_lang()
    text = STRINGS.get(lang, {}).get(key) or STRINGS[DEFAULT_LANGUAGE].get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def label(kind: str, value: str, lang: str = None) -> str:
    """Translated display label for a canonical data value (e.g. label("priority",
    "High") -> "Alto" in Portuguese). Returns the original value unchanged if no
    translation exists for this language/kind/value — never raises, never blanks,
    so unfamiliar or future enum values still display something sensible."""
    lang = lang or get_lang()
    return ENUM_LABELS.get(lang, {}).get(kind, {}).get(value, value)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    print("=== i18n.py self-test ===\n")

    # Every key present in English must also exist in Portuguese (and vice versa)
    en_keys = set(STRINGS["en"].keys())
    pt_keys = set(STRINGS["pt"].keys())
    missing_in_pt = en_keys - pt_keys
    missing_in_en = pt_keys - en_keys
    assert not missing_in_pt, f"Keys missing in pt: {missing_in_pt}"
    assert not missing_in_en, f"Keys missing in en: {missing_in_en}"
    print(f"Key parity across languages: OK ({len(en_keys)} keys each)")

    # t() basic lookup + formatting
    assert t("metric_obligations", lang="en") == "Obligations"
    assert t("metric_obligations", lang="pt") == "Obrigações"
    assert t("checklist_progress", lang="en", pct=42) == "Checklist 42% complete"
    assert t("checklist_progress", lang="pt", pct=42) == "Checklist 42% concluído"
    print("t() lookup + formatting: OK")

    # t() never crashes on a missing key or missing format args
    assert t("this_key_does_not_exist", lang="pt") == "this_key_does_not_exist"
    assert t("metric_obligations", lang="xx") == "Obligations"  # unknown lang -> English fallback
    print("t() graceful fallback (missing key / unknown language): OK")

    # label() translates known enum values, passes through unknowns
    assert label("priority", "High", lang="pt") == "Alto"
    assert label("priority", "High", lang="en") == "High"
    assert label("category", "Insurance", lang="pt") == "Seguro"
    assert label("category", "SomeNewCategory", lang="pt") == "SomeNewCategory"  # unknown -> passthrough
    assert label("responsible_party", "Tenant", lang="pt") == "Locatário"
    print("label() enum translation + passthrough for unknown values: OK")

    # Every enum value actually used in idp_extraction.py has a pt label (parity check)
    try:
        from idp_extraction import Category, Party, TriggerType, Priority, RiskType
        for enum_cls, kind in [(Category, "category"), (Party, "responsible_party"),
                               (TriggerType, "trigger_type"), (Priority, "priority")]:
            for member in enum_cls:
                assert member.value in ENUM_LABELS["pt"].get(kind, {}), \
                    f"{kind}='{member.value}' has no Portuguese label"
        for member in RiskType:
            assert member.value in ENUM_LABELS["pt"]["risk_type"], \
                f"risk_type='{member.value}' has no Portuguese label"
        print("Full enum coverage (every canonical value has a pt label): OK")
    except ImportError:
        print("(skipped enum-coverage check — idp_extraction not importable standalone)")

    print("\nAll i18n self-tests passed.")
