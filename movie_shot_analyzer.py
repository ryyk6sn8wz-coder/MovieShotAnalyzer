from __future__ import annotations
import math, sys
from pathlib import Path
from PIL import Image, ImageEnhance
from PySide6.QtCore import QRectF, Qt, QPointF
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QApplication,QCheckBox,QFileDialog,QFrame,QGridLayout,QHBoxLayout,QLabel,QMainWindow,QPushButton,QScrollArea,QSlider,QSpinBox,QVBoxLayout,QWidget)

IMAGE_EXTENSIONS={'.jpg','.jpeg','.png','.bmp','.webp','.tif','.tiff'}

def pil_to_pixmap(im:Image.Image)->QPixmap:
    im=im.convert('RGBA'); raw=im.tobytes('raw','RGBA')
    q=QImage(raw,im.width,im.height,im.width*4,QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(q)

def detect_frame(im:Image.Image):
    """Conservative letterbox/pillarbox detector; returns normalized L,T,R,B."""
    g=im.convert('L'); w,h=g.size
    if w<40 or h<40:return (0.,0.,1.,1.)
    # Downsample for speed and inspect edge strips. A bar must stay very dark and low-variance.
    small=g.resize((min(w,700),min(h,500)))
    sw,sh=small.size; px=small.load()
    def row_score(y):
        vals=[px[x,y] for x in range(sw)]
        return sum(vals)/sw, max(vals)-min(vals)
    def col_score(x):
        vals=[px[x,y] for y in range(sh)]
        return sum(vals)/sh, max(vals)-min(vals)
    def dark(s): return s[0] < 18 and s[1] < 45
    top=0
    while top < int(sh*.22) and dark(row_score(top)): top+=1
    bot=sh-1
    while bot > int(sh*.78) and dark(row_score(bot)): bot-=1
    left=0
    while left < int(sw*.18) and dark(col_score(left)): left+=1
    right=sw-1
    while right > int(sw*.82) and dark(col_score(right)): right-=1
    # Ignore tiny detections; they are often naturally dark image edges.
    if top < sh*.012: top=0
    if sh-1-bot < sh*.012: bot=sh-1
    if left < sw*.012: left=0
    if sw-1-right < sw*.012: right=sw-1
    return (left/sw, top/sh, (right+1)/sw, (bot+1)/sh)

class ImageCanvas(QWidget):
    def __init__(self,owner):
        super().__init__(); self.owner=owner; self.pixmap=None; self.image_rect=QRectF(); self.drag_item=None
        self.setAcceptDrops(True); self.setMinimumSize(640,420); self.setMouseTracking(True)
    def dragEnterEvent(self,e):
        if e.mimeData().hasUrls():e.acceptProposedAction()
    def dropEvent(self,e):
        self.owner.open_paths([Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]); e.acceptProposedAction()
    def frame_rect(self):
        r=self.image_rect; l,t,rr,b=self.owner.frame
        return QRectF(r.left()+r.width()*l,r.top()+r.height()*t,r.width()*(rr-l),r.height()*(b-t))
    def paintEvent(self,e):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.fillRect(self.rect(),QColor('#171a20'))
        if self.pixmap is None:
            p.setPen(QColor('#aeb7c4')); p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,"画像またはフォルダをここへドラッグ＆ドロップ\n\nまたは左の『画像を開く』『フォルダを開く』") ; return
        av=self.rect().adjusted(18,18,-18,-18); sc=self.pixmap.size().scaled(av.size(),Qt.AspectRatioMode.KeepAspectRatio)
        x=av.left()+(av.width()-sc.width())/2; y=av.top()+(av.height()-sc.height())/2; self.image_rect=QRectF(x,y,sc.width(),sc.height())
        p.drawPixmap(self.image_rect.toRect(),self.pixmap)
        fr=self.frame_rect(); p.save(); p.setClipRect(fr)
        def pen(c,w=2,style=Qt.PenStyle.SolidLine):
            q=QPen(QColor(c));q.setWidth(w);q.setStyle(style);p.setPen(q)
        if self.owner.show_thirds.isChecked():
            pen('#ff4d4f');
            for q in (1/3,2/3):
                xx=fr.left()+fr.width()*q; yy=fr.top()+fr.height()*q
                p.drawLine(int(xx),int(fr.top()),int(xx),int(fr.bottom()));p.drawLine(int(fr.left()),int(yy),int(fr.right()),int(yy))
        if self.owner.show_cross.isChecked():
            pen('#44b5ff'); c=fr.center();p.drawLine(int(c.x()),int(fr.top()),int(c.x()),int(fr.bottom()));p.drawLine(int(fr.left()),int(c.y()),int(fr.right()),int(c.y()))
        if self.owner.show_golden.isChecked():
            pen('#f5c542'); phi=.381966
            for q in (phi,1-phi):
                xx=fr.left()+fr.width()*q;yy=fr.top()+fr.height()*q;p.drawLine(int(xx),int(fr.top()),int(xx),int(fr.bottom()));p.drawLine(int(fr.left()),int(yy),int(fr.right()),int(yy))
        if self.owner.show_diagonal.isChecked():
            pen('#b77cff');p.drawLine(fr.topLeft(),fr.bottomRight());p.drawLine(fr.topRight(),fr.bottomLeft())
        if self.owner.show_triangle.isChecked():
            pen('#ff9f43');p.drawLine(fr.bottomLeft(),fr.topRight());p.drawLine(fr.topLeft(),QPointF(fr.right(),fr.top()+fr.height()*.72));p.drawLine(fr.bottomRight(),QPointF(fr.left(),fr.top()+fr.height()*.28))
        if self.owner.show_symmetry.isChecked():
            pen('#64d8cb',2,Qt.PenStyle.DashLine);c=fr.center();p.drawLine(int(c.x()),int(fr.top()),int(c.x()),int(fr.bottom()))
        if self.owner.show_spiral.isChecked():
            pen('#ffd166'); pts=[]
            # practical golden spiral visual approximation, centered on golden section
            cx=fr.left()+fr.width()*.382; cy=fr.top()+fr.height()*.618
            maxr=min(fr.width(),fr.height())*.62
            for i in range(180):
                th=i/179*math.pi*3.2; rad=maxr*math.exp(-.17*th); pts.append(QPointF(cx+rad*math.cos(th),cy-rad*math.sin(th)))
            p.drawPolyline(QPolygonF(pts))
        # Free helper lines, independent of fixed thirds/cross.
        pen('#36d1ff',2)
        for q in self.owner.helper_v[:self.owner.helper_v_count.value()]:
            xx=fr.left()+fr.width()*q;p.drawLine(int(xx),int(fr.top()),int(xx),int(fr.bottom()))
        for q in self.owner.helper_h[:self.owner.helper_h_count.value()]:
            yy=fr.top()+fr.height()*q;p.drawLine(int(fr.left()),int(yy),int(fr.right()),int(yy))
        p.restore()
        if self.owner.show_frame.isChecked():
            pen('#7ee787',2,Qt.PenStyle.DashLine);p.drawRect(fr)
    def _hit(self,pos):
        if not self.pixmap:return None
        fr=self.frame_rect(); tol=9
        # frame edges first
        if self.owner.manual_frame.isChecked():
            for name,val in [('L',fr.left()),('R',fr.right())]:
                if abs(pos.x()-val)<tol and fr.top()-tol<=pos.y()<=fr.bottom()+tol:return ('frame',name)
            for name,val in [('T',fr.top()),('B',fr.bottom())]:
                if abs(pos.y()-val)<tol and fr.left()-tol<=pos.x()<=fr.right()+tol:return ('frame',name)
        for i,q in enumerate(self.owner.helper_v[:self.owner.helper_v_count.value()]):
            if abs(pos.x()-(fr.left()+fr.width()*q))<tol:return ('v',i)
        for i,q in enumerate(self.owner.helper_h[:self.owner.helper_h_count.value()]):
            if abs(pos.y()-(fr.top()+fr.height()*q))<tol:return ('h',i)
        return None
    def mousePressEvent(self,e):
        if e.button()==Qt.MouseButton.LeftButton:self.drag_item=self._hit(e.position())
    def mouseMoveEvent(self,e):
        if not self.drag_item:return
        fr=self.frame_rect(); typ,idx=self.drag_item; pos=e.position()
        if typ=='v' and fr.width()>1:self.owner.helper_v[idx]=max(0,min(1,(pos.x()-fr.left())/fr.width()))
        elif typ=='h' and fr.height()>1:self.owner.helper_h[idx]=max(0,min(1,(pos.y()-fr.top())/fr.height()))
        elif typ=='frame':
            r=self.image_rect; l,t,rr,b=self.owner.frame
            if idx=='L':l=max(0,min(rr-.03,(pos.x()-r.left())/r.width()))
            elif idx=='R':rr=min(1,max(l+.03,(pos.x()-r.left())/r.width()))
            elif idx=='T':t=max(0,min(b-.03,(pos.y()-r.top())/r.height()))
            elif idx=='B':b=min(1,max(t+.03,(pos.y()-r.top())/r.height()))
            self.owner.frame=(l,t,rr,b)
        self.update()
    def mouseReleaseEvent(self,e):self.drag_item=None

