import json

from paper_dossier_extractor.merge import merge_partials
from paper_dossier_extractor.schemas import (
    PartialDossier,
    PartialEvidence,
    PartialFact,
    PartialMetric,
    SourcePage,
)


def _evidence(page: int, text: str) -> PartialEvidence:
    return PartialEvidence(page=page, source_text=text)


def _fact(
    name: str, page: int, text: str, description: str = ""
) -> PartialFact:
    return PartialFact(
        name=name,
        description=description,
        evidence=(_evidence(page, text),),
    )


def _metric(
    name: str,
    value: float | None,
    page: int,
    text: str,
    *,
    dataset: str | None = None,
    split: str | None = None,
    model: str | None = None,
) -> PartialMetric:
    return PartialMetric(
        name=name,
        reported_value=value,
        dataset=dataset,
        split=split,
        model=model,
        evidence=(_evidence(page, text),),
    )


def _pages(*items: tuple[int, str]) -> tuple[SourcePage, ...]:
    return tuple(SourcePage(page=page, text=text) for page, text in items)


def test_merge_deduplicates_facts_quotations_and_equal_metrics() -> None:
    source_pages = _pages(
        (17, "Dataset: Housing Data. RMSE was 2.0."),
        (18, "Housing Data was used on the test split. RMSE was 2.0."),
    )
    partials = (
        PartialDossier(
            datasets=(
                _fact("Housing-Data", 17, "Dataset: Housing Data."),
            ),
            metrics=(
                _metric(
                    "Root Mean Squared Error",
                    2.0,
                    17,
                    "RMSE was 2.0.",
                ),
            ),
        ),
        PartialDossier(
            datasets=(
                PartialFact(
                    name=" housing data ",
                    evidence=(
                        _evidence(17, " Dataset: Housing   Data. "),
                        _evidence(
                            18,
                            "Housing Data was used on the test split.",
                        ),
                    ),
                ),
            ),
            metrics=(
                _metric("rmse", 2.0, 18, "RMSE was 2.0."),
            ),
        ),
    )

    result = merge_partials(partials, source_pages)

    assert len(result.dossier.datasets) == 1
    assert [item.page for item in result.dossier.datasets[0].evidence] == [17, 18]
    assert result.dossier.metrics[0].name == "rmse"
    assert result.dossier.metrics[0].reported_value == 2.0
    assert [item.page for item in result.dossier.metrics[0].evidence] == [17, 18]
    assert result.warnings == ()
    assert result.rejected_citation_count == 0


def test_merge_preserves_conflicting_metric_values_and_marks_ambiguity() -> None:
    source_pages = _pages(
        (17, "RMSE was 1.8."),
        (18, "RMSE was 2.0."),
    )
    partials = (
        PartialDossier(
            metrics=(
                _metric(
                    "RMSE",
                    2.0,
                    18,
                    "RMSE was 2.0.",
                    dataset="Housing",
                    split="test",
                    model="random_forest",
                ),
            ),
        ),
        PartialDossier(
            metrics=(
                _metric(
                    "root mean squared error",
                    1.8,
                    17,
                    "RMSE was 1.8.",
                    dataset=" housing ",
                    split="TEST",
                    model="random_forest",
                ),
            ),
        ),
    )

    result = merge_partials(partials, source_pages)

    assert [item.reported_value for item in result.dossier.metrics] == [1.8, 2.0]
    assert result.warnings.count("ambiguous_metric") == 1


def test_merge_accepts_titles_only_from_pages_one_or_two() -> None:
    source_pages = _pages(
        (1, "Abstract."),
        (2, "Introduction."),
        (3, "A title reported too late."),
    )
    partials = (
        PartialDossier(
            title="A title reported too late",
            title_evidence=(_evidence(3, "A title reported too late."),),
        ),
    )

    result = merge_partials(partials, source_pages)

    assert result.dossier.title == "未提取到标题"
    assert "paper_title_not_extracted" in result.dossier.gaps


def test_merge_resolves_conflicting_titles_by_page_then_normalized_lexical_value() -> None:
    source_pages = _pages(
        (1, "Alpha title. Zebra title."),
        (2, "Beta title."),
    )
    partials = (
        PartialDossier(
            title="Beta title",
            title_evidence=(_evidence(2, "Beta title."),),
        ),
        PartialDossier(
            title="Zebra title",
            title_evidence=(_evidence(1, "Zebra title."),),
        ),
        PartialDossier(
            title="Alpha title",
            title_evidence=(_evidence(1, "Alpha title."),),
        ),
    )

    result = merge_partials(partials, source_pages)

    assert result.dossier.title == "Alpha title"
    assert result.warnings.count("ambiguous_title") == 1


def test_merge_requires_valid_regression_task_evidence() -> None:
    source_pages = _pages((1, "This study is a regression task."))
    explicit = PartialDossier(
        task_type="regression",
        task_evidence=(_evidence(1, "This study is a regression task."),),
    )
    uncertain = PartialDossier(task_type="uncertain")

    result = merge_partials((uncertain, explicit), source_pages)
    missing_evidence = merge_partials(
        (PartialDossier(task_type="regression"),), source_pages
    )
    uncertain_evidence = merge_partials(
        (
            PartialDossier(
                task_type="uncertain",
                task_evidence=(
                    _evidence(1, "This study is a regression task."),
                ),
            ),
        ),
        source_pages,
    )

    assert result.dossier.task_type == "regression"
    assert missing_evidence.dossier.task_type == "uncertain"
    assert uncertain_evidence.dossier.task_type == "uncertain"


def test_merge_uses_whitespace_normalized_exact_page_membership_and_preserves_excerpt() -> None:
    source_pages = _pages((17, "The RMSE   was 2.0 on the test split."))
    excerpt = " The RMSE was   2.0 on the test split. "
    partials = (
        PartialDossier(
            metrics=(_metric("RMSE", 2.0, 17, excerpt),),
        ),
    )

    result = merge_partials(partials, source_pages)

    assert result.dossier.metrics[0].evidence[0].source_text == excerpt
    assert result.rejected_citation_count == 0


def test_merge_rejects_wrong_page_quotations_and_removes_empty_facts() -> None:
    source_pages = _pages((17, "RMSE was 2.0."))
    partials = (
        PartialDossier(
            datasets=(_fact("Dataset", 18, "RMSE was 2.0."),),
            metrics=(_metric("RMSE", 2.0, 18, "RMSE was 2.0."),),
        ),
    )

    result = merge_partials(partials, source_pages)

    assert result.dossier.datasets == ()
    assert result.dossier.metrics == ()
    assert "citation_source_mismatch" in result.warnings
    assert result.rejected_citation_count == 2


def test_merge_serialization_is_canonical_and_repeatable() -> None:
    source_pages = _pages(
        (1, "A title. This is a regression task."),
        (17, "RMSE was 2.0."),
    )
    partials = (
        PartialDossier(
            title="A title",
            title_evidence=(_evidence(1, "A title."),),
            task_type="regression",
            task_evidence=(_evidence(1, "This is a regression task."),),
            metrics=(_metric("RMSE", 2.0, 17, "RMSE was 2.0."),),
        ),
    )

    result = merge_partials(partials, source_pages)
    repeated = merge_partials(partials, source_pages)

    assert result.canonical_json == repeated.canonical_json
    assert result.canonical_json == json.dumps(
        result.dossier.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
