"""The only code that talks to Google. Everything else uses the small SheetIO interface below,
so the sync logic can be tested with MemorySheet (no Google account, no internet).

Auth: a Google Cloud *service account* (JSON key in secrets/, path in .env GOOGLE_SERVICE_ACCOUNT_FILE).
Share your spreadsheet with the service account's e-mail (Editor). Sheet ID in .env GOOGLE_SHEET_ID.
Quota: Google allows ~60 write requests per minute; one sync uses about one request per tab.
"""

import time

from config.settings import env
from core.logger import get_logger
from core.paths import ROOT

log = get_logger("sheets")


def col_letter(n: int) -> str:
    """1 → A, 27 → AA"""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class Link(str):
    """A cell shown as clickable text: Link("open CLIP0004", "http://localhost:8765/clip/CLIP0004")."""

    def __new__(cls, text, url):
        obj = super().__new__(cls, text)
        obj.url = url
        return obj

    def formula(self):
        q = lambda x: str(x).replace('"', '""')  # noqa: E731
        return f'=HYPERLINK("{q(self.url)}","{q(str(self))}")'


class SheetIO:
    """read(tab) → rows (list of lists of str, row 1 = headers)."""

    def tabs(self) -> list[str]: ...
    def add_tab(self, name: str, headers: list[str], widths=None, dropdowns=None, typed=None): ...
    def format_tab(self, name: str, widths=None, dropdowns=None, typed=None): ...  # widths, dropdowns, header colours
    def arrange(self, order: list[str], colors: dict): ...       # tab order + tab colours
    def read(self, tab: str) -> list[list[str]]: ...
    def write_all(self, tab: str, rows: list[list]): ...          # replace the whole tab (rows incl. headers)
    def update_cells(self, tab: str, cells: list[tuple]): ...     # [(row, col, value)], 1-based


class MemorySheet(SheetIO):
    """In-memory spreadsheet for tests."""

    def __init__(self):
        self.data, self.writes = {}, 0

    def tabs(self):
        return list(self.data)

    def add_tab(self, name, headers, widths=None, dropdowns=None, typed=None):
        self.data[name] = [list(headers)]

    def format_tab(self, name, widths=None, dropdowns=None, typed=None):
        pass

    def arrange(self, order, colors):
        self.data = {k: self.data[k] for k in sorted(self.data, key=lambda t: order.index(t) if t in order else 99)}

    def read(self, tab):
        return [list(r) for r in self.data.get(tab, [])]

    def write_all(self, tab, rows):
        self.writes += 1
        self.data[tab] = [["" if v is None else str(v) for v in r] for r in rows]

    def update_cells(self, tab, cells):
        self.writes += 1
        t = self.data[tab]
        for r, c, v in cells:
            while len(t) < r:
                t.append([])
            while len(t[r - 1]) < c:
                t[r - 1].append("")
            t[r - 1][c - 1] = "" if v is None else str(v)


