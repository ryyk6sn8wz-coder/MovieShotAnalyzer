from __future__ import annotations
import math, sys
from pathlib import Path
from PIL import Image, ImageEnhance
from PySide6.QtCore import QRectF, Qt, QPointF
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QMainWindow, QPushButton, QScrollArea, QSlider, QSpinBox,
    QVBoxLayout, QWidget
)

IMAGE_EXTENSIONS={'.jpg','.jpeg','.png','.bmp','.webp','.tif','.tiff'}

def pil_to_pixmap(im:Image.Image)->QPixmap:
    im=im.convert('RGBA'); raw=im.tobytes('raw','RGBA')
    q=QImage(raw,im.width,im.height,im.width*4,QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(q)

def detect_frame(im:Image.Image):
    """Conservative letterbox/pillarbox detector; returns normalized L,T,R,B."""
    g=im.convert('L'); w,h=g.size
    if w<40 or h<40:return (0.,0.,1.,1.)
    small=g.resize((min(w,700),min(h,500)))
    sw,sh=small.size; px=small.load()
    def row_score(y):
        vals=[px[x,y] for x in range(sw)]; return sum(vals)/sw, max(vals)-min(vals)
    def col_score(x):
        vals=[px[x,y] for y in range(sh)]; return sum(vals)/sh, max(vals)-min(vals)
    def dark(s): return s[0] < 18 and s[1] < 45
    top=0
    while top < int(sh*.22) and dark(row_score(top)): top+=1
    bot=sh-1
    while bot > int(sh*.78) and dark(row_score(bot)): bot-=1
    left=0
    while left < int(sw*.18) and dark(col_score(left)): left+=1
    right=sw-1
    while right > int(sw*.82) and dark(col_score(right)): right-=1
    if top < sh*.012: top=0
    if sh-1-bot < sh*.012: bot=sh-1
    if left < sw*.012: left=0
    if sw-1-right < sw*.012: right=sw-1
    return (left/sw, top/sh, (right+1)/sw, (bot+1)/sh)

def seg_intersection(a:QPointF,b:QPointF,c:QPointF,d:QPointF):
    """Return segment intersection point or None."""
    x1,y1,x2,y2=a.x(),a.y(),b.x(),b.y(); x3,y3,x4,y4=c.x(),c.y(),d.x(),d.y()
    den=(x1-x2)*(y3-y4)-(y1-y2)*(x3-x4)
    if abs(den)<1e-8:return None
    t=((x1-x3)*(y3-y4)-(y1-y3)*(x3-x4))/den
    u=-((x1-x2)*(y1-y3)-(y1-y2)*(x1-x3))/den
    if -1e-6<=t<=1+1e-6 and -1e-6<=u<=1+1e-6:
        return QPointF(x1+t*(x2-x1),y1+t*(y2-y1))
    return None

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
    def npt(self,fr:QRectF,x:float,y:float):
        return QPointF(fr.left()+fr.width()*x,fr.top()+fr.height()*y)
    def _guide_pen(self,color):
        c=QColor(color); c.setAlpha(self.owner.guide_alpha.value())
        q=QPen(c); q.setWidth(self.owner.guide_width.value()); q.setStyle(Qt.PenStyle.SolidLine); return q
    def _collect_lines(self,fr:QRectF):
        """Visible straight guides. Tuples: (a,b,color). Spiral excluded."""
        lines=[]
        def add(a,b,c): lines.append((a,b,c))
        if self.owner.show_thirds.isChecked():
            for q in (1/3,2/3):
                add(self.npt(fr,q,0),self.npt(fr,q,1),'#ff4d4f'); add(self.npt(fr,0,q),self.npt(fr,1,q),'#ff4d4f')
        if self.owner.show_cross.isChecked():
            add(self.npt(fr,.5,0),self.npt(fr,.5,1),'#44b5ff'); add(self.npt(fr,0,.5),self.npt(fr,1,.5),'#44b5ff')
        if self.owner.show_golden.isChecked():
            for q in (.381966,.618034):
                add(self.npt(fr,q,0),self.npt(fr,q,1),'#f5c542'); add(self.npt(fr,0,q),self.npt(fr,1,q),'#f5c542')
        if self.owner.show_diagonal.isChecked():
            add(fr.topLeft(),fr.bottomRight(),'#b77cff'); add(fr.topRight(),fr.bottomLeft(),'#b77cff')
        if self.owner.show_triangle.isChecked():
            add(fr.bottomLeft(),fr.topRight(),'#ff9f43')
            add(fr.topLeft(),self.npt(fr,1,.72),'#ff9f43')
            add(fr.bottomRight(),self.npt(fr,0,.28),'#ff9f43')
        if self.owner.show_symmetry.isChecked():
            add(self.npt(fr,.5,0),self.npt(fr,.5,1),'#64d8cb')
        for q in self.owner.helper_v:
            add(self.npt(fr,q,0),self.npt(fr,q,1),self.owner.helper_color)
        for q in self.owner.helper_h:
            add(self.npt(fr,0,q),self.npt(fr,1,q),self.owner.helper_color)
        for a,b in self.owner.helper_free:
            add(self.npt(fr,*a),self.npt(fr,*b),self.owner.helper_color)
        return lines
    def paintEvent(self,e):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.fillRect(self.rect(),QColor('#171a20'))
        if self.pixmap is None:
            p.setPen(QColor('#aeb7c4')); p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,"画像またはフォルダをここへドラッグ＆ドロップ\n\nまたは左の『画像を開く』『フォルダを開く』"); return
        av=self.rect().adjusted(18,18,-18,-18); sc=self.pixmap.size().scaled(av.size(),Qt.AspectRatioMode.KeepAspectRatio)
        x=av.left()+(av.width()-sc.width())/2; y=av.top()+(av.height()-sc.height())/2; self.image_rect=QRectF(x,y,sc.width(),sc.height())
        p.drawPixmap(self.image_rect.toRect(),self.pixmap)
        fr=self.frame_rect(); p.save(); p.setClipRect(fr)
        lines=self._collect_lines(fr)
        for a,b,color in lines:
            pen=self._guide_pen(color)
            if color=='#64d8cb' and self.owner.show_symmetry.isChecked(): pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen); p.drawLine(a,b)
        if self.owner.show_spiral.isChecked():
            c=QColor('#ffd166'); c.setAlpha(self.owner.guide_alpha.value()); pen=QPen(c); pen.setWidth(self.owner.guide_width.value()); p.setPen(pen)
            pts=[]; cx=fr.left()+fr.width()*.382; cy=fr.top()+fr.height()*.618; maxr=min(fr.width(),fr.height())*.62
            for i in range(180):
                th=i/179*math.pi*3.2; rad=maxr*math.exp(-.17*th); pts.append(QPointF(cx+rad*math.cos(th),cy-rad*math.sin(th)))
            p.drawPolyline(QPolygonF(pts))
        if self.owner.show_points.isChecked():
            pts=[]
            for i in range(len(lines)):
                for j in range(i+1,len(lines)):
                    pt=seg_intersection(lines[i][0],lines[i][1],lines[j][0],lines[j][1])
                    if pt is None: continue
                    # de-duplicate near-identical crossings
                    if any((pt.x()-q.x())**2+(pt.y()-q.y())**2 < 16 for q in pts): continue
                    pts.append(pt)
            fill=QColor(self.owner.point_color); fill.setAlpha(self.owner.point_alpha.value())
            outline=QColor(self.owner.point_outline_color); outline.setAlpha(self.owner.point_alpha.value())
            p.setBrush(fill); pen=QPen(outline); pen.setWidth(self.owner.point_outline_width.value()); p.setPen(pen)
            r=self.owner.point_size.value()/2
            for pt in pts[:120]: p.drawEllipse(QRectF(pt.x()-r,pt.y()-r,r*2,r*2))
        # Free helper endpoints. Show only for selected free line or while dragging it.
        sel=self.owner.selected_helper
        if sel and sel[0]=='free' and 0<=sel[1]<len(self.owner.helper_free):
            a,b=self.owner.helper_free[sel[1]]; pa=self.npt(fr,*a); pb=self.npt(fr,*b)
            p.setBrush(QColor('#ffffff')); hp=QPen(QColor(self.owner.helper_color)); hp.setWidth(2); p.setPen(hp)
            for pt in (pa,pb): p.drawEllipse(QRectF(pt.x()-6,pt.y()-6,12,12))
        p.restore()
        if self.owner.show_frame.isChecked():
            q=QPen(QColor('#7ee787'));q.setWidth(2);q.setStyle(Qt.PenStyle.DashLine);p.setPen(q);p.setBrush(Qt.BrushStyle.NoBrush);p.drawRect(fr)
    def _dist_to_segment(self,p,a,b):
        vx=b.x()-a.x(); vy=b.y()-a.y(); wx=p.x()-a.x(); wy=p.y()-a.y(); vv=vx*vx+vy*vy
        if vv<=1e-8:return math.hypot(wx,wy)
        t=max(0,min(1,(wx*vx+wy*vy)/vv)); qx=a.x()+t*vx; qy=a.y()+t*vy
        return math.hypot(p.x()-qx,p.y()-qy)
    def _hit(self,pos):
        if not self.pixmap:return None
        fr=self.frame_rect(); tol=10
        if self.owner.manual_frame.isChecked():
            for name,val in [('L',fr.left()),('R',fr.right())]:
                if abs(pos.x()-val)<tol and fr.top()-tol<=pos.y()<=fr.bottom()+tol:return ('frame',name)
            for name,val in [('T',fr.top()),('B',fr.bottom())]:
                if abs(pos.y()-val)<tol and fr.left()-tol<=pos.x()<=fr.right()+tol:return ('frame',name)
        # Free line endpoint handles first, then free line body.
        for i,(a,b) in enumerate(self.owner.helper_free):
            pa=self.npt(fr,*a); pb=self.npt(fr,*b)
            if math.hypot(pos.x()-pa.x(),pos.y()-pa.y())<12:return ('free_end',i,0)
            if math.hypot(pos.x()-pb.x(),pos.y()-pb.y())<12:return ('free_end',i,1)
            if self._dist_to_segment(pos,pa,pb)<tol:return ('free_line',i)
        for i,q in enumerate(self.owner.helper_v):
            if abs(pos.x()-(fr.left()+fr.width()*q))<tol:return ('v',i)
        for i,q in enumerate(self.owner.helper_h):
            if abs(pos.y()-(fr.top()+fr.height()*q))<tol:return ('h',i)
        return None
    def mousePressEvent(self,e):
        if e.button()!=Qt.MouseButton.LeftButton:return
        hit=self._hit(e.position()); self.drag_item=hit
        if hit:
            typ=hit[0]
            if typ in ('v','h','free_line','free_end'):
                kind='free' if typ.startswith('free') else typ
                self.owner.selected_helper=(kind,hit[1]); self.owner.update_helper_buttons(); self.update()
            if typ=='free_line':
                fr=self.frame_rect(); self._drag_start=e.position(); self._drag_orig=self.owner.helper_free[hit[1]]
    def mouseMoveEvent(self,e):
        if not self.drag_item:return
        fr=self.frame_rect(); typ=self.drag_item[0]; pos=e.position()
        if typ=='v' and fr.width()>1:
            i=self.drag_item[1]; self.owner.helper_v[i]=max(0,min(1,(pos.x()-fr.left())/fr.width()))
        elif typ=='h' and fr.height()>1:
            i=self.drag_item[1]; self.owner.helper_h[i]=max(0,min(1,(pos.y()-fr.top())/fr.height()))
        elif typ=='free_end' and fr.width()>1 and fr.height()>1:
            i,end=self.drag_item[1],self.drag_item[2]; a,b=self.owner.helper_free[i]
            np=(max(0,min(1,(pos.x()-fr.left())/fr.width())),max(0,min(1,(pos.y()-fr.top())/fr.height())))
            self.owner.helper_free[i]=(np,b) if end==0 else (a,np)
        elif typ=='free_line' and fr.width()>1 and fr.height()>1:
            i=self.drag_item[1]; a,b=self._drag_orig
            dx=(pos.x()-self._drag_start.x())/fr.width(); dy=(pos.y()-self._drag_start.y())/fr.height()
            # Clamp translation so both endpoints stay within frame.
            dx=max(-min(a[0],b[0]),min(1-max(a[0],b[0]),dx)); dy=max(-min(a[1],b[1]),min(1-max(a[1],b[1]),dy))
            self.owner.helper_free[i]=((a[0]+dx,a[1]+dy),(b[0]+dx,b[1]+dy))
        elif typ=='frame':
            idx=self.drag_item[1]; r=self.image_rect; l,t,rr,b=self.owner.frame
            if idx=='L':l=max(0,min(rr-.03,(pos.x()-r.left())/r.width()))
            elif idx=='R':rr=min(1,max(l+.03,(pos.x()-r.left())/r.width()))
            elif idx=='T':t=max(0,min(b-.03,(pos.y()-r.top())/r.height()))
            elif idx=='B':b=min(1,max(t+.03,(pos.y()-r.top())/r.height()))
            self.owner.frame=(l,t,rr,b)
        self.update()
    def mouseReleaseEvent(self,e): self.drag_item=None

