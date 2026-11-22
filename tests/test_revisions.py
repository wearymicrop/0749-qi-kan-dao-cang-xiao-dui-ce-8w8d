import pytest

from app.modules.m7_dispatch.revision import Supplement, build_revision
from app.modules.m7_dispatch.service import dispatch
from app.store import get_run, get_signature, sign_run
from app.source import load_sample


def _signed(client, sample="dirty", signer="交班员"):
    run = client.post("/api/runs", json={"sample": sample}).json()
    client.post(f"/api/runs/{run['id']}/sign", json={"signer": signer, "note": "交班"})
    return run


LATE = {"accession": "00099", "title": "海岸观测通报", "year": 2025, "volume": "17", "issue": "3", "kind": "regular"}


def test_supplement_requires_signature(client):
    run = client.post("/api/runs", json={"sample": "dirty"}).json()
    response = client.post(f"/api/runs/{run['id']}/revisions", json={"receipts": [LATE]})
    assert response.status_code == 409 and response.json()["code"] == "RUN_NOT_SIGNED"


def test_supplement_must_not_be_empty(client):
    run = _signed(client)
    response = client.post(f"/api/runs/{run['id']}/revisions", json={"receipts": []})
    assert response.status_code == 422 and response.json()["code"] == "SUPPLEMENT_EMPTY"


def test_revision_keeps_signed_baseline_untouched(client):
    run = _signed(client)
    revision = client.post(f"/api/runs/{run['id']}/revisions", json={"receipts": [LATE], "note": "迟到"}).json()

    baseline = client.get(f"/api/runs/{run['id']}").json()
    assert baseline["result"]["balance"]["totals"] == run["result"]["balance"]["totals"]
    assert baseline["manifest"] == run["manifest"]
    assert get_signature(run["id"])["signed_at"] == client.get(f"/api/runs/{run['id']}/signature").json()["signature"]["signed_at"]

    assert revision["id"] != run["id"] and revision["baseline_id"] == run["id"]
    assert revision["kind"] == "revision" and revision["baseline_manifest"]["digest"] == run["id"]


def test_late_issue_enters_current_judgement_only(client):
    run = _signed(client)
    slot = ("CST", 2025, 3)
    before = next(row for row in run["result"]["balance"]["slots"] if (row["chain"], row["year"], row["issue"]) == slot)
    assert before["status"] == "missing"

    revision = client.post(f"/api/runs/{run['id']}/revisions", json={"receipts": [LATE]}).json()
    after = next(row for row in revision["result"]["balance"]["slots"]
                 if (row["chain"], row["year"], row["issue"]) == slot)
    assert after["status"] == "covered"
    assert after["evidence"][0]["accession"] == "00099"

    change = next(item for item in revision["delta"]["changes"] if item["issue"] == 3)
    assert (change["reason"], change["before"]["status"], change["after"]["status"]) == ("LATE_FILL", "missing", "covered")
    assert change["after"]["evidence"][0]["origin"] == "supplement"
    assert change["after"]["evidence"][0]["supplement_line"] == 1
    assert revision["signature_change"]["baseline"]["missing"] == 4
    assert revision["signature_change"]["current"]["missing"] == 3


CORRECTION = {"accession": "00011", "title": "海岸观测通报", "year": 2025, "volume": "17", "issue": "3", "kind": "regular"}


def test_correction_keeps_both_originals_and_disputes_slot(client):
    run = _signed(client)
    revision = client.post(f"/api/runs/{run['id']}/revisions", json={"receipts": [CORRECTION]}).json()

    audit = revision["supplement_audit"]["rows"][0]
    assert (audit["kind"], audit["outcome"]) == ("CORRECTION", "ACCESSION_CONFLICT")
    shared = next(item for item in revision["delta"]["changes"] if item["issue"] == 2)
    assert shared["reason"] == "DISPUTE_CHANGED"
    assert shared["before"]["code"] == "CLAIM_OVERLAP" and shared["after"]["code"] == "ACCESSION_CONFLICT"
    assert [(item["line"], item["origin"]) for item in shared["after"]["evidence"]] == [(2, "baseline"), (3, "baseline")]
    change = next(item for item in revision["delta"]["changes"] if item["issue"] == 3)
    assert change["reason"] == "CORRECTION_DISPUTE"
    assert change["before"]["status"] == "missing"
    assert [(item["line"], item["accession"], item["origin"])
            for item in change["after"]["evidence"]] == [(23, "00011", "supplement")]
    # 签收原件仍留在基线版；勘误件在修订版里另立补录行
    baseline, source = get_run(run["id"])
    assert any(row["accession"] == "00011" and row["issue"] == "2" for row in source["receipts"])


def test_same_supplement_rerun_reuses_revision_without_new_coverage(client):
    run = _signed(client)
    first = client.post(f"/api/runs/{run['id']}/revisions", json={"receipts": [LATE], "note": "A"}).json()
    again = client.post(f"/api/runs/{run['id']}/revisions", json={"receipts": [LATE], "note": "B"}).json()
    assert again["id"] == first["id"] and again["reused"] is True
    assert again["result"]["balance"]["totals"] == first["result"]["balance"]["totals"]

    listing = client.get(f"/api/runs/{run['id']}/revisions").json()
    assert len(listing["revisions"]) == 1
    assert listing["current_id"] == first["id"]


