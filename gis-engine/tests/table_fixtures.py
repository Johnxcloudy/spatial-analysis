from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook


def create_table_fixture_bundle(directory: Path) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    csv_path = directory / "coordinates.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["code", "x", "y", "label"])
        writer.writerows([
            ["001", "114", "27", "Chinese \u571f\u5730"],
            ["002", "", "27", "NULL"],
            ["003", "200", "95", "outside"],
            ["004", " 114.5 ", "27.5", ""],
        ])
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "coordinates"
    sheet.append(["Coordinate examples"])
    sheet.append(["code", "x", "y", "mixed"])
    sheet.append(["001", 114, 27, "001"])
    sheet.append(["002", "=114+1", 27, True])
    sheet.append(["003", True, 27, datetime(2026, 9, 9, 12, 30)])
    sheet.append(["004", datetime(2026, 9, 9), 27, "#DIV/0!"])
    sheet.append(["005", 114.5, 27.5, None])
    sheet["B3"].number_format = "000.00"
    workbook.create_sheet("other").append(["name", "value"])
    xlsx_path = directory / "coordinates.xlsx"
    workbook.save(xlsx_path)
    workbook.close()
    return {"csv": str(csv_path), "xlsx": str(xlsx_path)}