class GoogleSheet(SheetIO):
    """gspread implementation with retries on rate limits / network errors."""

    def __init__(self, sheet_id=None, key_file=None):
        import gspread
        key = key_file or env("GOOGLE_SERVICE_ACCOUNT_FILE", "secrets/service_account.json")
        path = (ROOT / key) if not key[1:3] == ":\\" and not key.startswith("/") else key
        self.gc = gspread.service_account(filename=str(path))
        self.sh = self.gc.open_by_key(sheet_id or env("GOOGLE_SHEET_ID"))
        self._ws = {}

    def _call(self, fn, *a, **k):
        last = None
        for wait in (0, 5, 20, 65):
            if wait:
                time.sleep(wait)
            try:
                return fn(*a, **k)
            except Exception as exc:  # noqa: BLE001 - 429 quota / 5xx / network
                last = exc
                msg = str(exc)
                if not any(x in msg for x in ("429", "500", "502", "503", "Quota", "timed out", "Connection")):
                    raise
                log.warning(f"Google Sheets busy ({msg[:80]}), retrying in {wait or 5}s")
        raise last

    def ws(self, tab):
        if tab not in self._ws:
            self._ws[tab] = self._call(self.sh.worksheet, tab)
        return self._ws[tab]

    def tabs(self):
        return [w.title for w in self._call(self.sh.worksheets)]

    def add_tab(self, name, headers, widths=None, dropdowns=None, typed=None):
        ws = self._call(self.sh.add_worksheet, title=name, rows=1000, cols=max(len(headers), 6))
        self._ws[name] = ws
        self._call(ws.update, values=[headers], range_name="A1")
        self.format_tab(name, widths, dropdowns, typed)

    def arrange(self, order, colors):
        """Put the tabs in `order` and colour them (unknown tabs of yours go to the end)."""
        sheets = self._call(self.sh.worksheets)
        sheets.sort(key=lambda w: order.index(w.title) if w.title in order else 99)
        reqs = []
        for i, w in enumerate(sheets):
            props, fields = {"sheetId": w.id, "index": i}, ["index"]
            if w.title in colors:
                r, g, b = colors[w.title]
                props["tabColor"] = {"red": r, "green": g, "blue": b}
                fields.append("tabColor")
            reqs.append({"updateSheetProperties": {"properties": props, "fields": ",".join(fields)}})
        if reqs:
            self._call(self.sh.batch_update, {"requests": reqs})

    def _fit_cols(self, ws, n):
        if ws.col_count < n:                       # a newer version added columns
            self._call(ws.add_cols, n - ws.col_count)

    def format_tab(self, name, widths=None, dropdowns=None, typed=None):
        """Header row: bold; GREEN = a column you type in, grey = the farm fills it (typing there does nothing)."""
        ws = self.ws(name)
        self._fit_cols(ws, len(widths or []))

        def head(c0, c1, rgb):
            return {"repeatCell": {"range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1,
                                             "startColumnIndex": c0, "endColumnIndex": c1},
                                   "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                                                  "backgroundColor": dict(zip(("red", "green", "blue"), rgb))}},
                                   "fields": "userEnteredFormat(textFormat,backgroundColor)"}}
        reqs = [head(0, max(len(widths or []), 1), (0.90, 0.90, 0.90))]
        reqs += [head(c - 1, c, (0.72, 0.88, 0.72)) for c in (typed or [])]
        reqs += [
                {"updateSheetProperties": {"properties": {"sheetId": ws.id,
                                                          "gridProperties": {"frozenRowCount": 1}},
                                           "fields": "gridProperties.frozenRowCount"}},
                # clear old dropdowns first (columns may have moved)
                {"setDataValidation": {"range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": 1000}}}]
        for i, w in enumerate(widths or []):
            if w:
                reqs.append({"updateDimensionProperties": {
                    "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                    "properties": {"pixelSize": w}, "fields": "pixelSize"}})
        for col, options in (dropdowns or {}).items():
            reqs.append({"setDataValidation": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": 1000,
                          "startColumnIndex": col - 1, "endColumnIndex": col},
                "rule": {"condition": {"type": "ONE_OF_LIST",
                                       "values": [{"userEnteredValue": o} for o in options]},
                         "showCustomUi": True, "strict": False}}})
        self._call(self.sh.batch_update, {"requests": reqs})

    def read(self, tab):
        return self._call(self.ws(tab).get_all_values)

    def write_all(self, tab, rows):
        ws = self.ws(tab)
        rows = [["" if v is None else v for v in r] for r in rows]
        width = max((len(r) for r in rows), default=1)
        rows = [r + [""] * (width - len(r)) for r in rows]
        self._fit_cols(ws, width)
        self._call(ws.update, values=rows, range_name="A1", raw=True)
        links = [{"range": f"{col_letter(c + 1)}{r + 1}", "values": [[v.formula()]]}
                 for r, row in enumerate(rows) for c, v in enumerate(row) if isinstance(v, Link)]
        if links:
            self._call(ws.batch_update, links, raw=False)
        n = len(rows)
        if ws.row_count > n:
            self._call(ws.batch_clear, [f"A{n + 1}:{col_letter(max(width, ws.col_count))}{ws.row_count}"])

    def update_cells(self, tab, cells):
        if not cells:
            return
        ws = self.ws(tab)

        def send():
            # built fresh on every try: gspread writes the tab name INTO these dicts, so re-sending the same
            # list after a network hiccup asked for 'Ready to Post'!'Ready to Post'!E2 and failed (2026-11-20)
            data = [{"range": f"{col_letter(c)}{r}", "values": [["" if v is None else v]]} for r, c, v in cells]
            return ws.batch_update(data, raw=True)

        self._call(send)


def open_sheet() -> SheetIO:
    if not env("GOOGLE_SHEET_ID"):
        raise RuntimeError("GOOGLE_SHEET_ID is empty in .env (see docs/SHEETS_SETUP.md)")
    return GoogleSheet()
