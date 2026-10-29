from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

EVIDENCE = Path(__file__).resolve().parent
RAW = EVIDENCE / "r304_A10_attachment_response.raw"
OUTPUT = EVIDENCE / "r304_A10_workbook_audit.json"
EXPECTED_RAW_SHA256 = "e291b4d9f388279ecf06e0c88c57c8ec47761e1dc9727d136502dcc6ae13b94e"
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def excel_text(cell: ET.Element, shared: list[str]) -> str:
    value = cell.find(f"{{{MAIN_NS}}}v")
    text = value.text if value is not None and value.text else ""
    if cell.attrib.get("t") == "s" and text:
        return shared[int(text)]
    if cell.attrib.get("t") == "inlineStr":
        inline = cell.find(f"{{{MAIN_NS}}}is")
        if inline is not None:
            return "".join(node.text or "" for node in inline.iter(f"{{{MAIN_NS}}}t"))
    return text


def column(cell_ref: str) -> str:
    return "".join(char for char in cell_ref if char.isalpha())


def main() -> None:
    raw = RAW.read_bytes()
    raw_hash = sha(raw)
    if raw_hash != EXPECTED_RAW_SHA256:
        raise RuntimeError("A10 attachment raw hash mismatch")

    sheets: dict[str, list[dict[str, str]]] = {}
    with zipfile.ZipFile(RAW) as workbook:
        names = set(workbook.namelist())
        workbook_root = ET.fromstring(workbook.read("xl/workbook.xml"))
        rel_root = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
        relationships = {node.attrib["Id"]: node.attrib["Target"] for node in rel_root.findall(f"{{{PKG_REL_NS}}}Relationship")}
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            shared_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
            shared = ["".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t")) for item in shared_root.findall(f"{{{MAIN_NS}}}si")]

        for sheet in workbook_root.findall(f".//{{{MAIN_NS}}}sheet"):
            target = relationships[sheet.attrib[f"{{{REL_NS}}}id"]].lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            root = ET.fromstring(workbook.read(target))
            rows: list[dict[str, str]] = []
            for row in root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
                values = {column(cell.attrib["r"]): excel_text(cell, shared).strip() for cell in row.findall(f"{{{MAIN_NS}}}c")}
                if values:
                    rows.append(values)
            sheets[sheet.attrib["name"]] = rows

    deltas: dict[str, dict[str, object]] = {}
    for sheet_name in ("调入", "调出"):
        rows = sheets.get(sheet_name, [])
        if not rows:
            raise RuntimeError(f"Expected workbook sheet missing: {sheet_name}")
        header = rows[0]
        index_col = next((key for key, value in header.items() if value == "指数代码"), None)
        code_col = next((key for key, value in header.items() if value == "证券代码"), None)
        name_col = next((key for key, value in header.items() if value in {"证券名称", "证券简称", "股票名称"}), None)
        if index_col is None or code_col is None or name_col is None:
            raise RuntimeError(f"Unexpected columns in {sheet_name}: {header}")
        members = [
            {"code": row[code_col].zfill(6), "name": row[name_col]}
            for row in rows[1:]
            if row.get(index_col, "").zfill(6) == "000300" and row.get(code_col)
        ]
        codes = [item["code"] for item in members]
        deltas[sheet_name] = {
            "row_count": len(members),
            "unique_code_count": len(set(codes)),
            "duplicate_codes": sorted({code for code in codes if codes.count(code) > 1}),
            "members": members,
        }

    audit = {
        "round": "R304-A10",
        "classification": "local_parse_of_single_official_workbook_no_network",
        "source_file": RAW.name,
        "source_sha256": raw_hash,
        "workbook_sheets": {name: len(rows) for name, rows in sheets.items()},
        "hs300_delta": deltas,
        "count_reconciles_24_replacements": all(deltas[name]["row_count"] == 24 and deltas[name]["unique_code_count"] == 24 for name in ("调入", "调出")),
        "complete_hs300_snapshot_present": False,
        "effective_date": "2017-12-11",
        "effective_date_source": "official notice 12590 body retained in r304_A9_detail_response.raw; announced as adjustment date",
        "seed_for_2018_01_02_supported": False,
        "interpretation": "Workbook has 24 incoming and 24 outgoing HS300 codes, but no complete member snapshot and no independently known prior 300-member state. The delta alone cannot yield the 2018-01-02 seed.",
        "network_requests": 0,
    }
    if not audit["count_reconciles_24_replacements"]:
        raise RuntimeError("HS300 delta counts do not match the official announcement")
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("A10 workbook audit exists; refusing overwrite")
    encoded = (json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with OUTPUT.open("xb") as stream:
        stream.write(encoded)
    with OUTPUT.with_name(OUTPUT.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{sha(encoded)}  {OUTPUT.name}\n")
    print(json.dumps({
        "audit_sha256": sha(encoded),
        "incoming_count": deltas["调入"]["row_count"],
        "outgoing_count": deltas["调出"]["row_count"],
        "unique_incoming": deltas["调入"]["unique_code_count"],
        "unique_outgoing": deltas["调出"]["unique_code_count"],
        "complete_snapshot_present": audit["complete_hs300_snapshot_present"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
