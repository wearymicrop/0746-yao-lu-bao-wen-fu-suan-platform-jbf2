from concurrent.futures import ThreadPoolExecutor

SHIFT = "2026-11-20-D1"
PACKET_A = {
    "terminal": "终端A",
    "records": [
        {"batch": "YL-01", "pieces": 96, "soak_s": 1320},
        {"batch": "YL-02", "pieces": 104, "soak_s": 1260},
    ],
}
PACKET_B = {
    "terminal": "终端B",
    "records": [
        {"batch": "YL-02", "pieces": 104, "soak_s": 1260},
        {"batch": "YL-03", "pieces": 88, "soak_s": 1140},
    ],
}
PACKET_C = {
    "terminal": "终端C",
    "records": [{"batch": "YL-04", "pieces": 110, "soak_s": 1380}],
}
PACKET_C_CONFLICT = {
    "terminal": "终端C",
    "records": [{"batch": "YL-03", "pieces": 89, "soak_s": 1140}],
}


def post(client, body, shift=SHIFT):
    return client.post(f"/api/shifts/{shift}/packets", json=body)


def view(client, shift=SHIFT):
    return client.get(f"/api/shifts/{shift}").json()


def handover(client, operator="值班员", shift=SHIFT):
    return client.post(f"/api/shifts/{shift}/handover", json={"operator": operator})


def test_empty_shift_lists_all_terminals_missing(client):
    ledger = view(client)
    assert ledger["decision"] == "PARTIAL"
    assert [gap["terminal"] for gap in ledger["gaps"]] == ["终端A", "终端B", "终端C"]
    assert ledger["totals"] == {
        "batches": 0,
        "pieces": 0,
        "soak_s": 0,
        "quarantined_batches": 0,
        "missing_terminals": 3,
    }


def test_retry_upload_never_adds_records(client):
    first = post(client, PACKET_A).json()
    assert first["stored"] == 2 and first["duplicates"] == 0
    again = post(client, PACKET_A).json()
    assert again["stored"] == 0 and again["duplicates"] == 2 and again["reused"]
    ledger = view(client)
    assert ledger["counts"]["stored"] == 2
    assert ledger["totals"]["pieces"] == 200 and ledger["totals"]["soak_s"] == 2580


def test_concurrent_retry_stores_once(client):
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: post(client, PACKET_A).json(), range(4)))
    assert sum(row["stored"] for row in results) == 2
    assert view(client)["counts"]["stored"] == 2


def test_same_batch_same_values_counts_once_with_all_sources(client):
    post(client, PACKET_A)
    post(client, PACKET_B)
    ledger = view(client)
    adopted = {row["batch"]: row for row in ledger["adopted"]}
    assert adopted["YL-02"]["pieces"] == 104
    assert adopted["YL-02"]["sources"] == ["终端A", "终端B"]
    assert adopted["YL-02"]["reports"] == 2
    assert ledger["totals"]["pieces"] == 96 + 104 + 88
    assert ledger["totals"]["soak_s"] == 1320 + 1260 + 1140


def test_conflicting_batch_quarantined_and_excluded_from_totals(client):
    post(client, [PACKET_A, PACKET_B])
    before = view(client)["totals"]
    post(client, PACKET_C_CONFLICT)
    ledger = view(client)
    assert ledger["decision"] == "PARTIAL"
    (quar,) = ledger["quarantined"]
    assert quar["batch"] == "YL-03" and quar["code"] == "BATCH_CONFLICT"
    assert {(r["terminal"], r["pieces"]) for r in quar["records"]} == {
        ("终端B", 88),
        ("终端C", 89),
    }
    totals = ledger["totals"]
    assert totals["pieces"] == before["pieces"] - 88
    assert totals["soak_s"] == before["soak_s"] - 1140
    assert totals["quarantined_batches"] == 1


def test_missing_terminal_gap_blocks_full_handover(client):
    post(client, PACKET_A)
    ledger = view(client)
    assert ledger["status"] == "PENDING"
    assert [gap["terminal"] for gap in ledger["gaps"]] == ["终端B", "终端C"]
    assert "缺口挂账" in ledger["ruling"]
    result = handover(client).json()
    assert result["decision"] == "PARTIAL" and result["reused"] is False
    assert result["snapshot"]["totals"]["missing_terminals"] == 2


def test_recovery_merge_after_handover_is_lossless(client):
    post(client, [PACKET_A, PACKET_B])
    partial = handover(client).json()
    assert partial["decision"] == "PARTIAL"
    post(client, PACKET_C)
    ledger = view(client)
    assert ledger["decision"] == "FULL" and ledger["gaps"] == []
    assert ledger["totals"]["pieces"] == 96 + 104 + 88 + 110
    assert ledger["totals"]["soak_s"] == 1320 + 1260 + 1140 + 1380
    full = handover(client).json()
    assert full["decision"] == "FULL" and full["id"] != partial["id"]
    history = client.get(f"/api/shifts/{SHIFT}/handovers").json()["handovers"]
    assert [row["id"] for row in history] == [partial["id"], full["id"]]
    assert history[0]["decision"] == "PARTIAL"


def test_handover_idempotent_and_operator_scoped(client):
    post(client, PACKET_A)
    first = handover(client).json()
    again = handover(client).json()
    assert again["id"] == first["id"] and again["reused"] is True
    other = handover(client, operator="复核员").json()
    assert other["id"] != first["id"]
    assert handover(client, operator="").status_code == 422


def test_invalid_record_rejected_but_valid_stored_and_retry_safe(client):
    packet = {
        "terminal": "终端A",
        "records": [
            {"batch": "YL-05", "pieces": 10, "soak_s": 60},
            {"batch": "YL-06", "pieces": "很多", "soak_s": 60},
            {"batch": "YL-07", "pieces": 12, "soak_s": 120},
        ],
    }
    result = post(client, packet).json()
    assert result["stored"] == 2
    assert [row["row"] for row in result["rejected"]] == [2]
    retry = post(client, packet).json()
    assert retry["stored"] == 0 and retry["duplicates"] == 2
    assert view(client)["counts"]["stored"] == 2


def test_unknown_terminal_and_bad_packet_rejected(client):
    assert post(client, {"terminal": "终端D", "records": []}).json()["code"] == (
        "UNKNOWN_TERMINAL"
    )
    assert post(client, {"records": []}).json()["code"] == "BAD_PACKET"
    assert post(client, []).status_code == 422
    assert post(client, {}).status_code == 422
    response = client.post(f"/api/shifts/{SHIFT}/packets", content="坏文件")
    assert response.json()["code"] == "BAD_JSON"


def test_shift_view_isolated_per_shift(client):
    post(client, PACKET_A, shift="shift-1")
    assert view(client, "shift-2")["counts"]["stored"] == 0
    assert view(client, "shift-1")["counts"]["stored"] == 2


def test_workbench_has_shift_panel(client):
    assert "班次总账" in client.get("/").text
