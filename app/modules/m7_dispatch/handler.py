from fastapi import APIRouter

from app.errors import LedgerError
from app.modules.m7_dispatch.attachment import evidence_file
from app.modules.m7_dispatch.revision import (
    CHANGE_LABELS, SUPPLEMENT_KINDS, Supplement, build_revision, revision_summaries,
)
from app.modules.m7_dispatch.service import dispatch
from app.source import Selection, resolve
from app.store import get_run, get_run_row, sign_run

router = APIRouter(prefix="/api/runs")


@router.post("")
def run(selection: Selection):
    data, sample = resolve(selection)
    return dispatch(data, sample)


@router.get("/{run_id}")
def run_detail(run_id: str):
    document, _ = get_run(run_id)
    return document


@router.post("/{run_id}/rerun")
def rerun(run_id: str):
    document, source = get_run(run_id)
    if document.get("kind") == "revision":
        stored = Supplement(receipts=document["supplement"]["receipts"], note=document["supplement"].get("note"))
        return build_revision(document["baseline_id"], stored, document["manifest"]["sample"])
    return dispatch(source, document["manifest"]["sample"])


@router.post("/{run_id}/sign")
def sign(run_id: str, body: dict | None = None):
    row = get_run_row(run_id)
    if row["kind"] == "revision":
        raise LedgerError("SIGN_BASELINE_ONLY", "签收结论只落于原始勾稽版本", 409)
    payload = body or {}
    return sign_run(run_id, payload.get("signer"), payload.get("note"))


@router.get("/{run_id}/signature")
def signature(run_id: str):
    row = get_run_row(run_id)
    baseline = row["baseline_id"] if row["kind"] == "revision" else row["id"]
    return revision_summaries(baseline)


@router.post("/{run_id}/revisions")
def revision(run_id: str, supplement: Supplement):
    """在已签收基线上登记迟到原件或勘误，形成可追溯修订版。"""
    return build_revision(run_id, supplement)


@router.get("/{run_id}/revisions")
def revisions(run_id: str):
    return revision_summaries(run_id)


@router.get("/{run_id}/export")
def export(run_id: str):
    document, source = get_run(run_id)
    document = {**document, "change_labels": CHANGE_LABELS, "supplement_kinds": SUPPLEMENT_KINDS}
    return evidence_file(document, source)
