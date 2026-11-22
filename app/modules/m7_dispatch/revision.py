"""签收基线与补录修订。

签收结果作为交班依据原样封存；供应商补录（迟到原件与勘误）不改写签收版本，
而是在签收基线之外登记一份可追溯的修订版。修订只追加来刊行，不删除签收行：
迟到原件进入当天缺刊判断，对签收原件的勘误与签收原件并列留档，互相可追。
"""
import hashlib
from threading import Lock
from time import perf_counter

from pydantic import BaseModel, ConfigDict

from app.config import MAX_RECORDS
from app.errors import LedgerError
from app.manifest import canonical, make_manifest
from app.modules.m2_receipts.models import Receipt
from app.pipeline import calculate
from app.store import archive, get_run, get_run_row, get_signature, list_revisions

CHANGE_LABELS = {
    "LATE_FILL": "迟到补刊补齐",
    "CORRECTION_FILL": "勘误补入期位",
    "LATE_DISPUTE": "迟到原件引发争议",
    "CORRECTION_DISPUTE": "勘误引发争议",
    "RESOLVED": "争议已澄清",
    "DISPUTE_CHANGED": "争议依据变更",
    "EVIDENCE_CHANGED": "到藏依据变更",
    "LOST_BASIS": "签收依据缺失",
}

SUPPLEMENT_KINDS = {
    "LATE": "迟到原件",
    "CORRECTION": "签收原件勘误",
    "REPLAY": "补录重放",
    "ISOLATED": "补录隔离",
}


class Supplement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipts: list = []
    note: str | None = None


# 同一基线的补录串行登记，避免两批补录各自基于同一版头而互相覆盖来刊行。
_locks = {}
_locks_guard = Lock()


def _baseline_lock(baseline_id):
    with _locks_guard:
        return _locks.setdefault(baseline_id, Lock())


def _signature_of(row):
    return canonical({field: row[field] for field in Receipt.model_fields})


def baseline_signatures(source):
    """签收清册中每个验收号已登记的来刊描述，用于分辨迟到与勘误。"""
    known = {}
    for raw in source["receipts"]:
        if not isinstance(raw, dict):
            continue
        try:
            row = Receipt.model_validate(raw).model_dump()
        except Exception:
            continue
        known.setdefault(row["accession"], set()).add(_signature_of(row))
    return known


def _supplement_audit(intake, head_len, known):
    """补录行逐条归因；隔离行也保留，不因未对平而丢失。"""
    rows = []
    for bucket, outcome in (("accepted", "ACCEPTED"), ("conflicts", "ACCESSION_CONFLICT"),
                            ("duplicates", "RECEIPT_REPLAY"), ("isolated", "ISOLATED")):
        for row in intake[bucket]:
            if row["line"] <= head_len:
                continue
            accession = row.get("accession")
            if bucket == "isolated":
                kind = "ISOLATED"
            elif accession in known:
                kind = "REPLAY" if _signature_of(row) in known[accession] else "CORRECTION"
            else:
                kind = "LATE"
            entry = {"supplement_line": row["line"] - head_len, "line": row["line"],
                     "accession": accession, "kind": kind, "kind_label": SUPPLEMENT_KINDS[kind],
                     "outcome": outcome, "code": row.get("code")}
            if row.get("original_line"):
                entry["original_line"] = row["original_line"]
            if row.get("raw") is not None:
                entry["raw"] = row["raw"]
            rows.append(entry)
    rows.sort(key=lambda item: item["supplement_line"])
    counts = {}
    for row in rows:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    return {"rows": rows, "counts": counts}


def _annotate(evidence, baseline_len, head_len):
    """按来源标注原件：签收行、先前修订行、本次补录行。"""
    items = []
    for item in evidence:
        if item["line"] <= baseline_len:
            origin = "baseline"
        elif item["line"] <= head_len:
            origin = "prior"
        else:
            origin = "supplement"
        entry = {**item, "origin": origin}
        if origin == "supplement":
            entry["supplement_line"] = item["line"] - head_len
        items.append(entry)
    return items


def _evidence_key(evidence):
    return tuple((item["line"], item["accession"]) for item in evidence)


