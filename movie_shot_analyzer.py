"""Movie Shot Analyzer V3 — frame-aware composition and perspective overlays."""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea, QSlider,
    QSpinBox, QVBoxLayout, QWidget,
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def detect_frame(path: Path) -> tuple[float, float, float, float]:
    """Detect near-black letterbox/pillarbox bands; returns normalized L,T,R,B."""
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((1000, 1000))
        pixels = np.asarray(image, dtype=np.float32)
    lum = pixels.mean(axis=2)
    h, w = lum.shape
    # A band must be mostly very dark, not merely a dark scene edge.
    row_dark = (lum < 22).mean(axis=1) > 0.92
    col_dark = (lum < 22).mean(axis=0) > 0.92

    def edge_run(values, forward=True):
        indices = range(len(values)) if forward else range(len(values) - 1, -1, -1)
        run = 0
        for i in indices:
            if values[i]: run += 1
            else: break
        return run

    top, bottom = edge_run(row_dark), edge_run(row_dark, False)
    left, right = edge_run(col_dark), edge_run(col_dark, False)
    # Do not crop away more than 30% at any edge: protects genuinely dark shots.
    top = top if top < h * .30 else 0; bottom = bottom if bottom < h * .30 else 0
    left = left if left < w * .30 else 0; right = right if right < w * .30 else 0
    if top + bottom > h * .45: top = bottom = 0
    if left + right > w * .45: left = right = 0
    return left / w, top / h, 1 - right / w, 1 - bottom / h