class MovieShotAnalyzer(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle('Movie Shot Analyzer V4 Analyzer 1');self.resize(1500,920);self.setMinimumSize(1050,680);self.setAcceptDrops(True)
        self.paths=[];self.current_index=-1;self.original=None;self.frame=(0.,0.,1.,1.);self.frames={};self.helper_v=[.2,.5,.8];self.helper_h=[.2,.5,.8]
        self._build_ui();self._style();self.statusBar().showMessage('V4 Analyzer 1 — 構図・フレーム確認版')
    def section(self,lay,text):
        lab=QLabel(text);lab.setObjectName('section');lay.addWidget(lab)
    def _build_ui(self):
        root=QWidget();self.setCentralWidget(root);outer=QHBoxLayout(root);outer.setContentsMargins(8,8,8,8);outer.setSpacing(8)
        cw=QWidget();cw.setObjectName('controlsWidget');c=QVBoxLayout(cw);c.setContentsMargins(12,12,12,12);c.setSpacing(7)
        title=QLabel('Movie Shot Analyzer');title.setObjectName('appTitle');c.addWidget(title);sub=QLabel('V4 Analyzer 1 / 構図・フレーム版');sub.setObjectName('subtitle');c.addWidget(sub)
        a=QPushButton('画像を開く');a.clicked.connect(self.choose_images);b=QPushButton('フォルダを開く');b.clicked.connect(self.choose_folder);c.addWidget(a);c.addWidget(b)
        self.file_label=QLabel('画像未選択');self.file_label.setWordWrap(True);self.file_label.setObjectName('fileLabel');c.addWidget(self.file_label)
        nav=QHBoxLayout();self.prev_button=QPushButton('◀ 前');self.next_button=QPushButton('次 ▶');self.prev_button.clicked.connect(self.prev_image);self.next_button.clicked.connect(self.next_image);nav.addWidget(self.prev_button);nav.addWidget(self.next_button);c.addLayout(nav)
        self.section(c,'構図ガイド')
        self.show_thirds=QCheckBox('三分割（固定）');self.show_thirds.setChecked(True);self.show_cross=QCheckBox('中央十字');self.show_golden=QCheckBox('黄金比');self.show_spiral=QCheckBox('黄金螺旋');self.show_diagonal=QCheckBox('対角線');self.show_triangle=QCheckBox('三角構図');self.show_symmetry=QCheckBox('対称軸')
        for w in (self.show_thirds,self.show_cross,self.show_golden,self.show_spiral,self.show_diagonal,self.show_triangle,self.show_symmetry):w.toggled.connect(self.refresh);c.addWidget(w)
        grid=QGridLayout();grid.addWidget(QLabel('追加 縦線'),0,0);self.helper_v_count=QSpinBox();self.helper_v_count.setRange(0,3);grid.addWidget(self.helper_v_count,0,1);grid.addWidget(QLabel('追加 横線'),1,0);self.helper_h_count=QSpinBox();self.helper_h_count.setRange(0,3);grid.addWidget(self.helper_h_count,1,1);self.helper_v_count.valueChanged.connect(self.refresh);self.helper_h_count.valueChanged.connect(self.refresh);c.addLayout(grid)
        hint=QLabel('追加線は水色。画像上で直接ドラッグできます。\n三分割の赤線は固定で動きません。');hint.setObjectName('note');hint.setWordWrap(True);c.addWidget(hint)
        self.section(c,'実映像フレーム')
        self.show_frame=QCheckBox('フレーム枠を表示');self.show_frame.setChecked(True);self.manual_frame=QCheckBox('4辺をドラッグ調整');self.manual_frame.setChecked(True);self.show_frame.toggled.connect(self.refresh);c.addWidget(self.show_frame);c.addWidget(self.manual_frame)
        row=QHBoxLayout();auto=QPushButton('黒帯を自動検出');auto.clicked.connect(self.auto_frame);reset=QPushButton('画像全体に戻す');reset.clicked.connect(self.reset_frame);row.addWidget(auto);row.addWidget(reset);c.addLayout(row)
        self.section(c,'表示補正（元画像は変更しません）')
        self.sliders={}
        for key,label,lo,hi,val in [('brightness','明るさ',50,150,100),('contrast','コントラスト',50,150,100),('gamma','ガンマ',50,200,100),('saturation','彩度',0,200,100)]:
            row=QHBoxLayout();row.addWidget(QLabel(label));s=QSlider(Qt.Orientation.Horizontal);s.setRange(lo,hi);s.setValue(val);v=QLabel(str(val));v.setFixedWidth(32);s.valueChanged.connect(lambda n,k=key,vl=v:(vl.setText(str(n)),self.update_display()));row.addWidget(s,1);row.addWidget(v);c.addLayout(row);self.sliders[key]=s
        resetdisp=QPushButton('表示補正をリセット');resetdisp.clicked.connect(self.reset_display);c.addWidget(resetdisp)
        c.addStretch(1)
        scroll=QScrollArea();scroll.setObjectName('controlScroll');scroll.setWidgetResizable(True);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);scroll.setWidget(cw);scroll.setMinimumWidth(310);scroll.setMaximumWidth(365);outer.addWidget(scroll,0)
        self.canvas=ImageCanvas(self);outer.addWidget(self.canvas,1);self._update_nav()
    def _style(self):
        self.setStyleSheet('''QMainWindow,QWidget{background:#20242b;color:#e8edf3}#controlsWidget{background:#20242b}#controlScroll{border:1px solid #343a43;background:#20242b}#appTitle{font-size:20px;font-weight:700}#subtitle,#note{color:#aeb7c4}#section{font-size:14px;font-weight:700;color:#d9e2ec;margin-top:8px;border-top:1px solid #3b424d;padding-top:8px}#fileLabel{background:#171a20;border:1px solid #343a43;border-radius:5px;padding:8px}QPushButton{background:#303641;border:1px solid #48505d;border-radius:5px;padding:7px}QPushButton:hover{background:#3a424f}QPushButton:disabled{color:#69717c;background:#272b32}QCheckBox{padding:3px 1px}QSpinBox{background:#171a20;border:1px solid #48505d;padding:4px}QSlider::groove:horizontal{height:4px;background:#3b424d}QSlider::handle:horizontal{width:14px;margin:-5px 0;background:#8ab4f8;border-radius:7px}QScrollBar:vertical{background:#20242b;width:12px}QScrollBar::handle:vertical{background:#4a5260;min-height:28px;border-radius:5px}QStatusBar{background:#171a20;color:#aeb7c4}''')
    def dragEnterEvent(self,e):
        if e.mimeData().hasUrls():e.acceptProposedAction()
    def dropEvent(self,e):self.open_paths([Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]);e.acceptProposedAction()
    def choose_images(self):
        n,_=QFileDialog.getOpenFileNames(self,'画像を開く','','Images (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)');
        if n:self.open_paths([Path(x) for x in n])
    def choose_folder(self):
        f=QFileDialog.getExistingDirectory(self,'画像フォルダを開く');
        if f:self.open_paths([Path(f)])
    def open_paths(self,paths):
        files=[]
        for p in paths:
            if p.is_dir():files.extend(sorted(x for x in p.rglob('*') if x.is_file() and x.suffix.lower() in IMAGE_EXTENSIONS))
            elif p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:files.append(p)
        uniq=[];seen=set()
        for p in files:
            k=str(p.resolve()).lower()
            if k not in seen:seen.add(k);uniq.append(p)
        if not uniq:self.statusBar().showMessage('対応画像が見つかりませんでした',5000);return
        self.paths=uniq;self.current_index=0;self.load_current()
    def load_current(self):
        if not(0<=self.current_index<len(self.paths)):return
        p=self.paths[self.current_index]
        try:
            with Image.open(p) as src:self.original=src.convert('RGB').copy()
            self.frame=self.frames.get(str(p),(0.,0.,1.,1.));self.update_display();self.file_label.setText(f'{p.name}\n{self.current_index+1} / {len(self.paths)}\n{self.original.width} × {self.original.height} px');self.statusBar().showMessage(str(p))
        except Exception as ex:self.file_label.setText(f'読み込み失敗: {p.name}\n{ex}')
        self._update_nav()
    def update_display(self):
        if self.original is None:return
        im=self.original.copy();im=ImageEnhance.Brightness(im).enhance(self.sliders['brightness'].value()/100);im=ImageEnhance.Contrast(im).enhance(self.sliders['contrast'].value()/100);im=ImageEnhance.Color(im).enhance(self.sliders['saturation'].value()/100)
        gam=self.sliders['gamma'].value()/100
        if abs(gam-1)>0.001:
            inv=1/gam;lut=[min(255,int((i/255)**inv*255+.5)) for i in range(256)];im=im.point(lut*3)
        self.canvas.pixmap=pil_to_pixmap(im);self.canvas.update()
    def reset_display(self):
        for k in self.sliders:self.sliders[k].setValue(100)
    def auto_frame(self):
        if self.original is None:return
        self.frame=detect_frame(self.original);self.save_frame();self.canvas.update();self.statusBar().showMessage('黒帯フレームを自動検出しました。必要なら緑の4辺をドラッグしてください。',5000)
    def reset_frame(self):self.frame=(0.,0.,1.,1.);self.save_frame();self.canvas.update()
    def save_frame(self):
        if 0<=self.current_index<len(self.paths):self.frames[str(self.paths[self.current_index])]=self.frame
    def prev_image(self):
        self.save_frame()
        if self.current_index>0:self.current_index-=1;self.load_current()
    def next_image(self):
        self.save_frame()
        if self.current_index+1<len(self.paths):self.current_index+=1;self.load_current()
    def _update_nav(self):self.prev_button.setEnabled(self.current_index>0);self.next_button.setEnabled(0<=self.current_index<len(self.paths)-1)
    def refresh(self):self.canvas.update()

if __name__=='__main__':
    app=QApplication(sys.argv);app.setApplicationName('Movie Shot Analyzer');w=MovieShotAnalyzer();w.show();sys.exit(app.exec())
