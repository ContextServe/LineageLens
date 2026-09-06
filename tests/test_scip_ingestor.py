"""Unit tests for SCIPProtobufIngestor and parse_scip_symbol_uri."""

from pathlib import Path

from lineagelens.scip_ingestor import SCIPProtobufIngestor, parse_scip_symbol_uri


def test_parse_scip_symbol_uri():
    uri = "scip-python python package 1.0 app/utils.py/fetch_data()."
    parsed = parse_scip_symbol_uri(uri)
    assert parsed["name"] == "fetch_data"
    assert parsed["package"] == "package"

    simple_uri = "com/example/MyClass#myMethod()."
    parsed_simple = parse_scip_symbol_uri(simple_uri)
    assert parsed_simple["name"] == "myMethod"


def test_scip_ingestor_missing_file(tmp_path: Path):
    ingestor = SCIPProtobufIngestor(tmp_path)
    graph = ingestor.ingest(tmp_path / "index.scip")
    assert len(graph.symbols) == 0
    assert len(graph.relations) == 0
