from app.config import MAX_PACKETS, MAX_ROWS, PROJECT, RULE_VERSION, TERMINALS
from app.errors import BusinessError
from app.manifest import digest
from app.quality import integer, token
from app.reasons import LABELS
from app.modules.m8_shift.merge import ledger_view


def shift_id(raw):
    try:
        return token(raw)
    except ValueError as error:
        raise BusinessError("BAD_SHIFT", "班次标识须为 1 至 160 字") from error


def view(ledger, shift):
    return ledger_view(shift, ledger.shift_records(shift), TERMINALS)


def checked_record(raw):
    return {
        "batch": token(raw["batch"]),
        "pieces": integer(raw["pieces"], 0, 1000000),
        "soak_s": integer(raw["soak_s"], 0, 86400),
    }


def accept(ledger, shift, body):
    packets = body if isinstance(body, list) else [body]
    if (
        not packets
        or len(packets) > MAX_PACKETS
        or any(not isinstance(packet, dict) for packet in packets)
    ):
        raise BusinessError("BAD_PACKET", f"缓存包须为 1 至 {MAX_PACKETS} 个对象")
    stored = duplicates = 0
    rejected = []
    for packet in packets:
        try:
            terminal = token(packet.get("terminal"))
        except (TypeError, ValueError) as error:
            raise BusinessError("BAD_PACKET", "缓存包须带终端标识") from error
        if terminal not in TERMINALS:
            raise BusinessError("UNKNOWN_TERMINAL", "未知采集终端，须为在册三台之一")
        records = packet.get("records")
        if not isinstance(records, list) or len(records) > MAX_ROWS:
            raise BusinessError("BAD_PACKET", f"records 须为不超过 {MAX_ROWS} 项的列表")
        for row, raw in enumerate(records, 1):
            try:
                record = dict(checked_record(raw), terminal=terminal)
            except (KeyError, TypeError, ValueError):
                rejected.append(
                    {
                        "terminal": terminal,
                        "row": row,
                        "code": "BAD_RECORD",
                        "message": LABELS["BAD_RECORD"],
                    }
                )
                continue
            if ledger.put_shift_record(shift, digest(record), record):
                duplicates += 1
            else:
                stored += 1
    return {
        "shift": shift,
        "stored": stored,
        "duplicates": duplicates,
        "rejected": rejected,
        "reused": stored == 0,
        "ledger": view(ledger, shift),
    }


def handover(ledger, shift, operator):
    try:
        operator = token(operator)
        if len(operator) > 32:
            raise ValueError
    except ValueError as error:
        raise BusinessError("OPERATOR_REQUIRED", "请输入 1 至 32 字的签核人") from error
    snapshot = view(ledger, shift)
    proof = {
        "project": PROJECT,
        "shift": shift,
        "rules": RULE_VERSION,
        "operator": operator,
        "decision": snapshot["decision"],
        "ledger_sha256": digest(snapshot),
    }
    checksum = digest(proof)
    record = {
        "id": "handover-" + checksum,
        "shift": shift,
        "operator": operator,
        "decision": snapshot["decision"],
        "ruling": snapshot["ruling"],
        "manifest": dict(proof, digest=checksum),
        "snapshot": snapshot,
    }
    return dict(record, reused=ledger.put_handover(record))
