from app.routers.resources import pipeline_accesses, pipeline_requires_diagnostics


def test_aggregation_permissions_include_foreign_reads_and_output_writes() -> None:
    reads, writes = pipeline_accesses(
        [
            {
                "$lookup": {
                    "from": "customers",
                    "pipeline": [{"$unionWith": {"coll": "archived_customers"}}],
                }
            },
            {"$out": {"db": "archive", "coll": "orders"}},
        ],
        "sales",
    )

    assert reads == {("sales", "customers"), ("sales", "archived_customers")}
    assert writes == {("archive", "orders")}


def test_diagnostic_aggregation_stage_is_detected_in_nested_pipeline() -> None:
    assert pipeline_requires_diagnostics(
        [{"$facet": {"per_index": [{"$indexStats": {}}]}}]
    )
    assert not pipeline_requires_diagnostics([{"$match": {"status": "paid"}}])