def _slot_key(slot):
    return slot["status"], slot["code"], _evidence_key(slot["evidence"])


def _classify(before, after, kinds):
    old, new = before["status"], after["status"]
    if old != "missing" and new == "missing":
        return "LOST_BASIS"
    if new == "disputed" and old != "disputed":
        return "CORRECTION_DISPUTE" if "CORRECTION" in kinds else "LATE_DISPUTE"
    if old == "missing" and new == "covered":
        return "CORRECTION_FILL" if "CORRECTION" in kinds else "LATE_FILL"
    if old == "disputed" and new == "covered":
        return "RESOLVED"
    if old == "disputed" and new == "disputed":
        return "DISPUTE_CHANGED"
    return "EVIDENCE_CHANGED"


def _kinds_for(slot_evidence, audit_rows, reason_lines):
    """期位变化的补录归因：直接补入的行，或与签收行同验收号的勘误。"""
    accessions = {item["accession"] for item in slot_evidence}
    return {row["kind"] for row in audit_rows
            if row["line"] in reason_lines and row["accession"] in accessions}


def compare_slots(base_balance, head_balance, baseline_len, head_len, audit_rows, reason_lines):
    """对照基线/上一版与当前版；reason_lines 限定可归因于哪批补录。"""
    before = {(s["chain"], s["year"], s["issue"]): s for s in base_balance["slots"]}
    changes, unchanged = [], 0
    for slot in head_balance["slots"]:
        previous = before.get((slot["chain"], slot["year"], slot["issue"]))
        if previous is None:
            continue
        if _slot_key(previous) == _slot_key(slot):
            unchanged += 1
            continue
        annotated = _annotate(slot["evidence"], baseline_len, head_len)
        kinds = _kinds_for(slot["evidence"], audit_rows, reason_lines)
        changes.append({
            "chain": slot["chain"], "year": slot["year"], "volume": slot["volume"], "issue": slot["issue"],
            "reason": _classify(previous, slot, kinds),
            "before": {"status": previous["status"], "code": previous["code"],
                       "evidence": _annotate(previous["evidence"], baseline_len, head_len)},
            "after": {"status": slot["status"], "code": slot["code"], "evidence": annotated},
        })
    by_reason = {}
    for change in changes:
        by_reason[change["reason"]] = by_reason.get(change["reason"], 0) + 1
    return {"changes": changes, "unchanged": unchanged,
            "counts": {"changed": len(changes), "by_reason": by_reason}}


def _validate_supplement(supplement):
    if not supplement.receipts:
        raise LedgerError("SUPPLEMENT_EMPTY", "补录清册不能为空")
    if len(supplement.receipts) > MAX_RECORDS:
        raise LedgerError("TOO_MANY_ROWS", f"补录最多 {MAX_RECORDS} 行", 413)
    try:
        canonical(supplement.receipts)
    except ValueError as error:
        raise LedgerError("INVALID_JSON", "补录数字须为有限值") from error


def _signature_change(before, after):
    """签收依据（交班结论）与修订结论的对照。"""
    return {
        "expected": after["totals"]["expected"],
        "baseline": {key: before["totals"][key] for key in ("covered", "missing", "disputed")},
        "current": {key: after["totals"][key] for key in ("covered", "missing", "disputed")},
        "status": {"baseline": before["status"], "current": after["status"]},
        "row_totals": {"baseline": before["row_totals"], "current": after["row_totals"]},
    }


def resolve_baseline(target_id):
    row = get_run_row(target_id)
    root_id = row["baseline_id"] if row["kind"] == "revision" else target_id
    return get_run_row(root_id)


def resolve_head(baseline_row):
    """现行累计版：基线本身，或最新一份在他之上的修订。"""
    documents = list_revisions(baseline_row["id"])
    if not documents:
        return baseline_row
    latest = documents[-1]
    return {"id": latest["id"], "kind": "revision", "baseline_id": baseline_row["id"],
            "parent_id": latest.get("parent_id"), "document": latest}