class MovieShotAnalyzer(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle('Movie Shot Analyzer V4 Analyzer 2'); self.resize(1500,920); self.setMinimumSize(1050,680); self.setAcceptDrops(True)
        self.paths=[]; self.current_index=-1; self.original=None; self.frame=(0.,0.,1.,1.); self.frames={}
        self.helper_v=[]; self.helper_h=[]; self.helper_free=[]; self.selected_helper=None
        self.helper_color='#36d1ff'; self.point_color='#ffd400'; self.point_outline_color='#1a1a1a'
        self._build_ui(); self._style(); self.statusBar().showMessage('V4 Analyzer 2 — 補助線・交点表示版')
    def section(self,lay,text):
        lab=QLabel(text); lab.setObjectName('section'); lay.addWidget(lab)
    def _build_ui(self):
        root=QWidget(); self.setCentralWidget(root); outer=QHBoxLayout(root); outer.setContentsMargins(8,8,8,8); outer.setSpacing(8)
        cw=QWidget(); cw.setObjectName('controlsWidget'); c=QVBoxLayout(cw); c.setContentsMargins(12,12,12,12); c.setSpacing(7)
        title=QLabel('Movie Shot Analyzer'); title.setObjectName('appTitle'); c.addWidget(title)
        sub=QLabel('V4 Analyzer 2 / 構図・補助線版'); sub.setObjectName('subtitle'); c.addWidget(sub)
        a=QPushButton('画像を開く'); a.clicked.connect(self.choose_images); b=QPushButton('フォルダを開く'); b.clicked.connect(self.choose_folder); c.addWidget(a); c.addWidget(b)
        self.file_label=QLabel('画像未選択'); self.file_label.setWordWrap(True); self.file_label.setObjectName('fileLabel'); c.addWidget(self.file_label)
        nav=QHBoxLayout(); self.prev_button=QPushButton('◀ 前'); self.next_button=QPushButton('次 ▶'); self.prev_button.clicked.connect(self.prev_image); self.next_button.clicked.connect(self.next_image); nav.addWidget(self.prev_button); nav.addWidget(self.next_button); c.addLayout(nav)

        self.section(c,'構図ガイド')
        self.show_thirds=QCheckBox('三分割（固定）'); self.show_thirds.setChecked(True)
        self.show_cross=QCheckBox('中央十字'); self.show_golden=QCheckBox('黄金比'); self.show_spiral=QCheckBox('黄金螺旋'); self.show_diagonal=QCheckBox('対角線'); self.show_triangle=QCheckBox('三角構図'); self.show_symmetry=QCheckBox('対称軸')
        for w in (self.show_thirds,self.show_cross,self.show_golden,self.show_spiral,self.show_diagonal,self.show_triangle,self.show_symmetry): w.toggled.connect(self.refresh); c.addWidget(w)

        self.section(c,'補助線')
        row=QHBoxLayout(); av=QPushButton('＋ 縦'); ah=QPushButton('＋ 横'); af=QPushButton('＋ 自由線'); av.clicked.connect(self.add_vertical); ah.clicked.connect(self.add_horizontal); af.clicked.connect(self.add_free); row.addWidget(av); row.addWidget(ah); row.addWidget(af); c.addLayout(row)
        row=QHBoxLayout(); self.delete_helper_btn=QPushButton('選択した補助線を削除'); self.delete_helper_btn.clicked.connect(self.delete_selected_helper); self.delete_helper_btn.setEnabled(False); clear=QPushButton('補助線を全削除'); clear.clicked.connect(self.clear_helpers); row.addWidget(self.delete_helper_btn); row.addWidget(clear); c.addLayout(row)
        hint=QLabel('縦・横線は線自体をドラッグ。自由線は両端の○で角度変更、線自体のドラッグで平行移動。\n三分割の赤線は固定です。'); hint.setObjectName('note'); hint.setWordWrap(True); c.addWidget(hint)

        self.section(c,'ガイド表示')
        grid=QGridLayout(); grid.addWidget(QLabel('線の太さ'),0,0); self.guide_width=QSpinBox(); self.guide_width.setRange(1,10); self.guide_width.setValue(2); self.guide_width.valueChanged.connect(self.refresh); grid.addWidget(self.guide_width,0,1)
        grid.addWidget(QLabel('線の透明度'),1,0); self.guide_alpha=QSlider(Qt.Orientation.Horizontal); self.guide_alpha.setRange(40,255); self.guide_alpha.setValue(230); self.guide_alpha.valueChanged.connect(self.refresh); grid.addWidget(self.guide_alpha,1,1); c.addLayout(grid)
        self.show_points=QCheckBox('交点に塗りつぶし○を表示'); self.show_points.setChecked(True); self.show_points.toggled.connect(self.refresh); c.addWidget(self.show_points)
        grid=QGridLayout(); grid.addWidget(QLabel('○サイズ'),0,0); self.point_size=QSpinBox(); self.point_size.setRange(4,30); self.point_size.setValue(10); self.point_size.valueChanged.connect(self.refresh); grid.addWidget(self.point_size,0,1)
        grid.addWidget(QLabel('○外周の太さ'),1,0); self.point_outline_width=QSpinBox(); self.point_outline_width.setRange(0,8); self.point_outline_width.setValue(2); self.point_outline_width.valueChanged.connect(self.refresh); grid.addWidget(self.point_outline_width,1,1); c.addLayout(grid)
        row=QHBoxLayout(); pc=QPushButton('○の色'); pc.clicked.connect(self.choose_point_color); po=QPushButton('○の外周色'); po.clicked.connect(self.choose_point_outline_color); row.addWidget(pc); row.addWidget(po); c.addLayout(row)
        row=QHBoxLayout(); row.addWidget(QLabel('○透明度')); self.point_alpha=QSlider(Qt.Orientation.Horizontal); self.point_alpha.setRange(40,255); self.point_alpha.setValue(255); self.point_alpha.valueChanged.connect(self.refresh); row.addWidget(self.point_alpha,1); c.addLayout(row)

        self.section(c,'実映像フレーム')
        self.show_frame=QCheckBox('フレーム枠を表示'); self.show_frame.setChecked(True); self.manual_frame=QCheckBox('4辺をドラッグ調整'); self.manual_frame.setChecked(True); self.show_frame.toggled.connect(self.refresh); c.addWidget(self.show_frame); c.addWidget(self.manual_frame)
        row=QHBoxLayout(); auto=QPushButton('黒帯を自動検出'); auto.clicked.connect(self.auto_frame); reset=QPushButton('画像全体に戻す'); reset.clicked.connect(self.reset_frame); row.addWidget(auto); row.addWidget(reset); c.addLayout(row)

        self.section(c,'表示補正（元画像は変更しません）')
        self.sliders={}
        for key,label,lo,hi,val in [('brightness','明るさ',50,150,100),('contrast','コントラスト',50,150,100),('gamma','ガンマ',50,200,100),('saturation','彩度',0,200,100)]:
            row=QHBoxLayout(); row.addWidget(QLabel(label)); s=QSlider(Qt.Orientation.Horizontal); s.setRange(lo,hi); s.setValue(val); v=QLabel(str(val)); v.setFixedWidth(32); s.valueChanged.connect(lambda n,k=key,vl=v:(vl.setText(str(n)),self.update_display())); row.addWidget(s,1); row.addWidget(v); c.addLayout(row); self.sliders[key]=s
        resetdisp=QPushButton('表示補正をリセット'); resetdisp.clicked.connect(self.reset_display); c.addWidget(resetdisp); c.addStretch(1)
        scroll=QScrollArea(); scroll.setObjectName('controlScroll'); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); scroll.setWidget(cw); scroll.setMinimumWidth(325); scroll.setMaximumWidth(385); outer.addWidget(scroll,0)
        self.canvas=ImageCanvas(self); outer.addWidget(self.canvas,1); self._update_nav()
    def _style(self):
        self.setStyleSheet('''QMainWindow,QWidget{background:#20242b;color:#e8edf3}#controlsWidget{background:#20242b}#controlScroll{border:1px solid #343a43;background:#20242b}#appTitle{font-size:20px;font-weight:700}#subtitle,#note{color:#aeb7c4}#section{font-size:14px;font-weight:700;color:#d9e2ec;margin-top:8px;border-top:1px solid #3b424d;padding-top:8px}#fileLabel{background:#171a20;border:1px solid #343a43;border-radius:5px;padding:8px}QPushButton{background:#303641;border:1px solid #48505d;border-radius:5px;padding:7px}QPushButton:hover{background:#3a424f}QPushButton:disabled{color:#69717c;background:#272b32}QCheckBox{padding:3px 1px}QSpinBox{background:#171a20;border:1px solid #48505d;padding:4px}QSlider::groove:horizontal{height:4px;background:#3b424d}QSlider::handle:horizontal{width:14px;margin:-5px 0;background:#8ab4f8;border-radius:7px}QScrollBar:vertical{background:#20242b;width:12px}QScrollBar::handle:vertical{background:#4a5260;min-height:28px;border-radius:5px}QStatusBar{background:#171a20;color:#aeb7c4}''')
    def dragEnterEvent(self,e):
        if e.mimeData().hasUrls():e.acceptProposedAction()
    def dropEvent(self,e): self.open_paths([Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]); e.acceptProposedAction()
    def choose_images(self):
        n,_=QFileDialog.getOpenFileNames(self,'画像を開く','','Images (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)')
        if n:self.open_paths([Path(x) for x in n])
    def choose_folder(self):
        f=QFileDialog.getExistingDirectory(self,'画像フォルダを開く')
        if f:self.open_paths([Path(f)])
    def open_paths(self,paths):
        files=[]
        for p in paths:
            if p.is_dir(): files.extend(sorted(x for x in p.rglob('*') if x.is_file() and x.suffix.lower() in IMAGE_EXTENSIONS))
            elif p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS: files.append(p)
        uniq=[]; seen=set()
        for p in files:
            k=str(p.resolve()).lower()
            if k not in seen: seen.add(k); uniq.append(p)
        if not uniq:self.statusBar().showMessage('対応画像が見つかりませんでした',5000); return
        self.paths=uniq; self.current_index=0; self.load_current()
    def load_current(self):
        if not(0<=self.current_index<len(self.paths)):return
        p=self.paths[self.current_index]
        try:
            with Image.open(p) as src:self.original=src.convert('RGB').copy()
            self.frame=self.frames.get(str(p),(0.,0.,1.,1.)); self.update_display(); self.file_label.setText(f'{p.name}\n{self.current_index+1} / {len(self.paths)}\n{self.original.width} × {self.original.height} px'); self.statusBar().showMessage(str(p))
        except Exception as ex:self.file_label.setText(f'読み込み失敗: {p.name}\n{ex}')
        self._update_nav()
    def update_display(self):
        if self.original is None:return
        im=self.original.copy(); im=ImageEnhance.Brightness(im).enhance(self.sliders['brightness'].value()/100); im=ImageEnhance.Contrast(im).enhance(self.sliders['contrast'].value()/100); im=ImageEnhance.Color(im).enhance(self.sliders['saturation'].value()/100)
        gam=self.sliders['gamma'].value()/100
        if abs(gam-1)>0.001:
            inv=1/gam; lut=[min(255,int((i/255)**inv*255+.5)) for i in range(256)]; im=im.point(lut*3)
        self.canvas.pixmap=pil_to_pixmap(im); self.canvas.update()
    def reset_display(self):
        for k in self.sliders:self.sliders[k].setValue(100)
    def auto_frame(self):
        if self.original is None:return
        self.frame=detect_frame(self.original); self.save_frame(); self.canvas.update(); self.statusBar().showMessage('黒帯フレームを自動検出しました。必要なら緑の4辺をドラッグしてください。',5000)
    def reset_frame(self): self.frame=(0.,0.,1.,1.); self.save_frame(); self.canvas.update()
    def save_frame(self):
        if 0<=self.current_index<len(self.paths): self.frames[str(self.paths[self.current_index])]=self.frame
    def prev_image(self):
        self.save_frame()
        if self.current_index>0:self.current_index-=1;self.load_current()
    def next_image(self):
        self.save_frame()
        if self.current_index+1<len(self.paths):self.current_index+=1;self.load_current()
    def _update_nav(self): self.prev_button.setEnabled(self.current_index>0); self.next_button.setEnabled(0<=self.current_index<len(self.paths)-1)
    def refresh(self): self.canvas.update()

    def add_vertical(self):
        if len(self.helper_v)>=6:self.statusBar().showMessage('縦補助線は最大6本です',3000); return
        q=.5 if not self.helper_v else min(.9,.15+.12*len(self.helper_v)); self.helper_v.append(q); self.selected_helper=('v',len(self.helper_v)-1); self.update_helper_buttons(); self.refresh()
    def add_horizontal(self):
        if len(self.helper_h)>=6:self.statusBar().showMessage('横補助線は最大6本です',3000); return
        q=.5 if not self.helper_h else min(.9,.15+.12*len(self.helper_h)); self.helper_h.append(q); self.selected_helper=('h',len(self.helper_h)-1); self.update_helper_buttons(); self.refresh()
    def add_free(self):
        if len(self.helper_free)>=6:self.statusBar().showMessage('自由補助線は最大6本です',3000); return
        off=.04*len(self.helper_free); self.helper_free.append(((.2,.25+off),(.8,.75+off))); self.selected_helper=('free',len(self.helper_free)-1); self.update_helper_buttons(); self.refresh()
    def delete_selected_helper(self):
        if not self.selected_helper:return
        kind,i=self.selected_helper
        arr={'v':self.helper_v,'h':self.helper_h,'free':self.helper_free}.get(kind)
        if arr is not None and 0<=i<len(arr): arr.pop(i)
        self.selected_helper=None; self.update_helper_buttons(); self.refresh()
    def clear_helpers(self):
        self.helper_v.clear(); self.helper_h.clear(); self.helper_free.clear(); self.selected_helper=None; self.update_helper_buttons(); self.refresh()
    def update_helper_buttons(self): self.delete_helper_btn.setEnabled(self.selected_helper is not None)
    def choose_point_color(self):
        c=QColorDialog.getColor(QColor(self.point_color),self,'交点○の塗り色')
        if c.isValid(): self.point_color=c.name(); self.refresh()
    def choose_point_outline_color(self):
        c=QColorDialog.getColor(QColor(self.point_outline_color),self,'交点○の外周色')
        if c.isValid(): self.point_outline_color=c.name(); self.refresh()

if __name__=='__main__':
    app=QApplication(sys.argv); app.setApplicationName('Movie Shot Analyzer'); w=MovieShotAnalyzer(); w.show(); sys.exit(app.exec())