class Canvas(QLabel):
    changed = Signal()

    def __init__(self, app):
        super().__init__("画像またはフォルダをここへドラッグ＆ドロップ\nまたは「画像を開く」を押してください")
        self.app = app; self.setAlignment(Qt.AlignmentFlag.AlignCenter); self.setAcceptDrops(True)
        self.setMinimumSize(600, 420); self.setStyleSheet("background:#16181d;color:#cbd5e1;font-size:18px;")
        self.source: QPixmap | None = None; self.image_rect = QRectF(); self.drag_target = None

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls(): event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
        self.app.open_paths(paths); event.acceptProposedAction()

    def resizeEvent(self, event):
        super().resizeEvent(event); self.update()

    def draw_image(self, painter):
        if not self.source: return
        scaled = self.source.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        x = (self.width() - scaled.width()) / 2; y = (self.height() - scaled.height()) / 2
        self.image_rect = QRectF(x, y, scaled.width(), scaled.height())
        painter.drawPixmap(self.image_rect.toRect(), self.source)

    def pos_from_norm(self, x, y):
        r = self.image_rect; return QPointF(r.left()+x*r.width(), r.top()+y*r.height())

    def norm_from_pos(self, p):
        r = self.image_rect
        return ((p.x()-r.left())/r.width(), (p.y()-r.top())/r.height())

    def frame_rect(self):
        l,t,r,b = self.app.frame
        p1=self.pos_from_norm(l,t); p2=self.pos_from_norm(r,b); return QRectF(p1,p2).normalized()

    def paintEvent(self, event):
        painter=QPainter(self); painter.fillRect(self.rect(), QColor("#16181d")); self.draw_image(painter)
        if not self.source: return
        a=self.app; fr=self.frame_rect()
        if a.show_frame.isChecked():
            pen=QPen(QColor("#f59e0b")); pen.setWidth(2); pen.setStyle(Qt.PenStyle.DashLine); painter.setPen(pen); painter.drawRect(fr)
            for x,y in ((fr.left(),fr.top()),(fr.right(),fr.top()),(fr.left(),fr.bottom()),(fr.right(),fr.bottom())): painter.drawRect(QRectF(x-4,y-4,8,8))
        color=QColor(a.guide_color); color.setAlphaF(a.guide_alpha.value()/100); pen=QPen(color, a.guide_width.value()); painter.setPen(pen)
        def vline(q): painter.drawLine(QPointF(fr.left()+fr.width()*q,fr.top()), QPointF(fr.left()+fr.width()*q,fr.bottom()))
        def hline(q): painter.drawLine(QPointF(fr.left(),fr.top()+fr.height()*q), QPointF(fr.right(),fr.top()+fr.height()*q))
        if a.thirds.isChecked():
            vline(1/3); vline(2/3); hline(1/3); hline(2/3)
            for q in a.third_v: vline(q)
            for q in a.third_h: hline(q)
        if a.cross.isChecked():
            vline(.5); hline(.5)
            for q in a.cross_v: vline(q)
            for q in a.cross_h: hline(q)
        if a.diagonal.isChecked():
            painter.drawLine(fr.topLeft(),fr.bottomRight()); painter.drawLine(fr.topRight(),fr.bottomLeft())
        if a.golden.isChecked():
            phi=.618; vline(1-phi); vline(phi); hline(1-phi); hline(phi)
        self.draw_perspective(painter, fr)

    def draw_perspective(self, painter, fr):
        a=self.app
        line_color=QColor(a.perspective_color); line_color.setAlphaF(a.perspective_alpha.value()/100)
        painter.setPen(QPen(line_color,a.perspective_width.value()))
        points=[]
        for i, vp in enumerate(a.vps):
            if vp is None: continue
            p=self.pos_from_norm(*vp); points.append(p)
            if a.show_perspective.isChecked():
                for q in (fr.topLeft(),fr.topRight(),fr.bottomLeft(),fr.bottomRight()): painter.drawLine(p,q)
            marker=QColor(a.vp_color); marker.setAlphaF(a.vp_alpha.value()/100)
            painter.setPen(QPen(marker,a.vp_border.value())); painter.setBrush(marker)
            radius=a.vp_size.value()/2; painter.drawEllipse(p,radius,radius)
            painter.setPen(QPen(line_color,a.perspective_width.value()))
        if a.eye_level.isChecked() and points:
            y=sum(p.y() for p in points)/len(points)
            eye=QColor(a.eye_color); eye.setAlphaF(a.eye_alpha.value()/100); painter.setPen(QPen(eye,a.eye_width.value()))
            painter.drawLine(QPointF(fr.left(),y),QPointF(fr.right(),y))

    def mousePressEvent(self, event):
        if not self.source or event.button()!=Qt.MouseButton.LeftButton: return
        p=event.position(); fr=self.frame_rect(); a=self.app
        # VP assignment mode has priority.
        if a.active_vp is not None:
            a.vps[a.active_vp]=self.norm_from_pos(p); a.active_vp=None; a.vp_buttons[a.active_vp if a.active_vp is not None else 0].setText("VP1を指定")
            a.refresh_vp_labels(); self.update(); return
        tolerance=8
        if a.show_frame.isChecked():
            sides=[("left",abs(p.x()-fr.left())),("right",abs(p.x()-fr.right())),("top",abs(p.y()-fr.top())),("bottom",abs(p.y()-fr.bottom()))]
            name,d=min(sides,key=lambda item:item[1])
            if d<tolerance and ((name in ("left","right") and fr.top()-tolerance<=p.y()<=fr.bottom()+tolerance) or (name in ("top","bottom") and fr.left()-tolerance<=p.x()<=fr.right()+tolerance)):
                self.drag_target=("frame",name); return
        candidates=[]
        for family, values, axis in (("third_v",a.third_v,"x"),("third_h",a.third_h,"y"),("cross_v",a.cross_v,"x"),("cross_h",a.cross_h,"y")):
            for idx,q in enumerate(values):
                coord=fr.left()+q*fr.width() if axis=="x" else fr.top()+q*fr.height()
                dist=abs((p.x() if axis=="x" else p.y())-coord)
                if dist<tolerance: candidates.append((dist,family,idx,axis))
        if candidates:
            _,family,idx,axis=min(candidates); self.drag_target=(family,idx,axis)

    def mouseMoveEvent(self,event):
        if not self.drag_target or not self.source: return
        p=event.position(); a=self.app; target=self.drag_target
        if target[0]=="frame":
            l,t,r,b=a.frame; n=self.norm_from_pos(p); side=target[1]
            if side=="left": l=max(0,min(n[0],r-.03))
            elif side=="right": r=min(1,max(n[0],l+.03))
            elif side=="top": t=max(0,min(n[1],b-.03))
            else: b=min(1,max(n[1],t+.03))
            a.frame=(l,t,r,b)
        else:
            family,idx,axis=target; fr=self.frame_rect(); value=(p.x()-fr.left())/fr.width() if axis=="x" else (p.y()-fr.top())/fr.height()
            getattr(a,family)[idx]=max(.01,min(.99,value))
        self.changed.emit(); self.update()

    def mouseReleaseEvent(self,event): self.drag_target=None


