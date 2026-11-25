from backend.knowledge.machine_model import MachineEntity, MachineRelation, MachinePort, UniversalMachineModel
from backend.knowledge.verification import verify_machine_model
from backend.retrieval.knowledge import retrieve_machine_knowledge

def test_topology_verification_catches_missing_reference():
    m=UniversalMachineModel(entities=[MachineEntity(id="a", name="A", entity_type="pump")], relations=[MachineRelation(subject_id="a",subject_name="A",relation_type="connected_to",object_id="missing",object_name="X")])
    issues=verify_machine_model(m)
    assert any(i.category=="topology" and i.severity=="error" for i in issues)

def test_spatial_verification_catches_bad_bbox():
    m=UniversalMachineModel(entities=[MachineEntity(id="a",name="A",entity_type="x",properties={"bbox":{"x":0.9,"y":0,"w":0.3,"h":0.2}})])
    issues=verify_machine_model(m)
    assert any(i.category=="spatial" and i.severity=="error" for i in issues)

def test_retrieval_is_hard_revision_scoped():
    """Phase 3 extraction: the canonical model comes from ingestion's
    HTTP API, never the snapshot table. The product/revision scoping
    is unchanged: a product mismatch returns no hits, and a revision
    with no model returns no hits."""
    import httpx

    from backend.retrieval.ingestion_client import IngestionClient

    model = {"entities": [{"id": "entity:p1", "name": "Pump P1",
                           "entity_type": "pump"}]}
    product_id = "prod-1"
    revision_id = "rev-1"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/v1/products/{product_id}/revisions":
            return httpx.Response(200, json=[
                {"id": revision_id, "product_id": product_id, "label": "Rev A"}])
        if path == f"/v1/knowledge/revisions/{revision_id}/model":
            return httpx.Response(200, json={
                "revision_id": revision_id, "version": 1, "model": model,
                "source_counts": {}})
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    client = IngestionClient(base_url="http://ingestion-test",
                             transport=httpx.MockTransport(handler))
    hits = retrieve_machine_knowledge(
        "pump", product_id=product_id, revision_id=revision_id, client=client)
    assert hits and hits[0].revision_id == revision_id
    assert retrieve_machine_knowledge(
        "pump", product_id="wrong", revision_id=revision_id,
        client=client) == []
    # A revision with no integrated model returns no hits.
    assert retrieve_machine_knowledge(
        "pump", product_id=product_id, revision_id="rev-missing",
        client=client) == []