def build_revision(target_id, supplement, sample=None):
    """在签收基线上登记一份补录，返回幂等的可追溯修订版。"""
    baseline_row = resolve_baseline(target_id)
    _validate_supplement(supplement)
    if get_signature(baseline_row["id"]) is None:
        raise LedgerError("RUN_NOT_SIGNED", "签收确认后方可登记补录", 409)
    with _baseline_lock(baseline_row["id"]):
        return _register(baseline_row, supplement, sample)


def _register(baseline_row, supplement, sample):
    head_row = resolve_head(baseline_row)
    supplement_sha = hashlib.sha256(canonical(supplement.receipts).encode()).hexdigest()

    # 同一补录对基线只登记一次；重跑命中已有修订，不新增覆盖也不重复追加来刊行。
    for document in list_revisions(baseline_row["id"]):
        if document["supplement"]["sha256"] == supplement_sha:
            return {**document, "reused": True}

    head_document, head_source = get_run(head_row["id"])
    head_receipts = list(head_source["receipts"])
    merged = {**head_source, "receipts": [*head_receipts, *supplement.receipts]}

    started = perf_counter()
    stages = calculate(merged)
    manifest = make_manifest({"receipts": supplement.receipts},
                             sample or baseline_row["document"]["manifest"].get("sample"),
                             round((perf_counter() - started) * 1000, 2),
                             records=len(supplement.receipts),
                             prefix=f"revision:{baseline_row['id']}")
    head_len = len(head_receipts)
    baseline_len = len(get_run(baseline_row["id"])[1]["receipts"])
    audit = _supplement_audit(stages["receipts"], head_len, baseline_signatures(head_source))
    current_lines = {row["line"] for row in audit["rows"]}
    delta = compare_slots(head_document["result"]["balance"], stages["balance"],
                          baseline_len, head_len, audit["rows"], current_lines)
    baseline_balance = baseline_row["document"]["result"]["balance"]
    prior_lines = {row["line"] for document in list_revisions(baseline_row["id"])
                   for row in document["supplement_audit"]["rows"]}
    baseline_delta = compare_slots(baseline_balance, stages["balance"], baseline_len, head_len,
                                   audit["rows"], prior_lines | current_lines)
    delta.update({"baseline_id": baseline_row["id"], "parent_id": head_row["id"],
                  "revision_id": manifest["digest"]})
    baseline_delta.update({"baseline_id": baseline_row["id"], "revision_id": manifest["digest"]})
    document = {
        "id": manifest["digest"], "kind": "revision",
        "baseline_id": baseline_row["id"], "parent_id": head_row["id"],
        "baseline_manifest": baseline_row["document"]["manifest"],
        "baseline_signature": get_signature(baseline_row["id"]),
        "supplement": {"note": supplement.note, "receipts": supplement.receipts, "sha256": supplement_sha},
        "manifest": manifest,
        "result": {"balance": stages["balance"], "supplements": stages["combined"]["supplements"],
                   "duplicates": stages["receipts"]["duplicates"], "titles": stages["titles"]["periods"]},
        "signature_change": _signature_change(baseline_balance, stages["balance"]),
        "delta": delta, "baseline_delta": baseline_delta, "supplement_audit": audit,
    }
    return archive(document, merged, kind="revision", baseline_id=baseline_row["id"], parent_id=head_row["id"])


def revision_summaries(baseline_id):
    baseline_row = resolve_baseline(baseline_id)
    documents = list_revisions(baseline_row["id"])
    summaries = [{"id": document["id"], "generated_at": document["manifest"]["generated_at"],
                  "note": document["supplement"].get("note"),
                  "delta": document["delta"]["counts"],
                  "baseline_delta": document["baseline_delta"]["counts"],
                  "reconciled": document["result"]["balance"]["status"] == "MATCHED",
                  "supplement": document["supplement_audit"]["counts"]} for document in documents]
    latest = documents[-1] if documents else None
    return {"baseline_id": baseline_row["id"], "signature": get_signature(baseline_row["id"]),
            "baseline_status": baseline_row["document"]["result"]["balance"]["status"],
            "revisions": summaries, "current_id": summaries[-1]["id"] if summaries else baseline_row["id"],
            "current_status": (latest["result"]["balance"]["status"] if latest
                               else baseline_row["document"]["result"]["balance"]["status"])}