class MovieShotAnalyzer(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("Movie Shot Analyzer V3"); self.resize(1500,900)
        self.current_path=None; self.paths=[]; self.frame=(0.,0.,1.,1.); self.active_vp=None; self.vps=[None,None,None]
        self.third_v=[]; self.third_h=[]; self.cross_v=[]; self.cross_h=[]; self.guide_color="#ef4444"; self.perspective_color="#3b82f6"; self.vp_color="#facc15"; self.eye_color="#22c55e"
        root=QWidget(); self.setCentralWidget(root); layout=QHBoxLayout(root); self.canvas=Canvas(self); layout.addWidget(self.canvas,1)
        panel=QWidget(); panel.setMaximumWidth(360); controls=QVBoxLayout(panel); layout.addWidget(panel)
        buttons=QHBoxLayout(); open_btn=QPushButton("画像を開く"); open_btn.clicked.connect(self.choose_images); buttons.addWidget(open_btn)
        export_btn=QPushButton("CSV出力"); export_btn.clicked.connect(self.export_csv); buttons.addWidget(export_btn); controls.addLayout(buttons)
        self.file_label=QLabel("画像未選択"); self.file_label.setWordWrap(True); controls.addWidget(self.file_label)
        controls.addWidget(self.frame_group()); controls.addWidget(self.guide_group()); controls.addWidget(self.perspective_group()); controls.addWidget(self.display_group()); controls.addStretch()
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(panel); layout.addWidget(scroll); layout.removeWidget(panel); panel.setParent(None)

    def frame_group(self):
        box=QGroupBox("実映像フレーム（全ガイドの基準）"); lay=QVBoxLayout(box)
        self.show_frame=QCheckBox("フレーム境界を表示"); self.show_frame.setChecked(True); self.show_frame.toggled.connect(self.canvas.update); lay.addWidget(self.show_frame)
        row=QHBoxLayout(); auto=QPushButton("黒帯を自動検出"); auto.clicked.connect(self.auto_detect); reset=QPushButton("全体にリセット"); reset.clicked.connect(self.reset_frame); row.addWidget(auto); row.addWidget(reset); lay.addLayout(row)
        lay.addWidget(QLabel("境界線を直接ドラッグして4辺を調整できます。")); return box

    def guide_group(self):
        box=QGroupBox("構図ガイド"); lay=QVBoxLayout(box)
        self.thirds=QCheckBox("三分割（基本線は常に維持）"); self.cross=QCheckBox("十字（中央線は常に維持）"); self.diagonal=QCheckBox("対角線"); self.golden=QCheckBox("黄金比"); self.thirds.setChecked(True)
        for w in (self.thirds,self.cross,self.diagonal,self.golden): w.toggled.connect(self.canvas.update); lay.addWidget(w)
        lay.addWidget(QLabel("追加ガイド：各方向 最大3本（線を直接ドラッグ可）"))
        grid=QGridLayout(); grid.addWidget(QLabel("三分割"),0,0); grid.addWidget(QLabel("十字"),0,1)
        for label, attr, row in (("縦線","third_v",1),("横線","third_h",2)):
            cell=QHBoxLayout(); b=QPushButton(label+" +"); b.clicked.connect(lambda _,x=attr:self.add_guide(x)); d=QPushButton("最後を削除"); d.clicked.connect(lambda _,x=attr:self.remove_guide(x)); cell.addWidget(b); cell.addWidget(d); grid.addLayout(cell,row,0)
        for label, attr, row in (("縦線","cross_v",1),("横線","cross_h",2)):
            cell=QHBoxLayout(); b=QPushButton(label+" +"); b.clicked.connect(lambda _,x=attr:self.add_guide(x)); d=QPushButton("最後を削除"); d.clicked.connect(lambda _,x=attr:self.remove_guide(x)); cell.addWidget(b); cell.addWidget(d); grid.addLayout(cell,row,1)
        lay.addLayout(grid); self.extra_label=QLabel(); lay.addWidget(self.extra_label)
        style=QFormLayout(); self.guide_width=self.spin(1,12,2); self.guide_alpha=self.slider(10,100,80); c=QPushButton("色を選ぶ"); c.clicked.connect(lambda:self.pick_color("guide_color")); style.addRow("ガイド太さ",self.guide_width); style.addRow("ガイド透明度",self.guide_alpha); style.addRow("ガイド色",c); lay.addLayout(style); self.refresh_extra_label(); return box

    def perspective_group(self):
        box=QGroupBox("パース／アイレベル"); lay=QVBoxLayout(box); self.show_perspective=QCheckBox("VPからパースラインを表示"); self.eye_level=QCheckBox("アイレベルを表示")
        for w in (self.show_perspective,self.eye_level): w.toggled.connect(self.canvas.update); lay.addWidget(w)
        self.vp_buttons=[]
        for i in range(3):
            row=QHBoxLayout(); b=QPushButton(f"VP{i+1}を指定"); b.clicked.connect(lambda _,x=i:self.set_vp_mode(x)); clear=QPushButton("消去"); clear.clicked.connect(lambda _,x=i:self.clear_vp(x)); row.addWidget(b); row.addWidget(clear); lay.addLayout(row); self.vp_buttons.append(b)
        form=QFormLayout(); self.perspective_width=self.spin(1,12,2); self.perspective_alpha=self.slider(10,100,75); self.vp_size=self.spin(4,40,12); self.vp_border=self.spin(1,8,1); self.vp_alpha=self.slider(10,100,100); self.eye_width=self.spin(1,12,2); self.eye_alpha=self.slider(10,100,90)
        for label,attr in (("パース色","perspective_color"),("VP色","vp_color"),("アイレベル色","eye_color")):
            b=QPushButton("色を選ぶ"); b.clicked.connect(lambda _,x=attr:self.pick_color(x)); form.addRow(label,b)
        form.addRow("パース太さ",self.perspective_width); form.addRow("パース透明度",self.perspective_alpha); form.addRow("VPサイズ",self.vp_size); form.addRow("VP枠太さ",self.vp_border); form.addRow("VP透明度",self.vp_alpha); form.addRow("アイレベル太さ",self.eye_width); form.addRow("アイレベル透明度",self.eye_alpha); lay.addLayout(form); return box

    def display_group(self):
        box=QGroupBox("表示補正（元画像・ガイド計算は不変）"); form=QFormLayout(box); self.brightness=self.slider(-100,100,0); self.contrast=self.slider(-100,100,0); self.gamma=self.slider(20,300,100); self.saturation=self.slider(-100,100,0)
        for label,w in (("明るさ",self.brightness),("コントラスト",self.contrast),("ガンマ",self.gamma),("彩度",self.saturation)): form.addRow(label,w)
        reset=QPushButton("表示補正をリセット"); reset.clicked.connect(self.reset_adjustments); form.addRow(reset); return box

    def slider(self, minimum, maximum, value):
        w=QSlider(Qt.Orientation.Horizontal); w.setRange(minimum,maximum); w.setValue(value); w.valueChanged.connect(self.refresh_image); return w
    def spin(self, minimum, maximum, value):
        w=QSpinBox(); w.setRange(minimum,maximum); w.setValue(value); w.valueChanged.connect(self.canvas.update); return w
    def choose_images(self):
        names,_=QFileDialog.getOpenFileNames(self,"画像を開く","","Images (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)"); self.open_paths([Path(x) for x in names])
    def open_paths(self, paths):
        files=[]
        for p in paths:
            files.extend(sorted(x for x in p.rglob("*") if x.suffix.lower() in IMAGE_EXTENSIONS) if p.is_dir() else [p])
        files=[p for p in files if p.exists() and p.suffix.lower() in IMAGE_EXTENSIONS]
        if files: self.paths=files; self.load_image(files[0])
    def load_image(self,path):
        self.current_path=path; self.frame=(0.,0.,1.,1.); self.vps=[None,None,None]; self.file_label.setText(f"{path.name}\n{len(self.paths)} 枚読み込み")
        self.refresh_image()
    def refresh_image(self):
        if not self.current_path: return
        with Image.open(self.current_path) as source:
            im=source.convert("RGB").copy()
        arr=np.asarray(im,dtype=np.float32)/255
        arr=np.clip(arr + self.brightness.value()/255,0,1)
        contrast=1+self.contrast.value()/100; arr=np.clip((arr-.5)*contrast+.5,0,1)
        arr=np.power(arr,1/(self.gamma.value()/100))
        gray=arr.mean(axis=2,keepdims=True); arr=np.clip(gray+(arr-gray)*(1+self.saturation.value()/100),0,1)
        arr=(arr*255).astype(np.uint8); h,w,_=arr.shape; qi=QImage(arr.data,w,h,w*3,QImage.Format.Format_RGB888).copy(); self.canvas.source=QPixmap.fromImage(qi); self.canvas.update()
    def auto_detect(self):
        if self.current_path: self.frame=detect_frame(self.current_path); self.canvas.update()
    def reset_frame(self): self.frame=(0.,0.,1.,1.); self.canvas.update()
    def reset_adjustments(self):
        for x,v in ((self.brightness,0),(self.contrast,0),(self.gamma,100),(self.saturation,0)): x.setValue(v)
    def add_guide(self,attr):
        values=getattr(self,attr)
        if len(values)>=3: return
        defaults=[.25,.5,.75]; values.append(defaults[len(values)]); self.refresh_extra_label(); self.canvas.update()
    def remove_guide(self,attr):
        values=getattr(self,attr)
        if values: values.pop()
        self.refresh_extra_label(); self.canvas.update()
    def refresh_extra_label(self):
        def item(name,values): return f"{name}: " + (" / ".join(f"{round(v*100)}% [×{i+1}]" for i,v in enumerate(values)) or "なし")
        self.extra_label.setText(item("三分割 縦",self.third_v)+"\n"+item("三分割 横",self.third_h)+"\n"+item("十字 縦",self.cross_v)+"\n"+item("十字 横",self.cross_h)+"\n※ [×番号] は該当する追加線を消去")
    def mousePressEvent(self,event): super().mousePressEvent(event)
    def set_vp_mode(self,index): self.active_vp=index; self.vp_buttons[index].setText("画像上をクリック…")
    def clear_vp(self,index): self.vps[index]=None; self.vp_buttons[index].setText(f"VP{index+1}を指定"); self.canvas.update()
    def refresh_vp_labels(self):
        for i,b in enumerate(self.vp_buttons): b.setText(f"VP{i+1}を再指定" if self.vps[i] else f"VP{i+1}を指定")
    def pick_color(self,attr):
        color=QColorDialog.getColor(QColor(getattr(self,attr)),self,"色を選択")
        if color.isValid(): setattr(self,attr,color.name()); self.canvas.update()
    def export_csv(self):
        if not self.current_path: return
        destination,_=QFileDialog.getSaveFileName(self,"CSVを保存","movie_shot_analysis.csv","CSV (*.csv)")
        if not destination: return
        with open(destination,"w",newline="",encoding="utf-8-sig") as f:
            writer=csv.writer(f); writer.writerow(["image","frame_left","frame_top","frame_right","frame_bottom","vp1","vp2","vp3"]); writer.writerow([str(self.current_path),*self.frame,*["" if v is None else f"{v[0]:.5f},{v[1]:.5f}" for v in self.vps]])


if __name__ == "__main__":
    app=QApplication(sys.argv); window=MovieShotAnalyzer(); window.show(); sys.exit(app.exec())
