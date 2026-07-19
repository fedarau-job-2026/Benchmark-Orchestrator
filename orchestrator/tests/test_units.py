"""Unit tests: grading, queue parsing/expansion, percentiles."""

import json

import pytest

from orchestrator.grading import is_correct
from orchestrator.jobs import expand_all, parse_queue
from orchestrator.metrics import percentile


# --- grading ---

@pytest.mark.parametrize(
    "expected,response,ok",
    [
        ("Paris", "The capital of France is Paris.", True),
        ("paris", "PARIS", True),
        ("96", "12 times 8 equals 96.", True),
        ("Paris", "London", False),
        (" Paris ", "paris", True),  # expected is stripped
    ],
)
def test_grading(expected, response, ok):
    assert is_correct(expected, response) is ok


# --- queue parsing & expansion ---

def write_fixtures(tmp_path, rows=3):
    csv_path = tmp_path / "bench.csv"
    lines = ["id,question,expected_answer"] + [
        f"{i},Q{i}?,A{i}" for i in range(1, rows + 1)
    ]
    csv_path.write_text("\n".join(lines) + "\n")
    queue_path = tmp_path / "queue.jsonl"
    queue_path.write_text(
        "\n".join(
            json.dumps({"benchmark_id": f"run_{n}", "csv_path": "bench.csv"})
            for n in (1, 2)
        )
        + "\n"
    )
    return queue_path


def test_parse_and_expand_relative_csv(tmp_path):
    queue_path = write_fixtures(tmp_path)
    specs = parse_queue(queue_path)
    assert [s.benchmark_id for s in specs] == ["run_1", "run_2"]
    assert all(s.type == "benchmark_csv" for s in specs)

    by_bench = expand_all(specs, queue_path.parent)
    assert set(by_bench) == {"run_1", "run_2"}
    tasks = by_bench["run_1"]
    assert len(tasks) == 3
    assert tasks[0].prompt == "Q1?" and tasks[0].expected == "A1"


def test_parse_queue_rejects_unknown_type(tmp_path):
    q = tmp_path / "queue.jsonl"
    q.write_text(json.dumps({"benchmark_id": "x", "type": "nope"}) + "\n")
    with pytest.raises(ValueError, match="unknown job type"):
        parse_queue(q)


def test_parse_queue_requires_benchmark_id(tmp_path):
    q = tmp_path / "queue.jsonl"
    q.write_text(json.dumps({"csv_path": "b.csv"}) + "\n")
    with pytest.raises(ValueError, match="missing benchmark_id"):
        parse_queue(q)


def test_parse_queue_skips_blank_lines(tmp_path):
    q = tmp_path / "queue.jsonl"
    q.write_text('{"benchmark_id": "a", "csv_path": "b.csv"}\n\n\n')
    assert len(parse_queue(q)) == 1


# --- percentiles ---

def test_percentile_empty():
    assert percentile([], 50) == 0.0


def test_percentile_basic():
    vals = sorted(float(v) for v in range(1, 101))  # 1..100
    assert percentile(vals, 50) == 50.0
    assert percentile(vals, 95) == 95.0
    assert percentile(vals, 0) == 1.0
    assert percentile(vals, 100) == 100.0
