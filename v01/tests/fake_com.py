"""Pure-Python fake PowerPoint/Excel COM object graph for Linux+Windows unit tests.

ONLY lives under tests/ (never in package code, per HANDOFF v2 Hard Rule 3).
Modeled after thomas-villani/pptlive tests/conftest.py::fake_powerpoint (MIT, 4b33e69217ba):
plain Python classes so COM property writes round-trip deterministically.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image

from pptv01.renderers.powerpoint_com.constants import (
    MSO_FALSE,
    MSO_SHAPE_TYPE_AUTO_SHAPE,
    MSO_SHAPE_TYPE_CHART,
    MSO_SHAPE_TYPE_LINE,
    MSO_SHAPE_TYPE_PICTURE,
    MSO_SHAPE_TYPE_PLACEHOLDER,
    MSO_SHAPE_TYPE_TABLE,
    MSO_SHAPE_TYPE_TEXT_BOX,
    MSO_TRUE,
    PP_LAYOUT_TEXT,
    PP_LAYOUT_TITLE,
    PP_LAYOUT_TITLE_ONLY,
    PP_PLACEHOLDER_BODY,
    PP_PLACEHOLDER_TITLE,
)


class _FakeColor:
    def __init__(self) -> None:
        self.RGB: int = 0


class _FakeFill:
    def __init__(self) -> None:
        self.ForeColor = _FakeColor()
        self.Visible = MSO_TRUE
        self.is_solid = False

    def Solid(self) -> None:
        self.is_solid = True
        self.Visible = MSO_TRUE


class _FakeLine:
    def __init__(self) -> None:
        self.ForeColor = _FakeColor()
        self.Visible = MSO_TRUE
        self.Weight = 1.0
        self.DashStyle = 1
        self.EndArrowheadStyle = 1


class _FakeFont:
    def __init__(self) -> None:
        self.Name = "Calibri"
        self.NameFarEast = "Microsoft YaHei"
        self.Size = 16.0
        self.Bold = MSO_FALSE
        self.Fill = _FakeFill()


class _FakeParagraphFormat:
    def __init__(self) -> None:
        self.Alignment = 1


class _FakeTextRange:
    def __init__(self, shape: "FakeShape") -> None:
        self._shape = shape
        self.Text = ""
        self.Font = _FakeFont()
        self.ParagraphFormat = _FakeParagraphFormat()
        self._bound_w_override: float | None = None
        self._bound_h_override: float | None = None

    @property
    def BoundWidth(self) -> float:
        if self._bound_w_override is not None:
            return self._bound_w_override
        return min(self._shape.Width, max(10.0, len(self.Text) * self.Font.Size * 0.55))

    @property
    def BoundHeight(self) -> float:
        if self._bound_h_override is not None:
            return self._bound_h_override
        lines = max(1, len(self.Text.splitlines()))
        return lines * self.Font.Size * 1.35


class _FakeTextFrame2:
    def __init__(self, shape: "FakeShape") -> None:
        self.TextRange = _FakeTextRange(shape)
        self.WordWrap = MSO_TRUE
        self.VerticalAnchor = 1
        self.AutoSize = 0

    @property
    def HasText(self) -> int:
        return MSO_TRUE if self.TextRange.Text else MSO_FALSE


class _FakeTextFrame:
    def __init__(self, tf2: _FakeTextFrame2) -> None:
        self.TextRange = tf2.TextRange


class _FakePlaceholderFormat:
    def __init__(self, ptype: int) -> None:
        self.Type = ptype


class _FakePictureFormat:
    def __init__(self) -> None:
        self.CropLeft = 0.0
        self.CropRight = 0.0
        self.CropTop = 0.0
        self.CropBottom = 0.0


class _FakeConnectorFormat:
    def __init__(self) -> None:
        self.BeginConnected = MSO_FALSE
        self.EndConnected = MSO_FALSE
        self.begin_shape: FakeShape | None = None
        self.end_shape: FakeShape | None = None
        self.begin_site: int = 0
        self.end_site: int = 0

    def BeginConnect(self, shape: "FakeShape", site: int) -> None:
        self.begin_shape = shape
        self.begin_site = site
        self.BeginConnected = MSO_TRUE

    def EndConnect(self, shape: "FakeShape", site: int) -> None:
        self.end_shape = shape
        self.end_site = site
        self.EndConnected = MSO_TRUE


class _FakeCell:
    def __init__(self, row: int, col: int) -> None:
        self.Shape = FakeShape(row * 100 + col, f"cell_{row}_{col}", MSO_SHAPE_TYPE_TEXT_BOX, 0, 0, 100, 30)


class _FakeTable:
    def __init__(self, n_rows: int, n_cols: int) -> None:
        self.n_rows = n_rows
        self.n_cols = n_cols
        self._cells = {(r, c): _FakeCell(r, c) for r in range(1, n_rows + 1) for c in range(1, n_cols + 1)}

    def Cell(self, r: int, c: int) -> _FakeCell:
        return self._cells[(r, c)]


class _FakeWorksheetCells:
    def __init__(self) -> None:
        self.data: dict[tuple[int, int], Any] = {}
        self.cleared = False

    def ClearContents(self) -> None:
        self.data.clear()
        self.cleared = True

    def Clear(self) -> None:
        self.ClearContents()

    def __call__(self, r: int, c: int) -> "_FakeCellVal":
        return _FakeCellVal(self.data, r, c)


class _FakeCellVal:
    def __init__(self, store: dict[tuple[int, int], Any], r: int, c: int) -> None:
        self._store = store
        self._key = (r, c)

    @property
    def Value(self) -> Any:
        return self._store.get(self._key)

    @Value.setter
    def Value(self, v: Any) -> None:
        self._store[self._key] = v


class _FakeWorksheet:
    def __init__(self) -> None:
        self.Cells = _FakeWorksheetCells()


class _FakeWorkbook:
    def __init__(self) -> None:
        self._ws = _FakeWorksheet()
        self.closed = False
        self.close_save_arg: Any = None

    def Worksheets(self, idx: int) -> _FakeWorksheet:
        assert idx == 1
        return self._ws

    def Close(self, save_changes: Any = False) -> None:
        self.closed = True
        self.close_save_arg = save_changes


class _FakeChartData:
    def __init__(self) -> None:
        self.activated = False
        self.Workbook = _FakeWorkbook()

    def Activate(self) -> None:
        self.activated = True


class _FakeChartTitle:
    def __init__(self) -> None:
        self.Text = ""


class _FakeChart:
    def __init__(self, xl_type: int) -> None:
        self.ChartType = xl_type
        self.ChartData = _FakeChartData()
        self.HasTitle = False
        self.ChartTitle = _FakeChartTitle()
        self.source_range: str | None = None

    def SetSourceData(self, range_str: str) -> None:
        if not isinstance(range_str, str):
            raise RuntimeError("SetSourceData requires a string range")
        self.source_range = range_str


class FakeShape:
    def __init__(
        self,
        shape_id: int,
        name: str,
        shape_type: int,
        left: float,
        top: float,
        width: float,
        height: float,
        *,
        placeholder_type: int | None = None,
    ) -> None:
        self.Id = shape_id
        self.Name = name
        self.Type = shape_type
        self.Left = float(left)
        self.Top = float(top)
        self.Width = float(width)
        self.Height = float(height)
        self.LockAspectRatio = MSO_FALSE
        self.Fill = _FakeFill()
        self.Line = _FakeLine()
        self.HasTextFrame = MSO_TRUE
        self.TextFrame2 = _FakeTextFrame2(self)
        self.TextFrame = _FakeTextFrame(self.TextFrame2)
        self.HasChart = MSO_FALSE
        self.Chart: _FakeChart | None = None
        self.HasTable = MSO_FALSE
        self.Table: _FakeTable | None = None
        self.Connector = MSO_FALSE
        self.ConnectorFormat = _FakeConnectorFormat()
        self.PictureFormat = _FakePictureFormat()
        self.AlternativeText = ""
        self.PlaceholderFormat = _FakePlaceholderFormat(placeholder_type or 0)
        self.picture_file: str | None = None
        self._parent: "_FakeShapes | None" = None

    def ZOrder(self, cmd: int) -> None:
        if self._parent is None or self not in self._parent._items:
            return
        self._parent._items.remove(self)
        if cmd == 1:  # msoSendToBack
            self._parent._items.insert(0, self)
        else:  # 0 = msoBringToFront
            self._parent._items.append(self)


class _FakeShapes:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self._items: list[FakeShape] = []
        self._next_id = 1
        self._log = log

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    @property
    def Count(self) -> int:
        return len(self._items)

    @property
    def HasTitle(self) -> int:
        return MSO_TRUE if any(s.Type == MSO_SHAPE_TYPE_PLACEHOLDER and s.PlaceholderFormat.Type == PP_PLACEHOLDER_TITLE for s in self._items) else MSO_FALSE

    @property
    def Title(self) -> FakeShape:
        for s in self._items:
            if s.Type == MSO_SHAPE_TYPE_PLACEHOLDER and s.PlaceholderFormat.Type == PP_PLACEHOLDER_TITLE:
                return s
        raise RuntimeError("no title placeholder")

    def Placeholders(self, idx: int) -> FakeShape:
        phs = [s for s in self._items if s.Type == MSO_SHAPE_TYPE_PLACEHOLDER]
        return phs[idx - 1]

    def _add(self, sh: FakeShape) -> FakeShape:
        sh._parent = self
        self._items.append(sh)
        return sh

    def _id(self) -> int:
        v = self._next_id
        self._next_id += 1
        return v

    def AddTextbox(self, orientation: int, left: float, top: float, width: float, height: float) -> FakeShape:
        self._log.append(("AddTextbox", (left, top, width, height)))
        return self._add(FakeShape(self._id(), "TextBox", MSO_SHAPE_TYPE_TEXT_BOX, left, top, width, height))

    def AddShape(self, shape_type: int, left: float, top: float, width: float, height: float) -> FakeShape:
        self._log.append(("AddShape", (shape_type, left, top, width, height)))
        return self._add(FakeShape(self._id(), "Shape", MSO_SHAPE_TYPE_AUTO_SHAPE, left, top, width, height))

    def AddPicture(self, path: str, link: int, save_with_doc: int, left: float, top: float, width: float, height: float) -> FakeShape:
        self._log.append(("AddPicture", (path, link, save_with_doc)))
        w, h = (width, height) if width > 0 and height > 0 else (300.0, 200.0)
        sh = FakeShape(self._id(), "Picture", MSO_SHAPE_TYPE_PICTURE, left, top, w, h)
        sh.HasTextFrame = MSO_FALSE
        sh.picture_file = path
        return self._add(sh)

    def AddTable(self, n_rows: int, n_cols: int, left: float, top: float, width: float, height: float) -> FakeShape:
        self._log.append(("AddTable", (n_rows, n_cols)))
        sh = FakeShape(self._id(), "Table", MSO_SHAPE_TYPE_TABLE, left, top, width, height)
        sh.HasTextFrame = MSO_FALSE
        sh.HasTable = MSO_TRUE
        sh.Table = _FakeTable(n_rows, n_cols)
        return self._add(sh)

    def AddChart2(self, style: int, xl_type: int, left: float, top: float, width: float, height: float) -> FakeShape:
        self._log.append(("AddChart2", (style, xl_type)))
        sh = FakeShape(self._id(), "Chart", MSO_SHAPE_TYPE_CHART, left, top, width, height)
        sh.HasTextFrame = MSO_FALSE
        sh.HasChart = MSO_TRUE
        sh.Chart = _FakeChart(xl_type)
        return self._add(sh)

    def AddConnector(self, conn_type: int, x1: float, y1: float, x2: float, y2: float) -> FakeShape:
        self._log.append(("AddConnector", (conn_type,)))
        sh = FakeShape(self._id(), "Connector", MSO_SHAPE_TYPE_LINE, x1, y1, abs(x2 - x1), abs(y2 - y1))
        sh.HasTextFrame = MSO_FALSE
        sh.Connector = MSO_TRUE
        return self._add(sh)


class _FakeNotesPage:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self.Shapes = _FakeShapes(log)
        self.Shapes._add(FakeShape(901, "NotesSlideImage", MSO_SHAPE_TYPE_PLACEHOLDER, 0, 0, 100, 100, placeholder_type=1))
        self.Shapes._add(FakeShape(902, "NotesBody", MSO_SHAPE_TYPE_PLACEHOLDER, 0, 100, 400, 200, placeholder_type=PP_PLACEHOLDER_BODY))


class FakeSlide:
    def __init__(self, layout_id: int, log: list[tuple[str, Any]]) -> None:
        self.layout_id = layout_id
        self.Shapes = _FakeShapes(log)
        self.NotesPage = _FakeNotesPage(log)
        if layout_id in (PP_LAYOUT_TITLE, PP_LAYOUT_TEXT, PP_LAYOUT_TITLE_ONLY):
            self.Shapes._add(
                FakeShape(self.Shapes._id(), "Title 1", MSO_SHAPE_TYPE_PLACEHOLDER, 40, 20, 860, 60, placeholder_type=PP_PLACEHOLDER_TITLE)
            )
        if layout_id == PP_LAYOUT_TEXT:
            self.Shapes._add(
                FakeShape(self.Shapes._id(), "Content Placeholder 2", MSO_SHAPE_TYPE_PLACEHOLDER, 40, 100, 860, 400, placeholder_type=PP_PLACEHOLDER_BODY)
            )


class _FakeSlides:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self._items: list[FakeSlide] = []
        self._log = log

    @property
    def Count(self) -> int:
        return len(self._items)

    def __call__(self, idx: int) -> FakeSlide:
        return self._items[idx - 1]

    def __iter__(self):
        return iter(self._items)

    def Add(self, idx: int, layout_id: int) -> FakeSlide:
        self._log.append(("Slides.Add", (idx, layout_id)))
        sl = FakeSlide(layout_id, self._log)
        self._items.insert(idx - 1, sl)
        return sl


class _FakePageSetup:
    def __init__(self) -> None:
        self.SlideWidth = 960.0
        self.SlideHeight = 540.0


class _FakeFontEntry:
    def __init__(self, name: str) -> None:
        self.Name = name


class FakePresentation:
    def __init__(self, app: "FakePowerPointApp", name: str = "Presentation1", *, with_window: int = 0) -> None:
        self._app = app
        self.Name = name
        self.with_window = with_window
        self.PageSetup = _FakePageSetup()
        self.Slides = _FakeSlides(app.call_log)
        self.Fonts = [_FakeFontEntry("Microsoft YaHei"), _FakeFontEntry("Calibri")]
        self.saved_path: str | None = None
        self.closed = False

    def SaveAs(self, path: str, file_format: int | None = None) -> None:
        self._app.call_log.append(("SaveAs", (path, file_format)))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        if file_format == 32:
            Path(path).write_bytes(b"%PDF-1.4\n%%EOF\n")
            return
        self.saved_path = path
        Path(path).write_bytes(b"FAKE_PPTX_FOR_UNIT_TESTS")
        self._app._saved_presentations[str(Path(path).resolve())] = self

    def Export(self, out_dir: str, fmt: str, width_px: int, height_px: int) -> None:
        self._app.call_log.append(("Export", (fmt, width_px, height_px)))
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, self.Slides.Count + 1):
            im = Image.new("RGB", (min(width_px, 320), min(height_px, 180)), (240, 245, 255))
            im.save(d / f"Slide{i}.PNG", "PNG")

    def ExportAsFixedFormat(self, path: str, fixed_type: int) -> None:
        self._app.call_log.append(("ExportAsFixedFormat", (fixed_type,)))
        Path(path).write_bytes(b"%PDF-1.4\n%%EOF\n")

    def Close(self) -> None:
        self._app.call_log.append(("Presentation.Close", self.Name))
        self.closed = True
        if self in self._app.Presentations._items:
            self._app.Presentations._items.remove(self)


class _FakePresentations:
    def __init__(self, app: "FakePowerPointApp") -> None:
        self._app = app
        self._items: list[FakePresentation] = []

    @property
    def Count(self) -> int:
        return len(self._items)

    def Add(self, WithWindow: int = MSO_TRUE) -> FakePresentation:
        self._app.call_log.append(("Presentations.Add", WithWindow))
        pres = FakePresentation(self._app, f"NewPres{len(self._items) + 1}", with_window=WithWindow)
        self._items.append(pres)
        return pres

    def Open(self, path: str, ReadOnly: int = MSO_FALSE, Untitled: int = MSO_FALSE, WithWindow: int = MSO_TRUE) -> FakePresentation:
        self._app.call_log.append(("Presentations.Open", (Path(path).name, ReadOnly, Untitled, WithWindow)))
        key = str(Path(path).resolve())
        saved = self._app._saved_presentations.get(key)
        if saved is not None:
            saved.closed = False
            if saved not in self._items:
                self._items.append(saved)
            return saved
        pres = FakePresentation(self._app, Path(path).name, with_window=WithWindow)
        self._items.append(pres)
        return pres


class FakePowerPointApp:
    """Fake PowerPoint.Application COM object for deterministic unit testing."""

    def __init__(self, *, pre_existing_user_docs: int = 0) -> None:
        self.Version = "16.0"
        self.Build = "20527"
        self.DisplayAlerts = 2
        self.AutomationSecurity = 1
        self.WindowState = 1
        self.call_log: list[tuple[str, Any]] = []
        self._saved_presentations: dict[str, FakePresentation] = {}
        self.Presentations = _FakePresentations(self)
        self.quit_called = False
        self.user_presentations: list[FakePresentation] = []
        for i in range(pre_existing_user_docs):
            up = FakePresentation(self, f"UserOpenDeck_{i + 1}.pptx")
            self.Presentations._items.append(up)
            self.user_presentations.append(up)

    def Quit(self) -> None:
        self.call_log.append(("Application.Quit", None))
        self.quit_called = True


class FakeExcelApp:
    def __init__(self) -> None:
        self.Version = "16.0"
        self.Build = "20527.0"
        self.DisplayAlerts = True
        self.quit_called = False

    def Quit(self) -> None:
        self.quit_called = True
