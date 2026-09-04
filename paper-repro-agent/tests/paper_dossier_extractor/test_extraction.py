import json

import pytest

from paper_dossier_extractor.chunking import serialize_pages
from paper_dossier_extractor.config import Settings
from paper_dossier_extractor.extraction import extract_chunks, parse_partial
from paper_dossier_extractor.ollama import OllamaError
from paper_dossier_extractor.schemas import OllamaCompletion, PageChunk, SourcePage


class ScriptedClient:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[PageChunk] = []

    def complete(self, chunk: PageChunk) -> OllamaCompletion:
        self.calls.append(chunk)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        assert isinstance(response, OllamaCompletion)
        return response


class SequenceClock:
    def __init__(self, *values: float) -> None:
        self.values = list(values)
        self.last = values[-1] if values else 0.0

    def __call__(self) -> float:
        if self.values:
            self.last = self.values.pop(0)
        return self.last


def _settings(**overrides: object) -> Settings:
    return Settings(api_token="x" * 32, **overrides)


def _completion(
    text: str = '{"task_type":"uncertain"}',
    finish_reason: str = "stop",
) -> OllamaCompletion:
    return OllamaCompletion(
        text=text,
        finish_reason=finish_reason,
        prompt_tokens=0,
        completion_tokens=0,
    )


def _chunk(chunk_id: str = "chunk-001", page_count: int = 4) -> PageChunk:
    pages = tuple(
        SourcePage(page=page, text=f"synthetic page {page}")
        for page in range(1, page_count + 1)
    )
    source_bytes = len(serialize_pages(pages).encode("utf-8"))
    return PageChunk(chunk_id=chunk_id, pages=pages, source_bytes=source_bytes)


def test_extract_chunks_bisects_truncated_chunk_depth_first() -> None:
    client = ScriptedClient(
        _completion(finish_reason="length"),
        _completion(),
        _completion(),
    )

    result = extract_chunks((_chunk(),), client, _settings())

    assert [item.chunk_id for item in result.successes] == [
        "chunk-001.1",
        "chunk-001.2",
    ]
    assert result.call_count == 3
    assert result.split_retry_count == 1
    assert result.failures == ()
    assert [
        (item.chunk_id, tuple(page.page for page in item.pages))
        for item in client.calls
    ] == [
        ("chunk-001", (1, 2, 3, 4)),
        ("chunk-001.1", (1, 2)),
        ("chunk-001.2", (3, 4)),
    ]
    assert client.calls[1].source_bytes == len(
        serialize_pages(client.calls[1].pages).encode("utf-8")
    )


def test_extract_chunks_keeps_bisection_depth_first_and_deterministic() -> None:
    client = ScriptedClient(
        _completion(finish_reason="length"),
        _completion(finish_reason="length"),
        _completion(),
        _completion(),
        _completion(),
    )

    result = extract_chunks((_chunk(),), client, _settings())

    assert [item.chunk_id for item in result.successes] == [
        "chunk-001.1.1",
        "chunk-001.1.2",
        "chunk-001.2",
    ]
    assert [item.chunk_id for item in result.failures] == []
    assert result.call_count == 5
    assert result.split_retry_count == 2
    assert [item.chunk_id for item in client.calls] == [
        "chunk-001",
        "chunk-001.1",
        "chunk-001.1.1",
        "chunk-001.1.2",
        "chunk-001.2",
    ]


def test_parse_partial_accepts_only_complete_json_schema_values() -> None:
    parsed = parse_partial(_completion())

    assert parsed.task_type == "uncertain"


@pytest.mark.parametrize(
    "completion",
    [
        _completion(text="not json"),
        _completion(text="```json\n{\"task_type\":\"uncertain\"}\n```") ,
        _completion(text="prefix {\"task_type\":\"uncertain\"}"),
        _completion(text=""),
        _completion(text=json.dumps({"task_type": "classification"})),
    ],
)
def test_extract_chunks_reports_strict_partial_parse_failures(
    completion: OllamaCompletion,
) -> None:
    client = ScriptedClient(completion)

    result = extract_chunks((_chunk(page_count=1),), client, _settings())

    assert result.successes == ()
    assert result.call_count == 1
    assert len(result.failures) == 1
    assert result.failures[0].chunk_id == "chunk-001"
    assert result.failures[0].code in {
        "qwen_chunk_empty",
        "qwen_chunk_invalid",
    }


def test_extract_chunks_does_not_split_a_one_page_terminal_failure() -> None:
    client = ScriptedClient(_completion(finish_reason="length"))

    result = extract_chunks((_chunk(page_count=1),), client, _settings())

    assert result.call_count == 1
    assert result.split_retry_count == 0
    assert result.successes == ()
    assert result.failures[0].code == "qwen_chunk_truncated"
    assert result.failures[0].page_range == (1, 1)


def test_extract_chunks_maps_transport_timeout_to_safe_chunk_code() -> None:
    client = ScriptedClient(OllamaError("ollama_timeout"))

    result = extract_chunks((_chunk(page_count=1),), client, _settings())

    assert result.call_count == 1
    assert result.failures[0].code == "qwen_chunk_timeout"
    assert "ollama_timeout" not in repr(result.failures[0])


def test_extract_chunks_rejects_the_thirteenth_call() -> None:
    chunks = tuple(
        _chunk(f"chunk-{index:03d}", page_count=1) for index in range(1, 14)
    )
    client = ScriptedClient(*(_completion() for _ in range(13)))

    result = extract_chunks(chunks, client, _settings())

    assert result.call_count == 12
    assert len(client.calls) == 12
    assert len(result.successes) == 12
    assert result.failures[-1].chunk_id == "chunk-013"
    assert result.failures[-1].code == "qwen_extraction_budget_exceeded"


def test_extract_chunks_rejects_a_call_at_the_exact_deadline() -> None:
    client = ScriptedClient(_completion())
    clock = SequenceClock(0.0, 1_200.0)

    result = extract_chunks((_chunk(page_count=1),), client, _settings(), clock)

    assert result.call_count == 0
    assert client.calls == []
    assert result.successes == ()
    assert result.failures[0].code == "qwen_extraction_budget_exceeded"
    assert result.failures[0].page_range == (1, 1)
