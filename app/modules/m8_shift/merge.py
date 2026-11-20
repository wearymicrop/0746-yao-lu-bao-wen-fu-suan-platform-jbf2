from collections import defaultdict

from app.reasons import LABELS


def ruling(decision, arrived_count, expected_count, quarantined_count):
    if decision == "FULL":
        return "三台缓存到齐且无异值炉次：按全部终端快照出账，整账交班。"
    parts = [f"按时交班：已到 {arrived_count} 台快照入账"]
    missing = expected_count - arrived_count
    if missing:
        parts.append(f"{missing} 台缺口挂账待补")
    if quarantined_count:
        parts.append(f"{quarantined_count} 个炉次隔离待核")
    return "，".join(parts) + "；恢复后按同一口径补登，不重复、不丢失。"


def ledger_view(shift, records, expected):
    """把班次内全部缓存记录并成总账视图。

    去重口径（产量与保温时长共用同一采纳集）：
    记录级按内容摘要判重，重试重传不新增；
    同炉次多台同值只计一次并保留全部来源；
    同炉次异值整批隔离待核，隔离期间不计入合计。
    """
    by_batch = defaultdict(list)
    for record in records:
        by_batch[record["batch"]].append(record)
    adopted, quarantined = [], []
    for batch in sorted(by_batch):
        group = by_batch[batch]
        values = {(item["pieces"], item["soak_s"]) for item in group}
        if len(values) == 1:
            pieces, soak_s = next(iter(values))
            adopted.append(
                {
                    "batch": batch,
                    "pieces": pieces,
                    "soak_s": soak_s,
                    "sources": sorted({item["terminal"] for item in group}),
                    "reports": len(group),
                }
            )
        else:
            quarantined.append(
                {
                    "code": "BATCH_CONFLICT",
                    "message": LABELS["BATCH_CONFLICT"],
                    "batch": batch,
                    "records": [
                        {
                            "terminal": item["terminal"],
                            "pieces": item["pieces"],
                            "soak_s": item["soak_s"],
                        }
                        for item in sorted(
                            group,
                            key=lambda item: (
                                item["terminal"],
                                item["pieces"],
                                item["soak_s"],
                            ),
                        )
                    ],
                }
            )
    arrived = sorted({record["terminal"] for record in records})
    gaps = [
        {"code": "TERMINAL_MISSING", "message": LABELS["TERMINAL_MISSING"], "terminal": t}
        for t in expected
        if t not in arrived
    ]
    decision = "FULL" if not gaps and not quarantined else "PARTIAL"
    return {
        "shift": shift,
        "expected_terminals": list(expected),
        "arrived_terminals": arrived,
        "status": "READY" if decision == "FULL" else "PENDING",
        "decision": decision,
        "ruling": ruling(decision, len(arrived), len(expected), len(quarantined)),
        "adopted": adopted,
        "quarantined": quarantined,
        "gaps": gaps,
        "totals": {
            "batches": len(adopted),
            "pieces": sum(item["pieces"] for item in adopted),
            "soak_s": sum(item["soak_s"] for item in adopted),
            "quarantined_batches": len(quarantined),
            "missing_terminals": len(gaps),
        },
        "counts": {"stored": len(records)},
    }
