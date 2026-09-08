"""Movie Shot Analyzer V4 Base

A deliberately small, stable Windows-first base build.
Goal: prove the UI, image loading, drag/drop, folder loading and thirds guide
before adding advanced perspective/lens features.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


class ImageCanvas(QWidget):
    def __init__(self, owner: "MovieShotAnalyzer") -> None:
        super().__init__()
        self.owner = owner
        self.pixmap: QPixmap | None = None
        self.image_rect = QRectF()
        self.setAcceptDrops(True)
        self.setMinimumSize(640, 420)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        self.owner.open_paths(paths)
        event.acceptProposedAction()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#171a20"))

        if self.pixmap is None:
            painter.setPen(QColor("#aeb7c4"))
            font = painter.font()
            font.setPointSize(14)
            painter.setFont(font)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "画像またはフォルダをここへドラッグ＆ドロップ\n\nまたは左の『画像を開く』『フォルダを開く』を押してください",
            )
            return

        available = self.rect().adjusted(18, 18, -18, -18)
        scaled = self.pixmap.size().scaled(available.size(), Qt.AspectRatioMode.KeepAspectRatio)
        x = available.left() + (available.width() - scaled.width()) / 2
        y = available.top() + (available.height() - scaled.height()) / 2
        self.image_rect = QRectF(x, y, scaled.width(), scaled.height())
        painter.drawPixmap(self.image_rect.toRect(), self.pixmap)

        if self.owner.show_thirds.isChecked():
            pen = QPen(QColor("#ff4d4f"))
            pen.setWidth(2)
            painter.setPen(pen)
            r = self.image_rect
            for q in (1 / 3, 2 / 3):
                xx = r.left() + r.width() * q
                yy = r.top() + r.height() * q
                painter.drawLine(int(xx), int(r.top()), int(xx), int(r.bottom()))
                painter.drawLine(int(r.left()), int(yy), int(r.right()), int(yy))

        if self.owner.show_cross.isChecked():
            pen = QPen(QColor("#44b5ff"))
            pen.setWidth(2)
            painter.setPen(pen)
            r = self.image_rect
            cx, cy = r.center().x(), r.center().y()
            painter.drawLine(int(cx), int(r.top()), int(cx), int(r.bottom()))
            painter.drawLine(int(r.left()), int(cy), int(r.right()), int(cy))


class MovieShotAnalyzer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Movie Shot Analyzer V4 Base")
        self.resize(1440, 900)
        self.setMinimumSize(980, 640)
        self.setAcceptDrops(True)

        self.paths: list[Path] = []
        self.current_index = -1

        self._build_ui()
        self._apply_dark_style()
        self.statusBar().showMessage("V4 Base — UI確認版")

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # IMPORTANT: the controls widget belongs to the scroll area only.
        # V3 accidentally detached the panel after setting it as the scroll widget,
        # which produced an empty white sidebar on Windows.
        self.controls_widget = QWidget()
        self.controls_widget.setObjectName("controlsWidget")
        controls = QVBoxLayout(self.controls_widget)
        controls.setContentsMargins(12, 12, 12, 12)
        controls.setSpacing(10)

        title = QLabel("Movie Shot Analyzer")
        title.setObjectName("appTitle")
        subtitle = QLabel("V4 Base / Windows UI確認版")
        subtitle.setObjectName("subtitle")
        controls.addWidget(title)
        controls.addWidget(subtitle)

        open_image = QPushButton("画像を開く")
        open_image.clicked.connect(self.choose_images)
        open_folder = QPushButton("フォルダを開く")
        open_folder.clicked.connect(self.choose_folder)
        controls.addWidget(open_image)
        controls.addWidget(open_folder)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        controls.addWidget(separator)

        self.file_label = QLabel("画像未選択")
        self.file_label.setWordWrap(True)
        self.file_label.setObjectName("fileLabel")
        controls.addWidget(self.file_label)

        nav = QHBoxLayout()
        self.prev_button = QPushButton("◀ 前")
        self.next_button = QPushButton("次 ▶")
        self.prev_button.clicked.connect(self.prev_image)
        self.next_button.clicked.connect(self.next_image)
        nav.addWidget(self.prev_button)
        nav.addWidget(self.next_button)
        controls.addLayout(nav)

        self.show_thirds = QCheckBox("三分割を表示")
        self.show_thirds.setChecked(True)
        self.show_thirds.toggled.connect(self._refresh_canvas)
        self.show_cross = QCheckBox("中央十字を表示")
        self.show_cross.toggled.connect(self._refresh_canvas)
        controls.addWidget(self.show_thirds)
        controls.addWidget(self.show_cross)

        note = QLabel(
            "この版では、まず次だけ確認します。\n"
            "・操作パネルが表示される\n"
            "・画像 / フォルダを開ける\n"
            "・ドラッグ＆ドロップできる\n"
            "・三分割 / 中央線が表示される\n\n"
            "ここがWindows EXEで正常なら、次版でVP・レンズ換算などを追加します。"
        )
        note.setWordWrap(True)
        note.setObjectName("note")
        controls.addWidget(note)
        controls.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("controlScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.controls_widget)
        scroll.setMinimumWidth(290)
        scroll.setMaximumWidth(340)
        outer.addWidget(scroll, 0)

        self.canvas = ImageCanvas(self)
        outer.addWidget(self.canvas, 1)
        self._update_nav()

    def _apply_dark_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #20242b; color: #e8edf3; }
            #controlsWidget { background: #20242b; }
            #controlScroll { border: 1px solid #343a43; background: #20242b; }
            #appTitle { font-size: 20px; font-weight: 700; }
            #subtitle { color: #9ca7b5; }
            #fileLabel { background: #171a20; border: 1px solid #343a43; border-radius: 5px; padding: 8px; }
            #note { color: #aeb7c4; line-height: 1.4; }
            QPushButton { background: #303641; border: 1px solid #48505d; border-radius: 5px; padding: 8px; }
            QPushButton:hover { background: #3a424f; }
            QPushButton:disabled { color: #69717c; background: #272b32; }
            QCheckBox { padding: 5px 1px; }
            QScrollBar:vertical { background: #20242b; width: 12px; }
            QScrollBar::handle:vertical { background: #4a5260; min-height: 28px; border-radius: 5px; }
            QStatusBar { background: #171a20; color: #aeb7c4; }
            """
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        self.open_paths(paths)
        event.acceptProposedAction()

    def choose_images(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self,
            "画像を開く",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)",
        )
        if names:
            self.open_paths([Path(name) for name in names])

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "画像フォルダを開く")
        if folder:
            self.open_paths([Path(folder)])

    def open_paths(self, paths: list[Path]) -> None:
        files: list[Path] = []
        for path in paths:
            if path.is_dir():
                files.extend(
                    sorted(
                        item
                        for item in path.rglob("*")
                        if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS
                    )
                )
            elif path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                files.append(path)

        # Stable de-duplication without reordering.
        unique: list[Path] = []
        seen: set[str] = set()
        for path in files:
            key = str(path.resolve()).lower()
            if key not in seen:
                seen.add(key)
                unique.append(path)

        if not unique:
            self.statusBar().showMessage("対応画像が見つかりませんでした", 5000)
            return

        self.paths = unique
        self.current_index = 0
        self.load_current()

    def load_current(self) -> None:
        if not (0 <= self.current_index < len(self.paths)):
            return
        path = self.paths[self.current_index]
        try:
            with Image.open(path) as source:
                # Convert through Pillow so formats such as TIFF/WEBP are reliable.
                image = source.convert("RGBA")
                raw = image.tobytes("raw", "RGBA")
                qimage = QImage(raw, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888).copy()
            self.canvas.pixmap = QPixmap.fromImage(qimage)
            self.file_label.setText(
                f"{path.name}\n{self.current_index + 1} / {len(self.paths)}\n{image.width} × {image.height} px"
            )
            self.statusBar().showMessage(str(path))
            self.canvas.update()
        except Exception as exc:
            self.file_label.setText(f"読み込み失敗: {path.name}\n{exc}")
            self.statusBar().showMessage("画像の読み込みに失敗しました", 5000)
        self._update_nav()

    def prev_image(self) -> None:
        if self.current_index > 0:
            self.current_index -= 1
            self.load_current()

    def next_image(self) -> None:
        if self.current_index + 1 < len(self.paths):
            self.current_index += 1
            self.load_current()

    def _update_nav(self) -> None:
        self.prev_button.setEnabled(self.current_index > 0)
        self.next_button.setEnabled(0 <= self.current_index < len(self.paths) - 1)

    def _refresh_canvas(self) -> None:
        self.canvas.update()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Movie Shot Analyzer")
    window = MovieShotAnalyzer()
    window.show()
    sys.exit(app.exec())