def test_unmatched_supplement_rows_are_retained_not_dropped(client):
    run = _signed(client)
    rows = [None, {"accession": "00090"}, {"accession": "00100", "title": "无名刊", "year": 2026,
                                            "volume": "18", "issue": "2", "kind": "regular"}]
    revision = client.post(f"/api/runs/{run['id']}/revisions", json={"receipts": rows}).json()
    audit = revision["supplement_audit"]["rows"]
    assert [row["kind"] for row in audit] == ["ISOLATED", "ISOLATED", "LATE"]
    assert [row["code"] for row in audit] == ["EMPTY_ROW", "RECEIPT_SCHEMA", None]
    balance = revision["result"]["balance"]
    assert balance["row_totals"]["difference"] == 0
    assert any(item["line"] > 22 and item["code"] == "UNKNOWN_TITLE" for item in balance["quarantine"])


def test_second_supplement_builds_on_head_and_reports_baseline_delta(client):
    run = _signed(client)
    first = client.post(f"/api/runs/{run['id']}/revisions", json={"receipts": [LATE], "note": "迟到"}).json()
    second_rows = [{"accession": "00098", "title": "方志资料辑刊", "year": 2026,
                    "volume": "4", "issue": "12", "kind": "regular"}]
    second = client.post(f"/api/runs/{first['id']}/revisions", json={"receipts": second_rows, "note": "第二批"}).json()
    assert second["parent_id"] == first["id"] and second["baseline_id"] == run["id"]
    assert second["delta"]["counts"]["changed"] == 1
    assert second["baseline_delta"]["counts"]["changed"] == 2
    # 第二版里，第一批补录行标为 prior，只有第二批标为 supplement
    first_round = next(c for c in second["baseline_delta"]["changes"] if c["issue"] == 3)
    assert [(item["line"], item["origin"]) for item in first_round["after"]["evidence"]] == [(23, "prior")]
    assert all(item["origin"] == "supplement" for c in second["delta"]["changes"]
               for item in c["after"]["evidence"])
    listing = client.get(f"/api/runs/{run['id']}/revisions").json()
    assert [item["id"] for item in listing["revisions"]] == [first["id"], second["id"]]
    assert listing["current_status"] == second["result"]["balance"]["status"]


def test_concurrent_distinct_supplements_both_reach_current_head(client):
    from concurrent.futures import ThreadPoolExecutor

    run = _signed(client)
    first_row = LATE
    second_row = {"accession": "00098", "title": "方志资料辑刊", "year": 2026,
                  "volume": "4", "issue": "12", "kind": "regular"}
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(lambda rows: client.post(f"/api/runs/{run['id']}/revisions",
                                                           json={"receipts": [rows]}), row)
                   for row in (first_row, second_row)]
        [future.result() for future in futures]
    listing = client.get(f"/api/runs/{run['id']}/revisions").json()
    assert len(listing["revisions"]) == 2
    current = client.get(f"/api/runs/{listing['current_id']}").json()
    accessions = {row["accession"] for row in current_supplement_source(client, listing["current_id"])
                  if isinstance(row, dict)}
    assert {"00099", "00098"} <= accessions
    assert current["result"]["balance"]["totals"]["covered"] == 9
    assert current["result"]["balance"]["row_totals"]["difference"] == 0


def current_supplement_source(client, revision_id):
    return client.get(f"/api/runs/{revision_id}/export").json()["source"]["receipts"]


def test_revision_export_carries_baseline_and_supplement(client):
    run = _signed(client)
    revision = client.post(f"/api/runs/{run['id']}/revisions", json={"receipts": [LATE], "note": "迟到"}).json()
    evidence = client.get(f"/api/runs/{revision['id']}/export").json()
    assert evidence["baseline_manifest"]["digest"] == run["id"]
    assert evidence["baseline_signature"]["signer"] == "交班员"
    assert evidence["supplement"]["receipts"] == [LATE]
    assert evidence["source"]["receipts"][-1] == LATE
    assert evidence["change_labels"]["LATE_FILL"] == "迟到补刊补齐"


def test_signing_a_revision_is_rejected(client):
    run = _signed(client)
    revision = client.post(f"/api/runs/{run['id']}/revisions", json={"receipts": [LATE]}).json()
    response = client.post(f"/api/runs/{revision['id']}/sign", json={})
    assert response.status_code == 409 and response.json()["code"] == "SIGN_BASELINE_ONLY"


def test_baseline_is_identical_when_supplement_replayed_by_service(client, dirty):
    run = client.post("/api/runs", json={"sample": "dirty"}).json()
    sign_run(run["id"], "甲", "交班")
    first = build_revision(run["id"], Supplement(receipts=[LATE]))
    second = build_revision(run["id"], Supplement(receipts=[LATE]))
    assert second["id"] == first["id"] and second["reused"] is True
    baseline = dispatch(dirty, "dirty")
    assert baseline["id"] == run["id"]
    assert baseline["result"]["balance"]["totals"]["covered"] == 7
