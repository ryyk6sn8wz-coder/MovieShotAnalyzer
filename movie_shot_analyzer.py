from __future__ import annotations
import math, sys, json, os
from concurrent.futures import ThreadPoolExecutor
import random
from pathlib import Path
from PIL import Image, ImageEnhance
try:
    import cv2
    import numpy as np
except Exception:
    cv2=None
    np=None
# V5.22 perspective-family tuning constants
AUTO_DEFAULT_RAYS = 8
VP3_VERTICAL_PARALLEL_DEG = 3.5
VP3_MIN_VERTICAL_SUPPORT = 5
VP3_MIN_VERTICAL_SPREAD = 0.40
INSIDE_VP_LOCALITY_PENALTY = 0.55
FAMILY_MIN_ANGLE_SEPARATION_DEG = 12.0
PERSON_REGION_WEIGHT = 0.18
STRUCTURAL_LONG_LINE_BONUS = 1.55
LOW_CONFIDENCE_VP_THRESHOLD = 0.30
EYE_LEVEL_FORCE_HORIZONTAL = True

from PySide6.QtCore import QRectF, Qt, QPointF, QEvent, QTimer
from PySide6.QtGui import QColor, QCursor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QMainWindow, QPushButton, QScrollArea, QSlider, QDoubleSpinBox, QLineEdit,
    QVBoxLayout, QWidget, QTabWidget, QMessageBox, QSizePolicy
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

def infinite_line_intersection(a,b,c,d):
    """Intersection of two infinite lines in normalized image coordinates."""
    x1,y1=a; x2,y2=b; x3,y3=c; x4,y4=d
    den=(x1-x2)*(y3-y4)-(y1-y2)*(x3-x4)
    if abs(den)<1e-9:return None
    px=((x1*y2-y1*x2)*(x3-x4)-(x1-x2)*(x3*y4-y3*x4))/den
    py=((x1*y2-y1*x2)*(y3-y4)-(y1-y2)*(x3*y4-y3*x4))/den
    return (px,py)

class StepControl(QWidget):
    """Minus/value/plus control with reliable 0.5 steps on Windows."""
    def __init__(self, minimum, maximum, value, step=0.5, decimals=1, parent=None):
        super().__init__(parent)
        lay=QHBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(4)
        self.minus=QPushButton('−'); self.minus.setFixedWidth(34)
        self.value=QDoubleSpinBox(); self.value.setRange(minimum,maximum); self.value.setDecimals(decimals); self.value.setSingleStep(step); self.value.setValue(value)
        self.value.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.plus=QPushButton('+'); self.plus.setFixedWidth(34)
        lay.addWidget(self.minus); lay.addWidget(self.value,1); lay.addWidget(self.plus)
        self.minus.clicked.connect(lambda: self.value.setValue(max(minimum,self.value.value()-step)))
        self.plus.clicked.connect(lambda: self.value.setValue(min(maximum,self.value.value()+step)))
    def val(self): return float(self.value.value())
    def setValue(self,v): self.value.setValue(v)

class ImageCanvas(QWidget):
    def __init__(self,owner):
        super().__init__(); self.owner=owner; self.pixmap=None; self.image_rect=QRectF(); self.drag_item=None
        self.setAcceptDrops(True); self.setMinimumSize(640,420); self.setMouseTracking(True); self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    def toggle_left_panel(self):
        if not hasattr(self,'left_panel'): return
        visible=self.left_panel.isVisible()
        self.left_panel.setVisible(not visible)
        self.left_toggle.setText('›' if visible else '‹')
        self.left_toggle.setToolTip('左パネルを表示' if visible else '左パネルを折りたたむ')

    def toggle_right_panel(self):
        if not hasattr(self,'right_panel'): return
        visible=self.right_panel.isVisible()
        self.right_panel.setVisible(not visible)
        self.right_toggle.setText('‹' if visible else '›')
        self.right_toggle.setToolTip('右パネルを表示' if visible else '右パネルを折りたたむ')

    def toggle_focus_view(self):
        # F toggles an image-first view while preserving each panel's previous state.
        if not hasattr(self,'_focus_view_on'): self._focus_view_on=False
        if not self._focus_view_on:
            self._focus_prev=(self.left_panel.isVisible(),self.right_panel.isVisible())
            self.left_panel.hide(); self.right_panel.hide(); self.left_toggle.setText('›'); self.right_toggle.setText('‹')
            self._focus_view_on=True; self.statusBar().showMessage('画像優先表示：Fで元に戻します',2500)
        else:
            lv,rv=getattr(self,'_focus_prev',(True,True)); self.left_panel.setVisible(lv); self.right_panel.setVisible(rv)
            self.left_toggle.setText('‹' if lv else '›'); self.right_toggle.setText('›' if rv else '‹')
            self._focus_view_on=False

    def keyPressEvent(self,e):
        # The canvas often owns keyboard focus. Forward navigation to the main window.
        key=e.key(); focus=QApplication.focusWidget()
        editing=isinstance(focus,(QDoubleSpinBox,QLineEdit,QSlider))
        if key==Qt.Key.Key_Left and not editing:
            self.owner.prev_image(); e.accept(); return
        if key==Qt.Key.Key_Right and not editing:
            self.owner.next_image(); e.accept(); return
        if key==Qt.Key.Key_F and not editing:
            self.owner.toggle_focus_view(); e.accept(); return
        super().keyPressEvent(e)

    def dragEnterEvent(self,e):
        if e.mimeData().hasUrls():e.acceptProposedAction()
    def dropEvent(self,e):
        self.owner.open_paths([Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]); e.acceptProposedAction()
    def frame_poly(self):
        r=self.image_rect
        pts=[]
        for x,y in self.owner.frame_quad:
            pts.append(QPointF(r.left()+r.width()*x, r.top()+r.height()*y))
        return pts
    def frame_rect(self):
        pts=self.frame_poly(); xs=[p.x() for p in pts]; ys=[p.y() for p in pts]
        return QRectF(min(xs),min(ys),max(xs)-min(xs),max(ys)-min(ys))
    def npt(self,fr_unused,x:float,y:float):
        # Bilinear interpolation inside the editable four-corner frame.
        tl,tr,br,bl=self.frame_poly()
        top=QPointF(tl.x()*(1-x)+tr.x()*x, tl.y()*(1-x)+tr.y()*x)
        bot=QPointF(bl.x()*(1-x)+br.x()*x, bl.y()*(1-x)+br.y()*x)
        return QPointF(top.x()*(1-y)+bot.x()*y, top.y()*(1-y)+bot.y()*y)
    def _guide_pen(self,color):
        c=QColor(color); c.setAlpha(round(255*self.owner.guide_alpha.value()/100))
        q=QPen(c); q.setWidthF(self.owner.guide_width.val()); q.setStyle(Qt.PenStyle.SolidLine); return q
    def _collect_lines(self,fr:QRectF):
        """Visible straight guides. Tuples: (a,b,color). Spiral excluded."""
        lines=[]
        # Fourth item marks whether a line belongs to a basic guide.
        # Intersection circles are calculated only from basic guides.
        def add(a,b,c,basic=False): lines.append((a,b,c,basic))
        if self.owner.show_thirds.isChecked():
            for q in (1/3,2/3):
                add(self.npt(fr,q,0),self.npt(fr,q,1),'#ff4d4f',True); add(self.npt(fr,0,q),self.npt(fr,1,q),'#ff4d4f',True)
        if self.owner.show_cross.isChecked():
            add(self.npt(fr,.5,0),self.npt(fr,.5,1),'#44b5ff',True); add(self.npt(fr,0,.5),self.npt(fr,1,.5),'#44b5ff',True)
        if self.owner.show_golden.isChecked():
            for q in (.381966,.618034):
                add(self.npt(fr,q,0),self.npt(fr,q,1),'#f5c542',True); add(self.npt(fr,0,q),self.npt(fr,1,q),'#f5c542',True)
        if self.owner.show_diagonal.isChecked():
            add(self.npt(fr,0,0),self.npt(fr,1,1),'#b77cff',True); add(self.npt(fr,1,0),self.npt(fr,0,1),'#b77cff',True)
        if self.owner.show_triangle.isChecked():
            add(self.npt(fr,0,1),self.npt(fr,1,0),'#ff9f43',True)
            add(self.npt(fr,0,0),self.npt(fr,1,.72),'#ff9f43',True)
            add(self.npt(fr,1,1),self.npt(fr,0,.28),'#ff9f43',True)
        if self.owner.show_symmetry.isChecked():
            add(self.npt(fr,.5,0),self.npt(fr,.5,1),'#64d8cb',True)
        if self.owner.show_radiating.isChecked():
            # A clean radial fan: all rays meet at an editable center.
            cx,cy=self.owner.comp_guides['radiating']['center']; c=self.npt(fr,cx,cy)
            edge=[]
            for q in (0,.2,.4,.6,.8,1):
                edge += [self.npt(fr,q,0), self.npt(fr,q,1)]
            for q in (.2,.4,.6,.8):
                edge += [self.npt(fr,0,q), self.npt(fr,1,q)]
            for pt in edge: add(c,pt,'#ff6b6b')
        if self.owner.show_tunnel.isChecked():
            col='#f97316'
            pts=self.owner.comp_guides['tunnel']['points']
            q=[self.npt(fr,*xy) for xy in pts]
            for i in range(4): add(q[i],q[(i+1)%4],col)
            for outer,inner in zip(((0,0),(1,0),(1,1),(0,1)),pts): add(self.npt(fr,*outer),self.npt(fr,*inner),col)
            # A second nested ring follows the editable inner frame toward its center.
            cx=sum(x for x,y in pts)/4; cy=sum(y for x,y in pts)/4
            q2=[]
            for x,y in pts: q2.append(self.npt(fr,cx+(x-cx)*.55,cy+(y-cy)*.55))
            for i in range(4): add(q2[i],q2[(i+1)%4],col)
        if self.owner.show_golden_triangle.isChecked():
            col='#ffd166'; pts=self.owner.comp_guides['golden_triangle']['points']
            add(self.npt(fr,*pts[0]),self.npt(fr,*pts[1]),col); add(self.npt(fr,*pts[2]),self.npt(fr,*pts[3]),col); add(self.npt(fr,*pts[4]),self.npt(fr,*pts[5]),col)
        if self.owner.show_vshape.isChecked():
            col='#ff477e'; pts=self.owner.comp_guides['vshape']['points']; add(self.npt(fr,*pts[0]),self.npt(fr,*pts[1]),col); add(self.npt(fr,*pts[2]),self.npt(fr,*pts[1]),col)
        if self.owner.show_double_diagonal.isChecked():
            col='#c77dff'; pts=self.owner.comp_guides['double_diagonal']['points']; add(self.npt(fr,*pts[0]),self.npt(fr,*pts[1]),col); add(self.npt(fr,*pts[2]),self.npt(fr,*pts[3]),col)
        if self.owner.show_lshape.isChecked():
            col='#2ec4b6'; pts=self.owner.comp_guides['lshape']['points']; add(self.npt(fr,*pts[0]),self.npt(fr,*pts[1]),col); add(self.npt(fr,*pts[1]),self.npt(fr,*pts[2]),col)
        if self.owner.show_pyramid.isChecked():
            col='#fb8500'; pts=self.owner.comp_guides['pyramid']['points']; a=self.npt(fr,*pts[0]); b=self.npt(fr,*pts[1]); cc=self.npt(fr,*pts[2]); add(a,b,col); add(b,cc,col); add(cc,a,col)
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
        av=self.rect().adjusted(18,18,-18,-18)
        # Workspace scale: 100% = normal fit, larger values shrink the image and
        # create working room around it for vanishing points outside the frame.
        ws=max(1.0, self.owner.workspace_scale.value()/100.0)
        target=av.size()
        target.setWidth(max(80,int(target.width()/ws))); target.setHeight(max(60,int(target.height()/ws)))
        sc=self.pixmap.size().scaled(target,Qt.AspectRatioMode.KeepAspectRatio)
        z=max(.25,min(4.0,self.owner.view_zoom))
        sw=max(1,int(sc.width()*z)); sh=max(1,int(sc.height()*z))
        x=av.left()+(av.width()-sw)/2; y=av.top()+(av.height()-sh)/2; self.image_rect=QRectF(x,y,sw,sh)
        p.drawPixmap(self.image_rect.toRect(),self.pixmap)
        fr=self.frame_rect(); frame_poly=QPolygonF(self.frame_poly()); p.save(); p.setClipPath(self._frame_clip_path())
        lines=self._collect_lines(fr)
        for a,b,color,*_meta in lines:
            pen=self._guide_pen(color)
            if color=='#64d8cb' and self.owner.show_symmetry.isChecked(): pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen); p.drawLine(a,b)
        # Curved composition guides are drawn separately from straight-line intersection logic.
        from PySide6.QtGui import QPainterPath
        curve_alpha=round(255*self.owner.guide_alpha.value()/100)
        curve_width=self.owner.guide_width.val()
        if self.owner.show_circle.isChecked():
            col=QColor('#ff5d8f'); col.setAlpha(curve_alpha); pen=QPen(col); pen.setWidthF(curve_width); p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            cx,cy=self.owner.comp_guides['circle']['center']; rx=self.owner.comp_guides['circle']['rx']; ry=self.owner.comp_guides['circle']['ry']; pc=self.npt(fr,cx,cy); cr=QRectF(pc.x()-fr.width()*rx, pc.y()-fr.height()*ry, fr.width()*rx*2, fr.height()*ry*2); p.drawEllipse(cr)
        if self.owner.show_cshape.isChecked():
            col=QColor('#ff5d8f'); col.setAlpha(curve_alpha); pen=QPen(col); pen.setWidthF(curve_width); p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            cx,cy=self.owner.comp_guides['cshape']['center']; rx=self.owner.comp_guides['cshape']['rx']; ry=self.owner.comp_guides['cshape']['ry']; pc=self.npt(fr,cx,cy); cr=QRectF(pc.x()-fr.width()*rx, pc.y()-fr.height()*ry, fr.width()*rx*2, fr.height()*ry*2)
            # Open C facing right. Qt angles are in sixteenths of a degree.
            p.drawArc(cr, 55*16, 250*16)
        if self.owner.show_scurve.isChecked():
            col=QColor('#ef476f'); col.setAlpha(curve_alpha); pen=QPen(col); pen.setWidthF(curve_width); p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            pts=self.owner.comp_guides['scurve']['points']; path=QPainterPath(self.npt(fr,*pts[0])); path.cubicTo(self.npt(fr,*pts[1]),self.npt(fr,*pts[2]),self.npt(fr,*pts[3])); path.cubicTo(self.npt(fr,*pts[4]),self.npt(fr,*pts[5]),self.npt(fr,*pts[6])); p.drawPath(path)

        if self.owner.show_spiral.isChecked():
            c=QColor('#ffd166'); c.setAlpha(round(255*self.owner.guide_alpha.value()/100)); pen=QPen(c); pen.setWidthF(self.owner.guide_width.val()); p.setPen(pen)
            pts=[]; cx=fr.left()+fr.width()*.382; cy=fr.top()+fr.height()*.618; maxr=min(fr.width(),fr.height())*.62
            for i in range(180):
                th=i/179*math.pi*3.2; rad=maxr*math.exp(-.17*th); pts.append(QPointF(cx+rad*math.cos(th),cy-rad*math.sin(th)))
            p.drawPolyline(QPolygonF(pts))
        if self.owner.show_points.isChecked():
            pts=[]
            # Basic guides keep their filled intersection circles. Additional
            # composition guides never create normal circles; their white
            # handles appear only while that guide is selected for editing.
            basic_lines=[ln for ln in lines if len(ln)>3 and ln[3]]
            for i in range(len(basic_lines)):
                for j in range(i+1,len(basic_lines)):
                    pt=seg_intersection(basic_lines[i][0],basic_lines[i][1],basic_lines[j][0],basic_lines[j][1])
                    if pt is None: continue
                    # de-duplicate near-identical crossings
                    if any((pt.x()-q.x())**2+(pt.y()-q.y())**2 < 16 for q in pts): continue
                    pts.append(pt)
            fill=QColor(self.owner.point_color); fill.setAlpha(round(255*self.owner.guide_alpha.value()/100))
            p.setBrush(fill); p.setPen(Qt.PenStyle.NoPen)
            r=self.owner.point_size.val()/2
            for pt in pts[:120]: p.drawEllipse(QRectF(pt.x()-r,pt.y()-r,r*2,r*2))
        # Composition-guide edit handles. They appear only in edit mode and only for the selected guide.
        if (not getattr(self.owner,'export_render_mode',False)) and self.owner.edit_comp_guides.isChecked() and self.owner.selected_comp_guide:
            self._draw_comp_handles(p,fr,self.owner.selected_comp_guide)
        # Free helper endpoints. Show only for selected free line or while dragging it.
        sel=self.owner.selected_helper
        if (not getattr(self.owner,'export_render_mode',False)) and sel and sel[0]=='free' and 0<=sel[1]<len(self.owner.helper_free):
            a,b=self.owner.helper_free[sel[1]]; pa=self.npt(fr,*a); pb=self.npt(fr,*b)
            p.setBrush(QColor('#ffffff')); hp=QPen(QColor(self.owner.helper_color)); hp.setWidth(2); p.setPen(hp)
            for pt in (pa,pb): p.drawEllipse(QRectF(pt.x()-6,pt.y()-6,12,12))
        p.restore()
        # Perspective overlay is intentionally NOT clipped to the movie frame,
        # because vanishing points often sit far outside the picture area.
        if self.owner.show_perspective.isChecked():
            self._draw_perspective(p)
        if self.owner.show_frame.isChecked():
            poly=QPolygonF(self.frame_poly())
            fc=QColor(self.owner.frame_color); fc.setAlpha(round(255*self.owner.frame_alpha.value()/100))
            q=QPen(fc); q.setWidthF(self.owner.frame_width.val()); q.setStyle(Qt.PenStyle.SolidLine)
            p.setPen(q); p.setBrush(Qt.BrushStyle.NoBrush); p.drawPolygon(poly)
            if (not getattr(self.owner,'export_render_mode',False)) and self.owner.manual_frame.isChecked() and not self.owner.lock_frame.isChecked():
                # Photoshop-like transform handles: four corners + four side midpoints.
                pts=self.frame_poly(); mids=[QPointF((pts[i].x()+pts[(i+1)%4].x())/2,(pts[i].y()+pts[(i+1)%4].y())/2) for i in range(4)]
                p.setBrush(fc); hp=QPen(QColor('#0f1712')); hp.setWidthF(1.0); p.setPen(hp)
                for pt in pts: p.drawRect(QRectF(pt.x()-6,pt.y()-6,12,12))
                for pt in mids: p.drawRect(QRectF(pt.x()-5,pt.y()-5,10,10))
    def _comp_handle_points(self,fr,name):
        d=self.owner.comp_guides[name]
        if name=='radiating': return [self.npt(fr,*d['center'])]
        if name=='tunnel':
            # Photoshop-like 8-point transform handles for the tunnel's inner frame:
            # 4 corners followed by top/right/bottom/left side midpoints.
            corners=[self.npt(fr,*q) for q in d['points']]
            mids=[QPointF((corners[i].x()+corners[(i+1)%4].x())/2,
                          (corners[i].y()+corners[(i+1)%4].y())/2) for i in range(4)]
            return corners+mids
        if name in ('golden_triangle','vshape','double_diagonal','scurve','lshape','pyramid'):
            return [self.npt(fr,*q) for q in d['points']]
        if name in ('circle','cshape'):
            cx,cy=d['center']; rx,ry=d['rx'],d['ry']
            return [self.npt(fr,cx,cy), self.npt(fr,min(1,cx+rx),cy), self.npt(fr,cx,min(1,cy+ry))]
        return []
    def _draw_comp_handles(self,p,fr,name):
        pts=self._comp_handle_points(fr,name)
        p.setBrush(QColor('#ffffff')); hp=QPen(QColor('#111820')); hp.setWidthF(1.5); p.setPen(hp)
        for pt in pts: p.drawEllipse(QRectF(pt.x()-6,pt.y()-6,12,12))
    def _pos_to_norm(self,pos,fr):
        if fr.width()<=1 or fr.height()<=1:return (0.,0.)
        return (max(0.,min(1.,(pos.x()-fr.left())/fr.width())), max(0.,min(1.,(pos.y()-fr.top())/fr.height())))
    def _comp_visible(self,name):
        return {
            'radiating':self.owner.show_radiating,'tunnel':self.owner.show_tunnel,'golden_triangle':self.owner.show_golden_triangle,
            'circle':self.owner.show_circle,'cshape':self.owner.show_cshape,'vshape':self.owner.show_vshape,
            'double_diagonal':self.owner.show_double_diagonal,'scurve':self.owner.show_scurve,'lshape':self.owner.show_lshape,'pyramid':self.owner.show_pyramid,
        }[name].isChecked()
    def _comp_segments(self,fr,name):
        d=self.owner.comp_guides[name]; seg=[]
        def n(q):return self.npt(fr,*q)
        if name=='radiating':
            c=n(d['center'])
            for q in (0,.2,.4,.6,.8,1): seg += [(c,n((q,0))),(c,n((q,1)))]
            for q in (.2,.4,.6,.8): seg += [(c,n((0,q))),(c,n((1,q)))]
        elif name=='tunnel':
            pts=d['points']; q=[n(x) for x in pts]
            seg += [(q[i],q[(i+1)%4]) for i in range(4)]
            seg += [(n(o),n(i)) for o,i in zip(((0,0),(1,0),(1,1),(0,1)),pts)]
        elif name=='golden_triangle':
            p=d['points']; seg=[(n(p[0]),n(p[1])),(n(p[2]),n(p[3])),(n(p[4]),n(p[5]))]
        elif name=='vshape':
            p=d['points']; seg=[(n(p[0]),n(p[1])),(n(p[2]),n(p[1]))]
        elif name=='double_diagonal':
            p=d['points']; seg=[(n(p[0]),n(p[1])),(n(p[2]),n(p[3]))]
        elif name=='lshape':
            p=d['points']; seg=[(n(p[0]),n(p[1])),(n(p[1]),n(p[2]))]
        elif name=='pyramid':
            p=d['points']; seg=[(n(p[0]),n(p[1])),(n(p[1]),n(p[2])),(n(p[2]),n(p[0]))]
        elif name=='scurve':
            # Polyline approximation for hit testing.
            p=d['points']
            def bez(a,b,c,d,t):
                u=1-t; return (u**3*a[0]+3*u*u*t*b[0]+3*u*t*t*c[0]+t**3*d[0],u**3*a[1]+3*u*u*t*b[1]+3*u*t*t*c[1]+t**3*d[1])
            arr=[]
            for i in range(13): arr.append(n(bez(p[0],p[1],p[2],p[3],i/12)))
            for i in range(1,13): arr.append(n(bez(p[3],p[4],p[5],p[6],i/12)))
            seg=[(arr[i],arr[i+1]) for i in range(len(arr)-1)]
        elif name in ('circle','cshape'):
            cx,cy=d['center']; rx,ry=d['rx'],d['ry']; arr=[]
            if name=='circle': angles=[i*math.tau/36 for i in range(37)]
            else: angles=[math.radians(55 + 250*i/30) for i in range(31)]
            for a in angles: arr.append(n((cx+rx*math.cos(a),cy-ry*math.sin(a))))
            seg=[(arr[i],arr[i+1]) for i in range(len(arr)-1)]
        return seg
    def _translate_comp(self,name,dx,dy,orig):
        if name=='radiating':
            x,y=orig['center']; self.owner.comp_guides[name]['center']=(max(0,min(1,x+dx)),max(0,min(1,y+dy)))
        elif name in ('circle','cshape'):
            x,y=orig['center']; rx,ry=orig['rx'],orig['ry'];
            nx=max(rx,min(1-rx,x+dx)); ny=max(ry,min(1-ry,y+dy)); self.owner.comp_guides[name].update(center=(nx,ny))
        else:
            pts=orig['points']; minx=min(x for x,y in pts); maxx=max(x for x,y in pts); miny=min(y for x,y in pts); maxy=max(y for x,y in pts)
            dx=max(-minx,min(1-maxx,dx)); dy=max(-miny,min(1-maxy,dy)); self.owner.comp_guides[name]['points']=[(x+dx,y+dy) for x,y in pts]

    def _image_norm_to_point(self,x,y):
        r=self.image_rect
        return QPointF(r.left()+r.width()*x, r.top()+r.height()*y)
    def _point_to_image_norm(self,pos):
        r=self.image_rect
        if r.width()<=1 or r.height()<=1:return (0.0,0.0)
        return ((pos.x()-r.left())/r.width(), (pos.y()-r.top())/r.height())

    def _v521_structural_segment_weight(self, seg, frame_w, frame_h, person_boxes=None):
        """Prefer long, spatially useful structural lines; heavily suppress lines centered in person regions."""
        x1, y1, x2, y2 = seg[:4]
        L = math.hypot(x2-x1, y2-y1)
        diag = max(1.0, math.hypot(frame_w, frame_h))
        norm_len = L / diag
        w = 0.35 + min(1.8, norm_len * 4.0)
        if norm_len >= 0.22:
            w *= STRUCTURAL_LONG_LINE_BONUS
        mx, my = (x1+x2)*0.5, (y1+y2)*0.5
        if person_boxes:
            for bx, by, bw, bh in person_boxes:
                if bx <= mx <= bx+bw and by <= my <= by+bh:
                    w *= PERSON_REGION_WEIGHT
                    break
        # Short lines around the central portrait/action area are common false positives.
        if norm_len < 0.12 and 0.20*frame_w <= mx <= 0.80*frame_w and 0.12*frame_h <= my <= 0.92*frame_h:
            w *= 0.35
        return max(0.01, w)

    def _v521_horizontal_eye_level_y(self):
        """Return a display-only horizontal eye-level height from the most reliable solved VPs."""
        vals = []
        for axis in (1, 2):
            try:
                vp = self.perspective_vps.get(axis)
                if vp is not None:
                    vals.append(float(vp[1]))
            except Exception:
                pass
        if vals:
            return sum(vals) / len(vals)
        return 0.5

    def _v521_learning_record(self, source="manual"):
        """Capture the user's actual calibration lines as teacher data, not only final VP coordinates."""
        rec = {"version": 2, "source": source, "families": []}
        try:
            lines_obj = self.perspective_lines
        except Exception:
            lines_obj = {}
        for axis in (1, 2, 3):
            fam = []
            try:
                axis_lines = lines_obj.get(axis, [])
            except Exception:
                axis_lines = []
            for ln in axis_lines:
                try:
                    if len(ln) >= 2 and hasattr(ln[0], "x"):
                        p1, p2 = ln[0], ln[1]
                        fam.append([float(p1.x()), float(p1.y()), float(p2.x()), float(p2.y())])
                    elif len(ln) >= 4:
                        fam.append([float(ln[0]), float(ln[1]), float(ln[2]), float(ln[3])])
                except Exception:
                    pass
            if fam:
                rec["families"].append({"axis": axis, "lines": fam})
        return rec

    def _v520_segment_angle_deg(self, seg):
        x1, y1, x2, y2 = seg[:4]
        a = math.degrees(math.atan2(y2-y1, x2-x1)) % 180.0
        return a

    def _v520_segment_mid(self, seg):
        x1, y1, x2, y2 = seg[:4]
        return ((x1+x2)*0.5, (y1+y2)*0.5)

    def _v520_segment_len(self, seg):
        x1, y1, x2, y2 = seg[:4]
        return math.hypot(x2-x1, y2-y1)

    def _v520_family_cluster(self, segments, min_sep_deg=FAMILY_MIN_ANGLE_SEPARATION_DEG):
        """Cluster detected structural segments into orientation families before solving VPs."""
        if not segments:
            return []
        bins = []
        for seg in segments:
            a = self._v520_segment_angle_deg(seg)
            L = max(1.0, self._v520_segment_len(seg))
            placed = False
            for b in bins:
                d = abs(((a - b["angle"] + 90.0) % 180.0) - 90.0)
                if d <= 7.5:
                    w0 = b["weight"]
                    b["angle"] = (b["angle"] * w0 + a * L) / (w0 + L)
                    b["weight"] += L
                    b["segments"].append(seg)
                    placed = True
                    break
            if not placed:
                bins.append({"angle": a, "weight": L, "segments": [seg]})
        bins.sort(key=lambda b: b["weight"], reverse=True)
        families = []
        for b in bins:
            if len(b["segments"]) < 2:
                continue
            if all(abs(((b["angle"]-f["angle"]+90.0)%180.0)-90.0) >= min_sep_deg for f in families):
                families.append(b)
            if len(families) >= 5:
                break
        return families

    def _v520_vertical_family_is_parallel(self, family, frame_h):
        """Treat near-vertical lines as parallel unless there is strong converging evidence."""
        if not family or len(family.get("segments", [])) < VP3_MIN_VERTICAL_SUPPORT:
            return True
        segs = family["segments"]
        angles = [self._v520_segment_angle_deg(s) for s in segs]
        devs = [min(abs(a-90.0), abs(a+90.0), abs(a-270.0)) for a in angles]
        med = sorted(devs)[len(devs)//2]
        ys = [self._v520_segment_mid(s)[1] for s in segs]
        spread = (max(ys)-min(ys))/max(1.0, float(frame_h))
        return med <= VP3_VERTICAL_PARALLEL_DEG and spread >= VP3_MIN_VERTICAL_SPREAD

    def _v520_penalize_inside_local_vp(self, vp, family_segments, frame_w, frame_h):
        """Penalize suspicious in-frame VPs supported by short/local segments."""
        if vp is None:
            return 1.0
        x, y = vp
        inside = 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
        if not inside or not family_segments:
            return 1.0
        lengths = [self._v520_segment_len(s) for s in family_segments]
        mids = [self._v520_segment_mid(s) for s in family_segments]
        mean_len = sum(lengths)/max(1, len(lengths))
        xs = [m[0] for m in mids]; ys = [m[1] for m in mids]
        spread = ((max(xs)-min(xs))/max(1.0, frame_w) + (max(ys)-min(ys))/max(1.0, frame_h))*0.5
        long_enough = mean_len >= 0.22*frame_w
        well_spread = spread >= 0.45
        if long_enough and well_spread:
            return 0.9
        return INSIDE_VP_LOCALITY_PENALTY

    def _draw_perspective(self,p):
        """VanishPoint-style calibration: two 2-anchor lines solve each VP."""
        if self.pixmap is None:return
        vp_defs=(('VP1',self.owner.vp1,self.owner.vp_ray_colors['vp1'],'vp1'),('VP2',self.owner.vp2,self.owner.vp_ray_colors['vp2'],'vp2'),('VP3',self.owner.vp3,self.owner.vp_ray_colors['vp3'],'vp3'))
        # V5.24: show both VP HORIZON and EYE LEVEL only after VP1 + VP2 are solved.
        # The geometric horizon may tilt when the camera has roll; EYE LEVEL itself is
        # always drawn screen-horizontal at the horizon's center-crossing height.
        base_pair_complete=(self.owner._persp_axis_complete.get('vp1',False)
                            and self.owner._persp_axis_complete.get('vp2',False))
        if base_pair_complete and not (getattr(self.owner,'vp_at_infinity',{}).get('vp1',False) or getattr(self.owner,'vp_at_infinity',{}).get('vp2',False)):
            h1=self._image_norm_to_point(*self.owner.vp1); h2=self._image_norm_to_point(*self.owner.vp2)
            hc=QColor('#8c96a3'); hc.setAlpha(105); hp=QPen(hc); hp.setWidthF(0.8); hp.setStyle(Qt.PenStyle.DashLine); p.setPen(hp)
            hdx=h2.x()-h1.x(); hdy=h2.y()-h1.y(); hln=max(1e-6,math.hypot(hdx,hdy)); hext=10000.0/hln
            ha=QPointF(h1.x()-hdx*hext,h1.y()-hdy*hext); hb=QPointF(h1.x()+hdx*hext,h1.y()+hdy*hext); p.drawLine(ha,hb)
            # Geometric VP horizon stays separate from the readable horizontal eye level.
            cx=self.image_rect.center().x()
            if abs(hdx)>1e-6:
                hy=h1.y()+(cx-h1.x())*hdy/hdx
            else:
                hy=(h1.y()+h2.y())*0.5
            p.setPen(hc); p.drawText(QRectF(self.image_rect.left()+6,hy-20,110,18),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'VP HORIZON')

            # Yellow EYE LEVEL is deliberately horizontal, regardless of VP horizon tilt.
            eye_y=hy
            ec=QColor('#ffe66d'); ec.setAlpha(235); ep=QPen(ec); ep.setWidthF(1.5); ep.setStyle(Qt.PenStyle.DashLine); p.setPen(ep)
            p.drawLine(QPointF(self.image_rect.left(),eye_y),QPointF(self.image_rect.right(),eye_y))
        # Fan guide lines are intentionally deferred until the two horizontal axes
        # (VP1 + VP2) are both solved.  This keeps the canvas clean while calibrating.
        base_pair_ready=(self.owner._persp_axis_complete.get('vp1',False)
                         and self.owner._persp_axis_complete.get('vp2',False))
        # Infinite VP families: use the two manual strokes only. Weakly converging
        # X/Z/Y axes become stable parallel guide families instead of false far VPs.
        for inf_key in ('vp1','vp2','vp3'):
            infmap=getattr(self.owner,'vp_at_infinity',{'vp1':False,'vp2':False,'vp3':False})
            if not (base_pair_ready and self.owner._persp_axis_complete.get(inf_key,False)
                    and infmap.get(inf_key,False) and self.owner.vp_ray_visible.get(inf_key,True)):
                continue
            lines=self.owner.perspective_lines.get(inf_key,[])
            if len(lines)>=2:
                w=max(1.0,float(self.owner.original.width))
                h=max(1.0,float(self.owner.original.height))

                dirs=[]
                for ln in lines[:2]:
                    dx=(ln[1][0]-ln[0][0])*w
                    dy=(ln[1][1]-ln[0][1])*h
                    n=math.hypot(dx,dy)
                    if n>1e-9:
                        u=(dx/n,dy/n)
                        # Line direction is unsigned. Align stroke 2 to stroke 1
                        # before averaging so opposite drag directions cannot cancel.
                        if dirs and (u[0]*dirs[0][0]+u[1]*dirs[0][1])<0:
                            u=(-u[0],-u[1])
                        dirs.append(u)

                if dirs:
                    solved_dir=getattr(self.owner,'camera_inf_dir',{}).get(inf_key)
                    if solved_dir is not None:
                        ux,uy=solved_dir
                    else:
                        sx=sum(d[0] for d in dirs); sy=sum(d[1] for d in dirs)
                        n=max(1e-9,math.hypot(sx,sy))
                        ux,uy=sx/n,sy/n

                    # If the user's average is extremely close to screen vertical,
                    # snap only that tiny hand jitter; otherwise preserve camera roll.
                    off_vertical=math.degrees(math.acos(max(0.0,min(1.0,abs(uy)))))
                    if off_vertical < 2.0:
                        ux,uy=0.0,1.0

                    nx,ny=-uy,ux
                    r=self.image_rect
                    corners=[QPointF(r.left(),r.top()),QPointF(r.right(),r.top()),
                             QPointF(r.right(),r.bottom()),QPointF(r.left(),r.bottom())]
                    projs=[c.x()*nx+c.y()*ny for c in corners]
                    lo,hi=min(projs),max(projs)
                    count=max(2,int(self.owner.vp_ray_counts.get(inf_key,12)))
                    col=QColor(self.owner.vp_ray_colors[inf_key])
                    col.setAlpha(round(255*self.owner.perspective_alpha.value()/100))
                    pen=QPen(col); pen.setWidthF(self.owner.perspective_line_width.value.value())
                    p.setPen(pen)
                    radius=max(r.width(),r.height())*4.0
                    for i in range(count):
                        t=lo+(hi-lo)*(i+0.5)/count
                        cx=nx*t; cy=ny*t
                        p.drawLine(QPointF(cx-ux*radius,cy-uy*radius),
                                   QPointF(cx+ux*radius,cy+uy*radius))

        for label,xy,color,key in vp_defs:
            ready = base_pair_ready and self.owner._persp_axis_complete.get(key,False) and not getattr(self.owner,'vp_at_infinity',{}).get(key,False)
            if not ready or not self.owner.vp_ray_visible.get(key,True):
                continue
            vp=self._image_norm_to_point(*xy); count=max(2,int(self.owner.vp_ray_counts.get(key,12)))
            rc=QColor(color); rc.setAlpha(round(255*self.owner.perspective_alpha.value()/100)); rp=QPen(rc); rp.setWidthF(self.owner.perspective_line_width.value.value()); p.setPen(rp)
            # Radius must reach the image even when the true VP is very far away.
            # This preserves the user's calibration direction without clamping the VP.
            center=self.image_rect.center()
            radius=max(20000.0, math.hypot(vp.x()-center.x(),vp.y()-center.y())*2.5 + 2000.0)

            # Aim the fan through the actual image rectangle.  If a VP is far outside
            # the image, a uniform 180-degree fan leaves most rays missing the picture.
            # Instead, sample only the angular interval subtended by the frame so every
            # requested ray crosses the image.  When the VP is inside the frame, keep a
            # conventional 180-degree full-line fan.
            r=self.image_rect
            inside=(r.left() <= vp.x() <= r.right() and r.top() <= vp.y() <= r.bottom())
            if inside:
                angles=[math.pi*i/count for i in range(count)]
            else:
                corners=[QPointF(r.left(),r.top()),QPointF(r.right(),r.top()),
                         QPointF(r.right(),r.bottom()),QPointF(r.left(),r.bottom())]
                raw=[math.atan2(c.y()-vp.y(),c.x()-vp.x()) for c in corners]
                # Find the shortest circular arc containing all four corner directions.
                vals=sorted((a%(2*math.pi)) for a in raw)
                gaps=[]
                for i,a0 in enumerate(vals):
                    a1=vals[(i+1)%len(vals)] + (2*math.pi if i==len(vals)-1 else 0)
                    gaps.append((a1-a0,i))
                _,gi=max(gaps)
                start=vals[(gi+1)%len(vals)]
                end=vals[gi] + (2*math.pi if vals[gi] < start else 0)
                span=max(1e-6,end-start)
                if count==1:
                    angles=[start+span*0.5]
                else:
                    # Small inset keeps the outermost rays visibly inside the frame,
                    # rather than merely grazing a single corner pixel.
                    inset=min(span*0.04, math.radians(1.0))
                    a0=start+inset; a1=end-inset
                    if a1<=a0:
                        a0=start; a1=end
                    angles=[a0+(a1-a0)*i/(count-1) for i in range(count)]
            for a in angles:
                dx=math.cos(a)*radius; dy=math.sin(a)*radius
                p.drawLine(QPointF(vp.x()-dx,vp.y()-dy),QPointF(vp.x()+dx,vp.y()+dy))

            # VanishPoint-like in-frame perspective grid.  Use the same solved VP family,
            # but clip it strictly to the editable green frame so the image itself gets a
            # readable mesh while the longer fan rays can remain available outside.
            if hasattr(self.owner,'show_perspective_grid') and self.owner.show_perspective_grid.isChecked():
                p.save()
                p.setClipPath(self._frame_clip_path())
                gc=QColor(color); gc.setAlpha(round(255*self.owner.perspective_alpha.value()/100)); gp=QPen(gc); gp.setWidthF(self.owner.perspective_line_width.value.value()); p.setPen(gp)
                for a in angles:
                    dx=math.cos(a)*radius; dy=math.sin(a)*radius
                    p.drawLine(QPointF(vp.x()-dx,vp.y()-dy),QPointF(vp.x()+dx,vp.y()+dy))
                p.restore()

        # Two calibration segments per axis.  Unsolved/inactive axes are fully hidden,
        # and even the active axis shows a segment only after the user actually draws it.
        # This prevents default/template lines appearing before the pencil stroke.
        for label,xy,color,key in vp_defs:
            complete = self.owner._persp_axis_complete.get(key, False)
            if not complete and key != self.owner.active_perspective_axis:
                continue
            for li,line in enumerate(self.owner.perspective_lines[key]):
                active=(key==self.owner.active_perspective_axis and li==self.owner.perspective_step)
                drawing_this = bool(self.drag_item and self.drag_item[0]=='perspective_draw'
                                    and self.drag_item[1]==key and self.drag_item[2]==li)
                touched = bool(self.owner._persp_anchor_touched.get((key,li),set()))
                if not complete and not touched and not drawing_this:
                    continue
                c=QColor(color); base_alpha=self.owner.perspective_alpha.value()/100; c.setAlpha(round(255*base_alpha*(1.0 if active else 0.55))); pen=QPen(c)
                # While positioning a calibration line, make it slightly bolder.
                # Once the second line is confirmed, return it to the same thin weight
                # as the first confirmed line while keeping the handles available.
                basew=self.owner.perspective_line_width.value.value(); pen.setWidthF(max(1.0,basew*2.0) if (active and not complete) else basew); p.setPen(pen)
                a=self._image_norm_to_point(*line[0]); b=self._image_norm_to_point(*line[1]); p.drawLine(a,b)
                dx=b.x()-a.x(); dy=b.y()-a.y(); ln=math.hypot(dx,dy)
                if ln>1e-6:
                    ext=10000.0/ln; cc=QColor(color); base_alpha=self.owner.perspective_alpha.value()/100; cc.setAlpha(round(255*base_alpha*(0.55 if active else 0.25))); xp=QPen(cc)
                    xp.setWidthF(max(0.75,basew*1.5) if (active and not complete) else max(0.5,basew*0.75)); xp.setStyle(Qt.PenStyle.DashLine); p.setPen(xp)
                    p.drawLine(QPointF(a.x()-dx*ext,a.y()-dy*ext),QPointF(a.x()+dx*ext,a.y()+dy*ext))
                if (not getattr(self.owner,'export_render_mode',False)) and active and self.owner.show_perspective_handles.isChecked():
                    outline=QPen(QColor(color)); outline.setWidthF(2.0); p.setPen(outline); p.setBrush(QColor('#ffffff'))
                    for ptxy in line:
                        hp=self._image_norm_to_point(*ptxy); p.drawEllipse(QRectF(hp.x()-5,hp.y()-5,10,10))
        # solved VP markers only; unsolved defaults stay invisible.
        for label,xy,color,key in vp_defs:
            if not self.owner._persp_axis_complete.get(key,False):
                continue
            if key=='vp3' and getattr(self.owner,'vp3_at_infinity',False):
                continue
            vp=self._image_norm_to_point(*xy); c=QColor(color); c.setAlpha(245); p.setBrush(c); p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(QRectF(vp.x()-7,vp.y()-7,14,14))
            p.setPen(QColor('#f5f7fa')); p.drawText(QRectF(vp.x()+10,vp.y()-12,58,24),Qt.AlignmentFlag.AlignVCenter,label)
        if base_pair_complete:
            p.setPen(QColor('#ffe66d')); p.drawText(QRectF(self.image_rect.left()+6,eye_y-23,120,20),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'EYE LEVEL')

    def _perspective_hit(self,pos):
        if not self.owner.show_perspective.isChecked() or self.pixmap is None:return None
        if self.owner.show_perspective_handles.isChecked():
            name=self.owner.active_perspective_axis; li=self.owner.perspective_step
            if li==1 and not self.owner._persp_anchor_touched.get((name,1),set()):
                return None
            line=self.owner.perspective_lines[name][li]
            for ei,xy in enumerate(line):
                pt=self._image_norm_to_point(*xy)
                if math.hypot(pos.x()-pt.x(),pos.y()-pt.y())<15:return ('perspective_anchor',name,li,ei)
        return None

    def wheelEvent(self,e):
        if self.pixmap is None:return
        steps=e.angleDelta().y()/120.0
        if abs(steps)<0.01:return
        self.owner.view_zoom=max(.25,min(4.0,self.owner.view_zoom*(1.12**steps)))
        self.owner.update_zoom_label(); self.update(); e.accept()

    def _frame_clip_path(self):
        from PySide6.QtGui import QPainterPath
        path=QPainterPath(); pts=self.frame_poly()
        if pts:
            path.moveTo(pts[0])
            for pt in pts[1:]: path.lineTo(pt)
            path.closeSubpath()
        return path
    def _dist_to_segment(self,p,a,b):
        vx=b.x()-a.x(); vy=b.y()-a.y(); wx=p.x()-a.x(); wy=p.y()-a.y(); vv=vx*vx+vy*vy
        if vv<=1e-8:return math.hypot(wx,wy)
        t=max(0,min(1,(wx*vx+wy*vy)/vv)); qx=a.x()+t*vx; qy=a.y()+t*vy
        return math.hypot(p.x()-qx,p.y()-qy)
    def _hit(self,pos):
        if not self.pixmap:return None
        fr=self.frame_rect(); tol=10
        phit=self._perspective_hit(pos)
        if phit:return phit
        # Transform handles have top priority, but the frame interior is checked last
        # so helper lines remain directly draggable even while free-transform is enabled.
        if self.owner.manual_frame.isChecked() and not self.owner.lock_frame.isChecked():
            pts=self.frame_poly()
            for i,pt in enumerate(pts):
                if math.hypot(pos.x()-pt.x(),pos.y()-pt.y()) < 13: return ('frame_corner',i)
            for i in range(4):
                a,b=pts[i],pts[(i+1)%4]; mid=QPointF((a.x()+b.x())/2,(a.y()+b.y())/2)
                if math.hypot(pos.x()-mid.x(),pos.y()-mid.y()) < 12: return ('frame_edge',i)
        # Editable composition guides. Handles have priority, then guide body.
        if self.owner.edit_comp_guides.isChecked():
            sel=self.owner.selected_comp_guide
            if sel and self._comp_visible(sel):
                for i,pt in enumerate(self._comp_handle_points(fr,sel)):
                    if math.hypot(pos.x()-pt.x(),pos.y()-pt.y())<12:return ('comp_handle',sel,i)
            # Prefer current selection, then other visible guides.
            names=[]
            if sel: names.append(sel)
            names += [n for n in ('tunnel','radiating','golden_triangle','circle','cshape','vshape','double_diagonal','scurve','lshape','pyramid') if n!=sel]
            for name in names:
                if not self._comp_visible(name): continue
                if any(self._dist_to_segment(pos,a,b)<tol for a,b in self._comp_segments(fr,name)): return ('comp_line',name)
        # Free line endpoint handles first, then free line body.
        for i,(a,b) in enumerate(self.owner.helper_free):
            pa=self.npt(fr,*a); pb=self.npt(fr,*b)
            if math.hypot(pos.x()-pa.x(),pos.y()-pa.y())<12:return ('free_end',i,0)
            if math.hypot(pos.x()-pb.x(),pos.y()-pb.y())<12:return ('free_end',i,1)
            if self._dist_to_segment(pos,pa,pb)<tol:return ('free_line',i)
        for i,q in enumerate(self.owner.helper_v):
            if self._dist_to_segment(pos,self.npt(fr,q,0),self.npt(fr,q,1))<tol:return ('v',i)
        for i,q in enumerate(self.owner.helper_h):
            if self._dist_to_segment(pos,self.npt(fr,0,q),self.npt(fr,1,q))<tol:return ('h',i)
        if self.owner.manual_frame.isChecked() and not self.owner.lock_frame.isChecked() and self._frame_clip_path().contains(pos):
            return ('frame_move',)
        return None
    def _get_pencil_cursor(self):
        """Small pencil cursor with the hot spot at the graphite tip."""
        if hasattr(self, "_pencil_cursor_cache"):
            return self._pencil_cursor_cache
        pm=QPixmap(28,28); pm.fill(Qt.GlobalColor.transparent)
        qp=QPainter(pm); qp.setRenderHint(QPainter.RenderHint.Antialiasing,True)
        # Pencil body, tilted from bottom-left tip toward top-right.
        body=QPen(QColor('#f0c94a')); body.setWidthF(5.0); body.setCapStyle(Qt.PenCapStyle.RoundCap)
        qp.setPen(body); qp.drawLine(QPointF(7,21),QPointF(21,7))
        edge=QPen(QColor('#7f8792')); edge.setWidthF(1.2); qp.setPen(edge); qp.drawLine(QPointF(8,20),QPointF(20,8))
        # Graphite tip and eraser cap.
        tip=QPen(QColor('#f4f6f8')); tip.setWidthF(2.0); tip.setCapStyle(Qt.PenCapStyle.RoundCap); qp.setPen(tip); qp.drawLine(QPointF(4.5,23.5),QPointF(7.3,20.7))
        er=QPen(QColor('#ff7b8a')); er.setWidthF(5.0); er.setCapStyle(Qt.PenCapStyle.RoundCap); qp.setPen(er); qp.drawPoint(QPointF(22,6))
        qp.end()
        self._pencil_cursor_cache=QCursor(pm,5,23)
        return self._pencil_cursor_cache

    def _update_cursor(self,pos):
        hit=self._hit(pos)
        if not hit:
            if (hasattr(self.owner,'right_tabs') and self.owner.right_tabs.currentIndex()==0
                    and hasattr(self.owner,'perspective_pencil') and self.owner.perspective_pencil.isChecked()
                    and self.image_rect.contains(pos)):
                self.setCursor(self._get_pencil_cursor()); return
            self.unsetCursor(); return
        typ=hit[0]
        if typ in ('perspective_vp','perspective_anchor'): self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif typ=='eye_level': self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif typ=='frame_corner': self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif typ=='frame_edge':
            i=hit[1]
            self.setCursor(Qt.CursorShape.SizeVerCursor if i in (0,2) else Qt.CursorShape.SizeHorCursor)
        elif typ=='frame_move': self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif typ=='comp_handle': self.setCursor(Qt.CursorShape.CrossCursor)
        elif typ=='comp_line': self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif typ in ('v','h','free_line','free_end'): self.setCursor(Qt.CursorShape.CrossCursor)
        else: self.unsetCursor()




    def mousePressEvent(self,e):
        if e.button()!=Qt.MouseButton.LeftButton:return
        pos=e.position()
        # Pencil-style calibration has canvas priority while the Perspective tab is active.
        # Existing white endpoint handles still win so a finished line can be fine-tuned.
        phit=self._perspective_hit(pos)
        in_image=self.image_rect.contains(pos)
        pencil_on=(hasattr(self.owner,'perspective_pencil') and self.owner.perspective_pencil.isChecked())
        persp_tab=(hasattr(self.owner,'right_tabs') and self.owner.right_tabs.currentIndex()==0)
        if persp_tab and pencil_on and in_image and not phit:
            name=self.owner.active_perspective_axis; li=self.owner.perspective_step
            nx,ny=self._point_to_image_norm(pos)
            lines=[[tuple(pt) for pt in line] for line in self.owner.perspective_lines[name]]
            lines[li]=[(nx,ny),(nx,ny)]
            self.owner.perspective_lines[name]=lines
            self.drag_item=('perspective_draw',name,li)
            self._persp_draw_start=(nx,ny)
            self.setCursor(self._get_pencil_cursor())
            self.update(); return

        hit=self._hit(pos); self.drag_item=hit
        if not hit and self.owner.edit_comp_guides.isChecked():
            self.owner.selected_comp_guide=None; self.owner.reset_comp_btn.setEnabled(False); self.update()
        if hit:
            typ=hit[0]
            if typ in ('perspective_vp','perspective_anchor','eye_level'):
                self._drag_start=pos
                self._persp_drag_orig=(tuple(self.owner.vp1),tuple(self.owner.vp2),tuple(self.owner.vp3),float(self.owner.eye_level_y))
            if typ in ('comp_handle','comp_line'):
                self.owner.selected_comp_guide=hit[1]; self.owner.reset_comp_btn.setEnabled(True); self.owner.selected_helper=None; self.owner.update_helper_buttons(); self.update()
                if typ=='comp_line' or (typ=='comp_handle' and hit[1]=='tunnel' and hit[2]>=4):
                    import copy
                    self._drag_start=pos; self._comp_drag_orig=copy.deepcopy(self.owner.comp_guides[hit[1]])
            if typ in ('v','h','free_line','free_end'):
                kind='free' if typ.startswith('free') else typ
                self.owner.selected_helper=(kind,hit[1]); self.owner.update_helper_buttons(); self.update()
            if typ=='free_line':
                fr=self.frame_rect(); self._drag_start=pos; self._drag_orig=self.owner.helper_free[hit[1]]
            elif typ in ('frame_edge','frame_move'):
                self._drag_start=pos; self._frame_drag_orig=[tuple(q) for q in self.owner.frame_quad]

    def mouseMoveEvent(self,e):
        if not self.drag_item:
            self._update_cursor(e.position()); return
        fr=self.frame_rect(); typ=self.drag_item[0]; pos=e.position()
        if typ=='perspective_draw':
            name,li=self.drag_item[1],self.drag_item[2]
            nx,ny=self._point_to_image_norm(pos)
            nx=max(-3.0,min(4.0,nx)); ny=max(-2.0,min(3.0,ny))
            lines=[[tuple(pt) for pt in line] for line in self.owner.perspective_lines[name]]
            lines[li]=[tuple(self._persp_draw_start),(nx,ny)]
            self.owner.perspective_lines[name]=lines
            if li==1: self.owner.solve_perspective_axis(name)
        elif typ=='perspective_vp':
            name=self.drag_item[1]; nx,ny=self._point_to_image_norm(pos)
            # Allow off-image VPs. Bounds keep the marker recoverable in the workspace.
            nx=max(-3.0,min(4.0,nx)); ny=max(-2.0,min(3.0,ny))
            if name=='vp1': self.owner.vp1=(nx,ny)
            elif name=='vp2': self.owner.vp2=(nx,ny)
            else: self.owner.vp3=(nx,ny)
            self.owner.update_perspective_labels()
        elif typ=='perspective_anchor':
            name,li,ei=self.drag_item[1],self.drag_item[2],self.drag_item[3]
            nx,ny=self._point_to_image_norm(pos); nx=max(-3.0,min(4.0,nx)); ny=max(-2.0,min(3.0,ny))
            # Move only the selected white anchor. While line 1 is being positioned,
            # do not let the hidden/default line 2 affect VP or eye-level calculations.
            lines=[[tuple(pt) for pt in line] for line in self.owner.perspective_lines[name]]
            lines[li][ei]=(nx,ny)
            self.owner.perspective_lines[name]=lines
            if li==1:
                self.owner.solve_perspective_axis(name)
        elif typ=='comp_handle':
            name,i=self.drag_item[1],self.drag_item[2]; d=self.owner.comp_guides[name]; nx,ny=self._pos_to_norm(pos,fr)
            if name=='radiating': d['center']=(nx,ny)
            elif name in ('circle','cshape'):
                cx,cy=d['center']
                if i==0: d['center']=(nx,ny)
                elif i==1: d['rx']=max(.03,min(.5,abs(nx-cx)))
                elif i==2: d['ry']=max(.03,min(.5,abs(ny-cy)))
            elif name=='tunnel' and i>=4:
                # Side-center handles move the corresponding side as a pair,
                # matching the green actual-frame transform behavior.
                edge=i-4
                orig=self._comp_drag_orig['points']
                aidx,bidx=((0,1),(1,2),(2,3),(3,0))[edge]
                a=self.npt(fr,*orig[aidx]); b=self.npt(fr,*orig[bidx])
                vx=b.x()-a.x(); vy=b.y()-a.y(); ln=max(1e-8,math.hypot(vx,vy))
                px=-vy/ln; py=vx/ln
                mdx=pos.x()-self._drag_start.x(); mdy=pos.y()-self._drag_start.y()
                amount=mdx*px+mdy*py
                dx=(amount*px)/max(1,fr.width()); dy=(amount*py)/max(1,fr.height())
                lo_dx=max(-orig[aidx][0],-orig[bidx][0]); hi_dx=min(1-orig[aidx][0],1-orig[bidx][0])
                lo_dy=max(-orig[aidx][1],-orig[bidx][1]); hi_dy=min(1-orig[aidx][1],1-orig[bidx][1])
                dx=max(lo_dx,min(hi_dx,dx)); dy=max(lo_dy,min(hi_dy,dy))
                pts=[list(q) for q in orig]
                for j in (aidx,bidx):
                    pts[j][0]=orig[j][0]+dx; pts[j][1]=orig[j][1]+dy
                d['points']=[tuple(q) for q in pts]
            else:
                pts=list(d['points']); pts[i]=(nx,ny); d['points']=pts
        elif typ=='comp_line':
            name=self.drag_item[1]; dx=(pos.x()-self._drag_start.x())/max(1,fr.width()); dy=(pos.y()-self._drag_start.y())/max(1,fr.height()); self._translate_comp(name,dx,dy,self._comp_drag_orig)
        elif typ=='v' and self.image_rect.width()>1:
            i=self.drag_item[1]; self.owner.helper_v[i]=max(0,min(1,(pos.x()-self.image_rect.left())/self.image_rect.width()))
        elif typ=='h' and self.image_rect.height()>1:
            i=self.drag_item[1]; self.owner.helper_h[i]=max(0,min(1,(pos.y()-self.image_rect.top())/self.image_rect.height()))
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
        elif typ=='frame_corner':
            i=self.drag_item[1]; r=self.image_rect
            nx=max(0,min(1,(pos.x()-r.left())/max(1,r.width()))); ny=max(0,min(1,(pos.y()-r.top())/max(1,r.height())))
            q=list(self.owner.frame_quad); q[i]=(nx,ny); self.owner.frame_quad=q
        elif typ=='frame_edge':
            i=self.drag_item[1]; r=self.image_rect; orig=self._frame_drag_orig
            # Photoshop-like side handle: move the selected side only in its perpendicular direction.
            aidx,bidx=((0,1),(1,2),(2,3),(3,0))[i]
            ax,ay=orig[aidx]; bx,by=orig[bidx]
            vx=(bx-ax)*r.width(); vy=(by-ay)*r.height(); ln=max(1e-8,math.hypot(vx,vy))
            nx=-vy/ln; ny=vx/ln
            mdx=pos.x()-self._drag_start.x(); mdy=pos.y()-self._drag_start.y()
            amount=mdx*nx+mdy*ny
            dx=(amount*nx)/max(1,r.width()); dy=(amount*ny)/max(1,r.height())
            q=[list(x) for x in orig]
            # Clamp the common translation so both endpoints remain inside the image.
            lo_dx=max(-orig[aidx][0],-orig[bidx][0]); hi_dx=min(1-orig[aidx][0],1-orig[bidx][0])
            lo_dy=max(-orig[aidx][1],-orig[bidx][1]); hi_dy=min(1-orig[aidx][1],1-orig[bidx][1])
            dx=max(lo_dx,min(hi_dx,dx)); dy=max(lo_dy,min(hi_dy,dy))
            for j in (aidx,bidx):
                q[j][0]=orig[j][0]+dx; q[j][1]=orig[j][1]+dy
            self.owner.frame_quad=[tuple(x) for x in q]
        elif typ=='frame_move':
            r=self.image_rect; orig=self._frame_drag_orig
            dx=(pos.x()-self._drag_start.x())/max(1,r.width()); dy=(pos.y()-self._drag_start.y())/max(1,r.height())
            minx=min(q[0] for q in orig); maxx=max(q[0] for q in orig); miny=min(q[1] for q in orig); maxy=max(q[1] for q in orig)
            dx=max(-minx,min(1-maxx,dx)); dy=max(-miny,min(1-maxy,dy))
            self.owner.frame_quad=[(q[0]+dx,q[1]+dy) for q in orig]
        self.update()
    def mouseReleaseEvent(self,e):
        if self.drag_item and self.drag_item[0].startswith('frame_'): self.owner.save_frame()
        if self.drag_item and self.drag_item[0]=='perspective_draw':
            name,li=self.drag_item[1],self.drag_item[2]
            line=self.owner.perspective_lines[name][li]
            dx=line[1][0]-line[0][0]; dy=line[1][1]-line[0][1]
            # Ignore accidental clicks; a real pencil stroke needs a measurable drag.
            if math.hypot(dx,dy) >= 0.0005:
                self.owner._persp_anchor_touched[(name,li)]={0,1}
                if li==0:
                    self.owner.begin_second_perspective_line(name)
                    self.owner.perspective_step=1
                    self.owner._persp_anchor_touched[(name,1)]=set()
                    self.owner.update_perspective_panel_state()
                else:
                    self.owner.solve_perspective_axis(name)
                    self.owner._persp_axis_complete[name]=True
                    self.owner.solve_perspective_axis(name)
                    self.owner.advance_after_axis_complete(name)
                self.owner.save_perspective(); self.owner.refresh()
            self.drag_item=None; return
        if self.drag_item and self.drag_item[0] in ('perspective_vp','perspective_anchor','eye_level'):
            if self.drag_item[0]=='perspective_anchor':
                name,li,ei=self.drag_item[1],self.drag_item[2],self.drag_item[3]
                key=(name,li)
                touched=self.owner._persp_anchor_touched.setdefault(key,set())
                touched.add(ei)
                if li==0 and touched=={0,1}:
                    self.owner.begin_second_perspective_line(name)
                    self.owner.perspective_step=1
                    self.owner._persp_anchor_touched[(name,1)]=set()
                    self.owner.update_perspective_panel_state()
                elif li==1:
                    self.owner.solve_perspective_axis(name)
                    if len(touched) >= 2:
                        self.owner._persp_axis_complete[name]=True
                        self.owner.perspective_step=1
                        self.owner.solve_perspective_axis(name)
                        self.owner.update_perspective_panel_state()
            self.owner.save_perspective()
            self.owner.refresh()
        self.drag_item=None

class MovieShotAnalyzer(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle('Movie Shot Analyzer — Camera Calibration Solver v1.2 v2'); self.resize(1500,920); self.setMinimumSize(1050,680); self.setAcceptDrops(True)
        self.paths=[]; self.current_index=-1; self.original=None; self.frame_quad=[(0.,0.),(1.,0.),(1.,1.),(0.,1.)]; self.frames={}
        self.perspective_by_image={}
        # Pure Manual Perspective: no automatic-analysis data and no learning data
        # are loaded into the perspective engine. Only the current manual strokes count.
        self.perspective_source='manual'
        self.vp1=(-0.30,0.50); self.vp2=(1.30,0.50); self.vp3=(0.50,-0.65); self.eye_level_y=0.50; self.view_zoom=1.0
        self.active_perspective_axis='vp1'; self.perspective_step=0
        self.vp_at_infinity={'vp1':False,'vp2':False,'vp3':False}; self.vp3_at_infinity=False
        self.camera_solution=None
        self.camera_inf_dir={'vp1':None,'vp2':None,'vp3':None}
        self.camera_solve_error=None
        self._persp_axis_complete={'vp1':False,'vp2':False,'vp3':False}; self._persp_anchor_touched={}; self.show_perspective_grid_default=True
        self.perspective_lines=self.default_perspective_lines()
        self.helper_v=[]; self.helper_h=[]; self.helper_free=[]; self.selected_helper=None
        self.selected_comp_guide=None
        self.comp_guides={
            'radiating': {'center': (.5,.5)},
            'tunnel': {'points': [( .18,.18),(.82,.18),(.82,.82),(.18,.82)]},
            'golden_triangle': {'points': [(0.,1.),(1.,0.),(0.,0.),(.42,1.),(.62,0.),(1.,1.)]},
            'circle': {'center': (.5,.5), 'rx': .34, 'ry': .42},
            'cshape': {'center': (.5,.5), 'rx': .34, 'ry': .42},
            'vshape': {'points': [(.18,0.),(.5,1.),(.82,0.)]},
            'double_diagonal': {'points': [(0.,.15),(.68,1.),(0.,.62),(1.,.08)]},
            'scurve': {'points': [(.72,.14),(.18,.05),(.18,.46),(.52,.48),(.86,.50),(.83,.91),(.28,.86)]},
            'lshape': {'points': [(.20,.14),(.20,.84),(.82,.84)]},
            'pyramid': {'points': [(.5,.12),(.14,.88),(.86,.88)]},
        }
        self.helper_color='#36d1ff'; self.point_color='#ff3838'; self.frame_color='#20f26b'
        self.vp_ray_colors={'vp1':'#00d4ff','vp2':'#ff4fa3','vp3':'#7ee787'}
        self.vp_ray_counts={'vp1':12,'vp2':12,'vp3':12}
        self.vp_ray_visible={'vp1':True,'vp2':True,'vp3':True}
        self.export_render_mode=False
        self.batch_export_running=False
        self.batch_export_cancelled=False
        self.batch_export_executor=ThreadPoolExecutor(max_workers=max(2,min(8,(os.cpu_count() or 4))))
        self.auto_frame_on_load=True
        self._persp_anchor_touched={}; self._persp_axis_complete={'vp1':False,'vp2':False,'vp3':False}; self._build_ui(); self._style(); self.statusBar().showMessage('V5.16 — 建築長線強化 + ワイド表示 + ←→画像送り')
    def section(self,lay,text):
        lab=QLabel(text); lab.setObjectName('section'); lay.addWidget(lab)
    def _build_ui(self):
        root=QWidget(); self.setCentralWidget(root)
        # Capture Left/Right/F before child widgets consume them. Numeric/text controls keep their own arrow behavior.
        app=QApplication.instance()
        if app is not None: app.installEventFilter(self); outer=QHBoxLayout(root); outer.setContentsMargins(8,8,8,8); outer.setSpacing(8)
        cw=QWidget(); cw.setObjectName('controlsWidget'); c=QVBoxLayout(cw); c.setContentsMargins(12,12,12,12); c.setSpacing(7)
        title=QLabel('Movie Shot Analyzer'); title.setObjectName('appTitle'); c.addWidget(title)
        sub=QLabel('Pure Manual Perspective v2'); sub.setObjectName('subtitle'); c.addWidget(sub)
        a=QPushButton('画像を開く'); a.clicked.connect(self.choose_images); b=QPushButton('フォルダを開く'); b.clicked.connect(self.choose_folder); c.addWidget(a); c.addWidget(b)
        self.file_label=QLabel('画像未選択'); self.file_label.setWordWrap(True); self.file_label.setObjectName('fileLabel'); c.addWidget(self.file_label)
        nav=QHBoxLayout(); self.prev_button=QPushButton('◀ 前'); self.next_button=QPushButton('次 ▶'); self.prev_button.clicked.connect(self.prev_image); self.next_button.clicked.connect(self.next_image); nav.addWidget(self.prev_button); nav.addWidget(self.next_button); c.addLayout(nav)
        keyhint=QLabel('← / → キーでも前後移動　・　F：画像優先表示'); keyhint.setObjectName('note'); keyhint.setWordWrap(True); c.addWidget(keyhint)

        self.section(c,'実映像フレーム')
        self.show_frame=QCheckBox('フレーム枠を表示'); self.show_frame.setChecked(True); self.manual_frame=QCheckBox('自由変形ハンドルを使う'); self.manual_frame.setChecked(True); self.show_frame.toggled.connect(self.refresh); c.addWidget(self.show_frame); c.addWidget(self.manual_frame)
        self.lock_frame=QCheckBox('緑フレームを固定（誤操作防止）'); self.lock_frame.setChecked(True); self.lock_frame.toggled.connect(self.refresh); c.addWidget(self.lock_frame)
        self.auto_frame_check=QCheckBox('黒帯を自動検出してフレーム適用'); self.auto_frame_check.setChecked(True); c.addWidget(self.auto_frame_check)
        row=QHBoxLayout(); auto=QPushButton('黒帯を自動検出'); auto.clicked.connect(self.auto_frame); reset=QPushButton('画像全体に戻す'); reset.clicked.connect(self.reset_frame); row.addWidget(auto); row.addWidget(reset); c.addLayout(row)
        apply_all=QPushButton('このフレームを全画像に適用'); apply_all.clicked.connect(self.apply_current_frame_to_all); c.addWidget(apply_all)
        fg=QGridLayout(); fg.addWidget(QLabel('フレーム太さ'),0,0); self.frame_width=StepControl(0.5,8.0,2.0,0.5); self.frame_width.value.valueChanged.connect(self.refresh); fg.addWidget(self.frame_width,0,1)
        fg.addWidget(QLabel('フレーム透明度'),1,0); self.frame_alpha=QSlider(Qt.Orientation.Horizontal); self.frame_alpha.setRange(0,100); self.frame_alpha.setValue(100); self.frame_alpha.valueChanged.connect(self.refresh); fg.addWidget(self.frame_alpha,1,1)
        fcbtn=QPushButton('フレーム色'); fcbtn.clicked.connect(self.choose_frame_color); fg.addWidget(fcbtn,2,0,1,2); c.addLayout(fg)

        self.section(c,'表示補正（元画像は変更しません）')
        self.sliders={}
        for key,label,lo,hi,val in [('brightness','明るさ',50,150,100),('contrast','コントラスト',50,150,100),('gamma','ガンマ',50,200,100),('saturation','彩度',0,200,100)]:
            row=QHBoxLayout(); row.addWidget(QLabel(label)); sld=QSlider(Qt.Orientation.Horizontal); sld.setRange(lo,hi); sld.setValue(val); v=QLabel(str(val)); v.setFixedWidth(32); sld.valueChanged.connect(lambda n,k=key,vl=v:(vl.setText(str(n)),self.update_display())); row.addWidget(sld,1); row.addWidget(v); c.addLayout(row); self.sliders[key]=sld
        resetdisp=QPushButton('表示補正をリセット'); resetdisp.clicked.connect(self.reset_display); c.addWidget(resetdisp)

        self.section(c,'画像書き出し')
        export_current=QPushButton('現在の画像を書き出し'); export_current.clicked.connect(self.export_current_image); c.addWidget(export_current)
        self.export_batch_button=QPushButton('全画像を一括書き出し'); self.export_batch_button.clicked.connect(self.batch_export_images); c.addWidget(self.export_batch_button)
        self.export_cancel_button=QPushButton('一括書き出しをキャンセル'); self.export_cancel_button.clicked.connect(self.cancel_batch_export); self.export_cancel_button.setEnabled(False); c.addWidget(self.export_cancel_button)
        self.export_progress_label=QLabel('書き出し: 待機中'); self.export_progress_label.setObjectName('note'); self.export_progress_label.setWordWrap(True); c.addWidget(self.export_progress_label)
        export_note=QLabel('表示中の構図ガイド・パース・緑フレームを画像に重ねてPNG保存します。EYE LEVEL/VP HORIZONはVP1＋VP2確定時のみ出力。編集用ハンドルは出力しません。'); export_note.setObjectName('note'); export_note.setWordWrap(True); c.addWidget(export_note)
        c.addStretch(1)
        scroll=QScrollArea(); scroll.setObjectName('controlScroll'); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); scroll.setWidget(cw); scroll.setMinimumWidth(235); scroll.setMaximumWidth(275); self.left_panel=scroll; outer.addWidget(scroll,0)
        self.left_toggle=QPushButton('‹'); self.left_toggle.setObjectName('panelToggle'); self.left_toggle.setFixedWidth(22); self.left_toggle.setToolTip('左パネルを折りたたむ'); self.left_toggle.clicked.connect(self.toggle_left_panel); outer.addWidget(self.left_toggle,0)
        self.canvas=ImageCanvas(self); outer.addWidget(self.canvas,1)
        self.right_toggle=QPushButton('›'); self.right_toggle.setObjectName('panelToggle'); self.right_toggle.setFixedWidth(22); self.right_toggle.setToolTip('右パネルを折りたたむ'); self.right_toggle.clicked.connect(self.toggle_right_panel); outer.addWidget(self.right_toggle,0)
        self._build_right_tabs(outer)
        self._update_nav(); self.update_perspective_labels(); self.update_perspective_panel_state()

    def _make_toggle_button(self,text,checked=False,tooltip=''):
        b=QPushButton(text); b.setCheckable(True); b.setChecked(checked)
        if tooltip: b.setToolTip(tooltip)
        return b

    def _build_right_tabs(self,outer):
        panel=QWidget(); panel.setObjectName('rightPanel'); panel.setMinimumWidth(350); panel.setMaximumWidth(430); self.right_panel=panel
        r=QVBoxLayout(panel); r.setContentsMargins(8,8,8,8); r.setSpacing(6)
        self.right_tabs=QTabWidget(); self.right_tabs.setObjectName('rightTabs'); r.addWidget(self.right_tabs)
        self._build_perspective_tab(); self._build_composition_tab(); self._build_analysis_tab()
        outer.addWidget(panel,0)

    def _build_perspective_tab(self):
        tab=QWidget(); outer=QVBoxLayout(tab); outer.setContentsMargins(0,0,0,0)
        sc=QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body=QWidget()
        body.setMinimumWidth(0)
        body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay=QVBoxLayout(body); lay.setContentsMargins(10,10,10,10); lay.setSpacing(9)
        self.show_perspective=QCheckBox(); self.show_perspective.setChecked(True); self.show_perspective.hide()
        self.show_perspective_handles=QCheckBox(); self.show_perspective_handles.setChecked(True); self.show_perspective_handles.hide()
        self.perspective_pencil=QCheckBox(); self.perspective_pencil.setChecked(True); self.perspective_pencil.hide()
        self.show_perspective_grid=QCheckBox(); self.show_perspective_grid.setChecked(True); self.show_perspective_grid.hide()
        self.section(lay,'カメラキャリブレーション')
        axisrow=QHBoxLayout(); self.axis_buttons={}
        tips={
            'vp1':'VP1を設定。1本目をドラッグで引き、続けて2本目もドラッグで引くとVPを確定します。',
            'vp2':'VP2を設定。別方向の平行エッジ2本から消失点を求めます。',
            'vp3':'VP3を設定。主に垂直方向の収束を2本の線から求めます。'}
        for key,label in [('vp1','X軸（水平・左右方向）'),('vp2','Z軸（奥行き方向）'),('vp3','Y軸（垂直・上下方向）')]:
            b=QPushButton(label)
            b.setCheckable(True)
            b.setMinimumWidth(0)
            b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            b.setStyleSheet('QPushButton{font-size:11px;padding:7px 4px;}')
            b.setToolTip(tips[key])
            b.clicked.connect(lambda checked,k=key:self.set_perspective_axis(k))
            axisrow.addWidget(b,1)
            self.axis_buttons[key]=b
        lay.addLayout(axisrow)
        self.persp_step_label=QLabel('1本目：画像上をドラッグして引く'); self.persp_step_label.setObjectName('fileLabel'); lay.addWidget(self.persp_step_label)
        self.persp_label=QLabel('未解決'); self.persp_label.setObjectName('note'); self.persp_label.setWordWrap(True); self.persp_label.setMinimumWidth(0); lay.addWidget(self.persp_label)
        row=QHBoxLayout()
        resetaxis=QPushButton('選択軸をリセット'); resetaxis.setMinimumWidth(0); resetaxis.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed); resetaxis.clicked.connect(self.reset_active_perspective_axis)
        resetall=QPushButton('全てリセット'); resetall.setMinimumWidth(0); resetall.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed); resetall.clicked.connect(self.reset_perspective)
        row.addWidget(resetaxis,1); row.addWidget(resetall,1); lay.addLayout(row)
        self.section(lay,'グリッド / 放射線')
        self.vp_ray_checks={}; self.vp_ray_count_labels={}; self.vp_ray_color_buttons={}
        for key,label in [('vp1','X軸'),('vp2','Z軸'),('vp3','Y軸')]:
            row=QHBoxLayout()
            chk=QCheckBox(f'{label} グリッド'); chk.setChecked(self.vp_ray_visible[key]); chk.toggled.connect(lambda v,k=key:self.set_vp_ray_visible(k,v)); row.addWidget(chk); self.vp_ray_checks[key]=chk
            minus=QPushButton('−'); minus.setFixedWidth(34); minus.clicked.connect(lambda checked=False,k=key:self.change_vp_ray_count(k,-1)); row.addWidget(minus)
            val=QLabel(str(self.vp_ray_counts[key])); val.setAlignment(Qt.AlignmentFlag.AlignCenter); val.setFixedWidth(30); row.addWidget(val); self.vp_ray_count_labels[key]=val
            plus=QPushButton('+'); plus.setFixedWidth(34); plus.clicked.connect(lambda checked=False,k=key:self.change_vp_ray_count(k,1)); row.addWidget(plus)
            col=QPushButton('色'); col.setFixedWidth(42); col.clicked.connect(lambda checked=False,k=key:self.choose_vp_ray_color(k)); row.addWidget(col); self.vp_ray_color_buttons[key]=col
            lay.addLayout(row)
        self._update_vp_color_buttons()
        row=QHBoxLayout(); row.addWidget(QLabel('パース線の太さ')); self.perspective_line_width=StepControl(0.5,5.0,0.5,0.5); self.perspective_line_width.value.valueChanged.connect(self.refresh); row.addWidget(self.perspective_line_width); lay.addLayout(row)
        row=QHBoxLayout(); row.addWidget(QLabel('パース線の透明度')); self.perspective_alpha=QSlider(Qt.Orientation.Horizontal); self.perspective_alpha.setRange(0,100); self.perspective_alpha.setValue(70); self.perspective_alpha.valueChanged.connect(self.refresh); row.addWidget(self.perspective_alpha,1); self.perspective_alpha_label=QLabel('70%'); self.perspective_alpha_label.setFixedWidth(42); self.perspective_alpha.valueChanged.connect(lambda v:self.perspective_alpha_label.setText(f'{v}%')); row.addWidget(self.perspective_alpha_label); lay.addLayout(row)
        self.section(lay,'カメラ解')
        self.camera_solve_label=QLabel('X/Zの2軸から解けます。Yを追加すると3軸で再計算します。')
        self.camera_solve_label.setObjectName('fileLabel'); self.camera_solve_label.setWordWrap(True); self.camera_solve_label.setMinimumWidth(0); lay.addWidget(self.camera_solve_label)
        solve_btn=QPushButton('カメラを再計算')
        solve_btn.clicked.connect(self.solve_camera_calibration); lay.addWidget(solve_btn)
        self.section(lay,'レンズ推定（35mm換算）')
        self.persp_lens=QLabel('VP1 と VP2 を確定すると推定を開始します。'); self.persp_lens.setObjectName('fileLabel'); self.persp_lens.setWordWrap(True); self.persp_lens.setMinimumWidth(0); lay.addWidget(self.persp_lens)
        self.persp_lens_detail=QLabel('レンズは手動X/Zを主推定。Camera Solver値は比較用です。'); self.persp_lens_detail.setObjectName('note'); self.persp_lens_detail.setWordWrap(True); self.persp_lens_detail.setMinimumWidth(0); lay.addWidget(self.persp_lens_detail)
        self.section(lay,'表示')
        row=QHBoxLayout(); row.addWidget(QLabel('作業領域')); self.workspace_scale=QSlider(Qt.Orientation.Horizontal); self.workspace_scale.setRange(100,400); self.workspace_scale.setValue(100); self.workspace_scale.valueChanged.connect(self.refresh); row.addWidget(self.workspace_scale,1); self.workspace_label=QLabel('100%'); self.workspace_label.setFixedWidth(46); self.workspace_scale.valueChanged.connect(lambda v:self.workspace_label.setText(f'{v}%')); row.addWidget(self.workspace_label); lay.addLayout(row)
        row=QHBoxLayout(); row.addWidget(QLabel('ホイールズーム')); self.zoom_label=QLabel('100%'); row.addWidget(self.zoom_label); zreset=QPushButton('100%'); zreset.clicked.connect(self.reset_zoom); row.addWidget(zreset); lay.addLayout(row)
        lay.addStretch(1); sc.setWidget(body); outer.addWidget(sc); self.right_tabs.addTab(tab,'パース・レンズ')

    def _build_composition_tab(self):
        tab=QWidget(); outer=QVBoxLayout(tab); outer.setContentsMargins(0,0,0,0)
        sc=QScrollArea(); sc.setWidgetResizable(True); sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); body=QWidget(); c=QVBoxLayout(body); c.setContentsMargins(10,10,10,10); c.setSpacing(7)
        self.section(c,'基本ガイド')
        basic=QGridLayout(); basic.setSpacing(6)
        specs=[('show_thirds','三分割',True),('show_cross','十字',False),('show_golden','黄金比',False),('show_spiral','黄金螺旋',False),('show_diagonal','対角線',False),('show_triangle','三角構図',False),('show_symmetry','対称軸',False)]
        for i,(attr,label,checked) in enumerate(specs):
            b=self._make_toggle_button(label,checked); b.toggled.connect(self.refresh); setattr(self,attr,b); basic.addWidget(b,i//2,i%2)
        c.addLayout(basic)
        note=QLabel('基本ガイドの直線同士の交点には、塗りつぶし○を表示できます。'); note.setObjectName('note'); note.setWordWrap(True); c.addWidget(note)

        self.section(c,'追加の構図')
        add=QGridLayout(); add.setSpacing(6)
        specs=[('show_radiating','放射構図'),('show_tunnel','トンネル'),('show_golden_triangle','ゴールデントライアングル'),('show_circle','円構図'),('show_cshape','C字構図'),('show_vshape','V字構図'),('show_double_diagonal','ダブル対角線'),('show_scurve','S字構図'),('show_lshape','L字構図'),('show_pyramid','ピラミッド構図')]
        for i,(attr,label) in enumerate(specs):
            b=self._make_toggle_button(label,False); b.toggled.connect(self.comp_guide_visibility_changed); setattr(self,attr,b); add.addWidget(b,i//2,i%2)
        c.addLayout(add)
        self.edit_comp_guides=QCheckBox('追加構図を編集'); self.edit_comp_guides.setChecked(True); self.edit_comp_guides.toggled.connect(self.comp_edit_toggled); c.addWidget(self.edit_comp_guides)
        self.reset_comp_btn=QPushButton('選択中ガイドを初期位置へ戻す'); self.reset_comp_btn.setEnabled(False); self.reset_comp_btn.clicked.connect(self.reset_selected_comp_guide); c.addWidget(self.reset_comp_btn)

        self.section(c,'補助線')
        row=QHBoxLayout(); av=QPushButton('＋ 縦'); ah=QPushButton('＋ 横'); af=QPushButton('＋ 自由線'); av.clicked.connect(self.add_vertical); ah.clicked.connect(self.add_horizontal); af.clicked.connect(self.add_free); row.addWidget(av); row.addWidget(ah); row.addWidget(af); c.addLayout(row)
        row=QHBoxLayout(); self.delete_helper_btn=QPushButton('選択線を削除'); self.delete_helper_btn.clicked.connect(self.delete_selected_helper); self.delete_helper_btn.setEnabled(False); clear=QPushButton('全削除'); clear.clicked.connect(self.clear_helpers); row.addWidget(self.delete_helper_btn); row.addWidget(clear); c.addLayout(row)

        self.section(c,'線・交点の設定')
        grid=QGridLayout(); grid.addWidget(QLabel('線の太さ'),0,0); self.guide_width=StepControl(0.5,10.0,1.5,0.5); self.guide_width.value.valueChanged.connect(self.refresh); grid.addWidget(self.guide_width,0,1)
        grid.addWidget(QLabel('構図ガイド透明度'),1,0); self.guide_alpha=QSlider(Qt.Orientation.Horizontal); self.guide_alpha.setRange(0,100); self.guide_alpha.setValue(90); self.guide_alpha.valueChanged.connect(self.refresh); grid.addWidget(self.guide_alpha,1,1); self.guide_alpha_label=QLabel('90%'); self.guide_alpha_label.setFixedWidth(38); self.guide_alpha.valueChanged.connect(lambda v:self.guide_alpha_label.setText(f'{v}%')); grid.addWidget(self.guide_alpha_label,1,2); c.addLayout(grid)
        self.show_points=QCheckBox('基本ガイドの交点○を表示'); self.show_points.setChecked(True); self.show_points.toggled.connect(self.refresh); c.addWidget(self.show_points)
        grid=QGridLayout(); grid.addWidget(QLabel('○サイズ'),0,0); self.point_size=StepControl(2.0,30.0,8.0,0.5); self.point_size.value.valueChanged.connect(self.refresh); grid.addWidget(self.point_size,0,1); c.addLayout(grid)
        row=QHBoxLayout(); pc=QPushButton('○の色'); pc.clicked.connect(self.choose_point_color); row.addWidget(pc); row.addStretch(1); c.addLayout(row)
        c.addStretch(1); sc.setWidget(body); outer.addWidget(sc); self.right_tabs.addTab(tab,'構図ガイド')

    def _build_analysis_tab(self):
        tab=QWidget(); lay=QVBoxLayout(tab); lay.setContentsMargins(10,10,10,10); lay.setSpacing(8)
        self.section(lay,'ショット分析')
        self.analysis_summary=QLabel('構図・パース・レンズの結果を現在の1カットについてまとめます。'); self.analysis_summary.setObjectName('fileLabel'); self.analysis_summary.setWordWrap(True); lay.addWidget(self.analysis_summary)
        self.analysis_lens=QLabel('レンズ推定は「パース・レンズ」タブに統合しました。'); self.analysis_lens.setObjectName('note'); self.analysis_lens.setWordWrap(True); lay.addWidget(self.analysis_lens)
        self.lens_detail=QLabel(''); self.lens_detail.setObjectName('note'); self.lens_detail.setWordWrap(True); self.lens_detail.setVisible(False); lay.addWidget(self.lens_detail)
        self.section(lay,'パース結果')
        self.analysis_perspective=QLabel('VP1 / VP2 / VP3 / Eye Level'); self.analysis_perspective.setObjectName('note'); self.analysis_perspective.setWordWrap(True); lay.addWidget(self.analysis_perspective)
        self.section(lay,'構図タイプ候補')
        lbl=QLabel('Balance / Unbalanced、フレーム内フレーム、視線誘導など、固定ガイドだけでは判断できない項目は後段の画像判定で扱います。'); lbl.setObjectName('note'); lbl.setWordWrap(True); lay.addWidget(lbl)
        lay.addStretch(1); self.right_tabs.addTab(tab,'ショット分析')

    def set_vp_ray_visible(self,key,value):
        self.vp_ray_visible[key]=bool(value); self.refresh()
    def change_vp_ray_count(self,key,delta):
        self.vp_ray_counts[key]=max(2,min(48,self.vp_ray_counts[key]+delta))
        if hasattr(self,'vp_ray_count_labels'): self.vp_ray_count_labels[key].setText(str(self.vp_ray_counts[key]))
        self.refresh()
    def choose_vp_ray_color(self,key):
        c=QColorDialog.getColor(QColor(self.vp_ray_colors[key]),self,f'{key.upper()} の色')
        if c.isValid():
            self.vp_ray_colors[key]=c.name(); self._update_vp_color_buttons(); self.refresh()
    def _update_vp_color_buttons(self):
        if not hasattr(self,'vp_ray_color_buttons'): return
        for key,b in self.vp_ray_color_buttons.items():
            b.setStyleSheet(f'background:{self.vp_ray_colors[key]}; color:#111; font-weight:700;')

    def set_perspective_axis(self,name):
        self.active_perspective_axis=name
        # Reopening a completed VP goes straight to line 2 for fine adjustment.
        # A new VP always starts with only line 1 visible.
        if self._persp_axis_complete.get(name,False):
            self.perspective_step=1
        else:
            self.perspective_step=0
            self._persp_anchor_touched[(name,0)]=set()
            self._persp_anchor_touched.pop((name,1),None)
        self.update_perspective_panel_state(); self.refresh()
    def begin_second_perspective_line(self,name):
        """Arm line 2 for a fresh pencil stroke instead of auto-placing anchors."""
        lines=[[tuple(pt) for pt in line] for line in self.perspective_lines[name]]
        # Keep it degenerate and hidden until the next drag starts.
        lines[1]=[(0.5,0.5),(0.5,0.5)]
        self.perspective_lines[name]=lines
        self._persp_axis_complete[name]=False

    # Backward-compatible alias for any older saved/action path.
    def prepare_second_perspective_line(self,name):
        self.begin_second_perspective_line(name)

    def advance_after_axis_complete(self,name):
        """Strict pencil workflow: VP1 -> VP2 -> VP3, each starting from a blank first line."""
        order=['vp1','vp2','vp3']
        idx=order.index(name)
        if idx < 2:
            nxt=order[idx+1]
            self.active_perspective_axis=nxt
            self.perspective_step=0
            # Critical V5.27 fix: remove old/default geometry. Otherwise its white
            # handles can intercept the pencil and prevent the next VP from starting.
            self.perspective_lines[nxt]=[[(0.5,0.5),(0.5,0.5)],[(0.5,0.5),(0.5,0.5)]]
            self._persp_axis_complete[nxt]=False
            self._persp_anchor_touched[(nxt,0)]=set()
            self._persp_anchor_touched.pop((nxt,1),None)
            if nxt=='vp3':
                self.vp3_at_infinity=False
        else:
            # VP3 is the final stage. Keep it selected for optional white-handle tuning.
            self.active_perspective_axis='vp3'
            self.perspective_step=1
        self.update_perspective_panel_state()
        self.refresh()

    def set_perspective_step(self,step):
        self.perspective_step=0 if step<=0 else 1; self.update_perspective_panel_state(); self.refresh()
    def next_perspective_step(self):
        if self.perspective_step==0:self.perspective_step=1
        else:
            order=['vp1','vp2','vp3']; self.active_perspective_axis=order[(order.index(self.active_perspective_axis)+1)%3]; self.perspective_step=0
        self.update_perspective_panel_state(); self.refresh()
    def update_perspective_panel_state(self):
        if not hasattr(self,'axis_buttons'): return
        for k,b in self.axis_buttons.items(): b.setChecked(k==self.active_perspective_axis)
        lab=self.active_perspective_axis.upper()
        if hasattr(self,'persp_step_label'):
            if self._persp_axis_complete.get(self.active_perspective_axis,False):
                self.persp_step_label.setText(f'{lab} 完了：白○で微調整')
            elif self.perspective_step==0:
                self.persp_step_label.setText(f'{lab} 1本目：短いエッジでもOK・ドラッグして基準線')
            else:
                self.persp_step_label.setText(f'{lab} 2本目：短いエッジでもOK・離すと確定')
    def reset_active_perspective_axis(self):
        defaults=self.default_perspective_lines(); name=self.active_perspective_axis
        import copy; self.perspective_lines[name]=copy.deepcopy(defaults[name]); self._persp_axis_complete[name]=False; self.vp_at_infinity[name]=False; self.vp3_at_infinity=self.vp_at_infinity['vp3']; self._persp_anchor_touched[(name,0)]=set(); self._persp_anchor_touched.pop((name,1),None); self.perspective_step=0; self.update_perspective_panel_state(); self.save_perspective(); self.refresh()

    def _style(self):
        self.setStyleSheet('''QMainWindow,QWidget{background:#20242b;color:#e8edf3}#controlsWidget{background:#20242b}#controlScroll{border:1px solid #343a43;background:#20242b}#appTitle{font-size:20px;font-weight:700}#panelTitle{font-size:18px;font-weight:700}#rightPanel{background:#1b1f26;border:1px solid #343a43}QTabWidget::pane{border:1px solid #343a43;background:#1b1f26}QTabBar::tab{background:#272d36;border:1px solid #3e4652;padding:9px 12px;margin-right:2px}QTabBar::tab:selected{background:#2d6cdf;color:white}QPushButton:checked{background:#2d6cdf;border-color:#68a0ff;color:white}#subtitle,#note{color:#aeb7c4}#section{font-size:14px;font-weight:700;color:#d9e2ec;margin-top:8px;border-top:1px solid #3b424d;padding-top:8px}#fileLabel{background:#171a20;border:1px solid #343a43;border-radius:5px;padding:8px}QPushButton{background:#303641;border:1px solid #48505d;border-radius:5px;padding:7px}QPushButton:hover{background:#3a424f}#panelToggle{padding:2px;font-size:17px;font-weight:700;background:#252b34;border-radius:3px}QPushButton:disabled{color:#69717c;background:#272b32}QLabel{min-height:20px;padding-top:2px;padding-bottom:2px}QCheckBox{min-height:22px;padding:3px 1px}QDoubleSpinBox{background:#171a20;border:1px solid #48505d;padding:4px}QSlider::groove:horizontal{height:4px;background:#3b424d}QSlider::handle:horizontal{width:14px;margin:-5px 0;background:#8ab4f8;border-radius:7px}QScrollBar:vertical{background:#20242b;width:12px}QScrollBar::handle:vertical{background:#4a5260;min-height:28px;border-radius:5px}QStatusBar{background:#171a20;color:#aeb7c4}''')
    def toggle_left_panel(self):
        if not hasattr(self,'left_panel'): return
        visible=self.left_panel.isVisible()
        self.left_panel.setVisible(not visible)
        self.left_toggle.setText('›' if visible else '‹')
        self.left_toggle.setToolTip('左パネルを表示' if visible else '左パネルを折りたたむ')

    def toggle_right_panel(self):
        if not hasattr(self,'right_panel'): return
        visible=self.right_panel.isVisible()
        self.right_panel.setVisible(not visible)
        self.right_toggle.setText('‹' if visible else '›')
        self.right_toggle.setToolTip('右パネルを表示' if visible else '右パネルを折りたたむ')

    def toggle_focus_view(self):
        # F toggles an image-first view while preserving each panel's previous state.
        if not hasattr(self,'_focus_view_on'): self._focus_view_on=False
        if not self._focus_view_on:
            self._focus_prev=(self.left_panel.isVisible(),self.right_panel.isVisible())
            self.left_panel.hide(); self.right_panel.hide(); self.left_toggle.setText('›'); self.right_toggle.setText('‹')
            self._focus_view_on=True; self.statusBar().showMessage('画像優先表示：Fで元に戻します',2500)
        else:
            lv,rv=getattr(self,'_focus_prev',(True,True)); self.left_panel.setVisible(lv); self.right_panel.setVisible(rv)
            self.left_toggle.setText('‹' if lv else '›'); self.right_toggle.setText('›' if rv else '‹')
            self._focus_view_on=False

    def eventFilter(self,obj,event):
        if event.type()==QEvent.Type.KeyPress and event.modifiers()==Qt.KeyboardModifier.NoModifier:
            focus=QApplication.focusWidget()
            editing=isinstance(focus,(QDoubleSpinBox,QLineEdit,QSlider))
            if not editing:
                if event.key()==Qt.Key.Key_Left:
                    self.prev_image(); return True
                if event.key()==Qt.Key.Key_Right:
                    self.next_image(); return True
                if event.key()==Qt.Key.Key_F:
                    self.toggle_focus_view(); return True
        return super().eventFilter(obj,event)

    def keyPressEvent(self,e):
        key=e.key(); focus=QApplication.focusWidget()
        # Keep arrow-key editing inside numeric/text controls and sliders.
        editing=isinstance(focus,(QDoubleSpinBox,QLineEdit,QSlider))
        if key==Qt.Key.Key_Left and not editing:
            self.prev_image(); e.accept(); return
        if key==Qt.Key.Key_Right and not editing:
            self.next_image(); e.accept(); return
        if key==Qt.Key.Key_F and not editing:
            self.toggle_focus_view(); e.accept(); return
        super().keyPressEvent(e)

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
            if str(p) in self.frames:
                self.frame_quad=[tuple(q) for q in self.frames[str(p)]]
            elif getattr(self,'auto_frame_check',None) is not None and self.auto_frame_check.isChecked():
                l,t,r,b=detect_frame(self.original)
                self.frame_quad=[(l,t),(r,t),(r,b),(l,b)]
                self.frames[str(p)]=[tuple(q) for q in self.frame_quad]
            else:
                self.frame_quad=[(0.,0.),(1.,0.),(1.,1.),(0.,1.)]
            pd=self.perspective_by_image.get(str(p),{'vp1':(-0.30,0.50),'vp2':(1.30,0.50),'vp3':(0.50,-0.65),'eye':0.50,'lines':self.default_perspective_lines()})
            self.vp1=tuple(pd.get('vp1',(-0.30,0.50))); self.vp2=tuple(pd.get('vp2',(1.30,0.50))); self.vp3=tuple(pd.get('vp3',(0.50,-0.65))); self.eye_level_y=float(pd.get('eye',0.50))
            import copy
            self.perspective_lines=copy.deepcopy(pd.get('lines',self.default_perspective_lines()))
            saved_complete=pd.get('complete',{})
            self._persp_axis_complete={k:bool(saved_complete.get(k,False)) for k in ('vp1','vp2','vp3')}
            self.vp_at_infinity={k:bool(pd.get('infinity',{}).get(k,False)) for k in ('vp1','vp2','vp3')}
            self.vp3_at_infinity=self.vp_at_infinity['vp3']
            self._persp_anchor_touched={}
            self.active_perspective_axis='vp1'; self.perspective_step=1 if self._persp_axis_complete.get('vp1',False) else 0
            self.update_perspective_panel_state(); self.update_perspective_labels()
            self.update_display(); self.file_label.setText(f'{p.name}\n{self.current_index+1} / {len(self.paths)}\n{self.original.width} × {self.original.height} px'); self.statusBar().showMessage(str(p))
        except Exception as ex:self.file_label.setText(f'読み込み失敗: {p.name}\n{ex}')
        self._update_nav()
    def _display_adjusted_image(self):
        if self.original is None:return None
        im=self.original.copy(); im=ImageEnhance.Brightness(im).enhance(self.sliders['brightness'].value()/100); im=ImageEnhance.Contrast(im).enhance(self.sliders['contrast'].value()/100); im=ImageEnhance.Color(im).enhance(self.sliders['saturation'].value()/100)
        gam=self.sliders['gamma'].value()/100
        if abs(gam-1)>0.001:
            inv=1/gam; lut=[min(255,int((i/255)**inv*255+.5)) for i in range(256)]; im=im.point(lut*3)
        return im

    def update_display(self):
        im=self._display_adjusted_image()
        if im is None:return
        self.canvas.pixmap=pil_to_pixmap(im); self.canvas.update()

    def _render_export_image(self):
        """Safely export the visible image area with overlays.

        V5.23 deliberately avoids rendering a newly-created hidden QWidget.
        On Windows/PySide6 that path can terminate the process inside Qt without
        raising a Python exception.  Instead we grab the already-visible canvas,
        crop exactly to its current image rectangle, then scale to source pixels.
        """
        im=self._display_adjusted_image()
        if im is None:return None
        w,h=im.size
        old_zoom=self.view_zoom
        old_ws=self.workspace_scale.value()
        old_export=self.export_render_mode
        try:
            # Use the live canvas/backing store: this is substantially safer on Windows.
            self.export_render_mode=True
            self.view_zoom=1.0
            if old_ws != 100:
                self.workspace_scale.setValue(100)
            self.canvas.repaint()
            QApplication.processEvents()
            r=self.canvas.image_rect.toAlignedRect().intersected(self.canvas.rect())
            if r.width()<2 or r.height()<2:
                return None
            pm=self.canvas.grab(r)
            if pm.isNull():
                return None
            qimg=pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
            return qimg.scaled(w,h,Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)
        finally:
            self.export_render_mode=old_export
            self.view_zoom=old_zoom
            if self.workspace_scale.value()!=old_ws:
                self.workspace_scale.setValue(old_ws)
            self.canvas.repaint()

    def export_current_image(self):
        if self.original is None or not (0<=self.current_index<len(self.paths)):
            self.statusBar().showMessage('書き出す画像がありません',4000); return
        self.save_frame(); self.save_perspective()
        default=str(self.paths[self.current_index].with_name(self.paths[self.current_index].stem+'_guides.png'))
        destination,_=QFileDialog.getSaveFileName(self,'現在の画像を書き出し',default,'PNG (*.png)')
        if not destination:return
        if not destination.lower().endswith('.png'): destination += '.png'
        qimg=self._render_export_image()
        if qimg is None or not qimg.save(destination,'PNG'):
            QMessageBox.warning(self,'画像書き出し','画像の保存に失敗しました。'); return
        self.statusBar().showMessage(f'画像を書き出しました: {destination}',6000)

    def batch_export_images(self):
        if self.batch_export_running:
            self.statusBar().showMessage('一括書き出しはすでに実行中です',4000); return
        if not self.paths:
            self.statusBar().showMessage('書き出す画像がありません',4000); return
        out=QFileDialog.getExistingDirectory(self,'全画像の保存先')
        if not out:return
        self.save_frame(); self.save_perspective()
        self.batch_export_running=True; self.batch_export_cancelled=False
        self.export_batch_button.setEnabled(False); self.export_cancel_button.setEnabled(True)
        self._batch_state={
            'out':Path(out),'paths':list(self.paths),'pos':0,'saved':0,'failures':[],
            'return_index':self.current_index,'futures':[]
        }
        self.export_progress_label.setText(f'書き出し: 0 / {len(self.paths)}')
        self.statusBar().showMessage('一括書き出しを開始しました。操作は続けられます。')
        QTimer.singleShot(0,self._batch_export_step)

    @staticmethod
    def _save_export_qimage(qimg,destination):
        try:
            ok=qimg.save(str(destination),'PNG')
            return bool(ok), str(destination)
        except Exception as ex:
            return False, str(ex)

    def _batch_export_step(self):
        st=getattr(self,'_batch_state',None)
        if not self.batch_export_running or st is None:return
        if self.batch_export_cancelled or st['pos']>=len(st['paths']):
            self._finish_batch_export(); return

        # Snapshot the user's current image. Rendering remains on Qt's GUI thread for safety,
        # while PNG compression/writing is sent to worker threads. One frame per event-loop
        # turn keeps the application responsive instead of locking the UI for the whole batch.
        user_index=self.current_index
        idx=st['pos']; path=st['paths'][idx]
        try:
            self.current_index=idx
            self.load_current()
            qimg=self._render_export_image()
            dest=st['out']/(path.stem+'_guides.png')
            if dest.exists(): dest=st['out']/(f'{path.stem}_{idx+1:04d}_guides.png')
            if qimg is None:
                st['failures'].append(path.name)
            else:
                # Detach image bytes from the GUI backing store before worker-thread save.
                detached=qimg.copy()
                fut=self.batch_export_executor.submit(self._save_export_qimage,detached,dest)
                st['futures'].append((path.name,fut))
        except Exception as ex:
            st['failures'].append(f'{path.name}: {ex}')
        finally:
            # Restore whatever the user was looking at before this short render slice.
            if 0<=user_index<len(self.paths):
                self.current_index=user_index
                self.load_current()

        st['pos']+=1
        self.export_progress_label.setText(f"書き出し: {st['pos']} / {len(st['paths'])}")
        self.statusBar().showMessage(f"一括書き出し {st['pos']}/{len(st['paths'])} — バックグラウンド保存中")
        QTimer.singleShot(1,self._batch_export_step)

    def _finish_batch_export(self):
        st=getattr(self,'_batch_state',None)
        if st is None:return
        # Poll workers without freezing the GUI; finish only after outstanding PNG writes complete.
        pending=[x for x in st['futures'] if not x[1].done()]
        if pending:
            self.export_progress_label.setText(f"書き出し: {st['pos']} / {len(st['paths'])}（保存完了待ち {len(pending)}）")
            QTimer.singleShot(50,self._finish_batch_export); return
        for name,fut in st['futures']:
            try:
                ok,msg=fut.result()
                if ok: st['saved']+=1
                else: st['failures'].append(f'{name}: {msg}')
            except Exception as ex:
                st['failures'].append(f'{name}: {ex}')

        cancelled=self.batch_export_cancelled
        self.batch_export_running=False; self.batch_export_cancelled=False
        self.export_batch_button.setEnabled(True); self.export_cancel_button.setEnabled(False)
        self.export_progress_label.setText('書き出し: キャンセル済み' if cancelled else '書き出し: 完了')

        box=QMessageBox(self)
        box.setWindowTitle('一括書き出し')
        box.setIcon(QMessageBox.Icon.Information if not st['failures'] else QMessageBox.Icon.Warning)
        total=len(st['paths'])
        box.setText(('キャンセルしました。' if cancelled else '一括書き出しが完了しました。')+f'\n{st["saved"]} / {total}枚を保存しました。')
        detail=f'保存先:\n{st["out"]}'
        if st['failures']:
            detail+=f'\n\n失敗: {len(st["failures"])}枚\n'+'\n'.join(st['failures'][:8])
        box.setInformativeText(detail)
        box.setMinimumWidth(680)
        box.exec()
        self.statusBar().showMessage('一括書き出し処理を終了しました',6000)
        self._batch_state=None

    def reset_display(self):
        for k in self.sliders:self.sliders[k].setValue(100)
    def auto_frame(self):
        if self.original is None:return
        l,t,r,b=detect_frame(self.original); self.frame_quad=[(l,t),(r,t),(r,b),(l,b)]; self.save_frame(); self.canvas.update(); self.statusBar().showMessage('黒帯フレームを自動検出しました。必要なら緑の四隅または辺をドラッグしてください。',5000)
    def apply_current_frame_to_all(self):
        if self.original is None or not self.paths:return
        self.save_frame()
        common=[tuple(q) for q in self.frame_quad]
        for p in self.paths:
            self.frames[str(p)]=[tuple(q) for q in common]
        self.statusBar().showMessage(f'現在のフレームを全{len(self.paths)}画像に適用しました',5000)

    def cancel_batch_export(self):
        if self.batch_export_running:
            self.batch_export_cancelled=True
            self.export_progress_label.setText('書き出し: キャンセル処理中…')

    def reset_frame(self): self.frame_quad=[(0.,0.),(1.,0.),(1.,1.),(0.,1.)]; self.save_frame(); self.canvas.update()
    def save_frame(self):
        if 0<=self.current_index<len(self.paths): self.frames[str(self.paths[self.current_index])]=[tuple(q) for q in self.frame_quad]
    def prev_image(self):
        self.save_frame(); self.save_perspective()
        if self.current_index>0:self.current_index-=1;self.load_current()
    def next_image(self):
        self.save_frame(); self.save_perspective()
        if self.current_index+1<len(self.paths):self.current_index+=1;self.load_current()
    def _update_nav(self): self.prev_button.setEnabled(self.current_index>0); self.next_button.setEnabled(0<=self.current_index<len(self.paths)-1)
    def refresh(self): self.canvas.update()

    def default_perspective_lines(self):
        return {
            'vp1': [[(.16,.34),(.46,.43)],[(.16,.72),(.46,.59)]],
            'vp2': [[(.54,.43),(.84,.34)],[(.54,.59),(.84,.72)]],
            'vp3': [[(.36,.78),(.43,.30)],[(.64,.78),(.57,.30)]],
        }
    def _axis_observation_h(self,name):
        """Return the manual axis vanishing point as a homogeneous pixel point.

        The two user strokes are treated as observations only.  A point with w≈0 is
        naturally a vanishing point at infinity; no arbitrary finite-VP clamp is used.
        """
        if self.original is None:
            return None
        lines=self.perspective_lines.get(name,[])
        if len(lines)<2:
            return None
        w=float(self.original.width); h=float(self.original.height)

        def line_h(seg):
            (x1,y1),(x2,y2)=seg
            p1=np.array([x1*w,y1*h,1.0],dtype=float)
            p2=np.array([x2*w,y2*h,1.0],dtype=float)
            l=np.cross(p1,p2)
            n=math.hypot(float(l[0]),float(l[1]))
            if n<1e-9:
                return None
            return l/n

        l1=line_h(lines[0]); l2=line_h(lines[1])
        if l1 is None or l2 is None:
            return None
        q=np.cross(l1,l2)
        n=float(np.linalg.norm(q))
        if n<1e-12:
            return None
        return q/n

    def _camera_dir_from_h(self,q,cx,cy,f):
        """Back-project a homogeneous image point/direction through K^-1."""
        if q is None or f<=1e-9:
            return None
        q=np.asarray(q,dtype=float)
        d=np.array([(q[0]-cx*q[2])/f,
                    (q[1]-cy*q[2])/f,
                    q[2]],dtype=float)
        n=float(np.linalg.norm(d))
        if n<1e-12:
            return None
        return d/n

    def _project_camera_dir(self,d,cx,cy,f):
        """Project a camera-space direction to a homogeneous image vanishing point."""
        d=np.asarray(d,dtype=float)
        return np.array([f*d[0]+cx*d[2],
                         f*d[1]+cy*d[2],
                         d[2]],dtype=float)

    def _camera_solution_fit_error_deg(self,projected):
        """RMS angular error between solved axis directions and the user's six lines."""
        if self.original is None:
            return None
        w=float(self.original.width); h=float(self.original.height)
        errs=[]
        for key,q in projected.items():
            if q is None:
                continue
            lines=self.perspective_lines.get(key,[])
            for seg in lines[:2]:
                x1,y1=seg[0][0]*w,seg[0][1]*h
                x2,y2=seg[1][0]*w,seg[1][1]*h
                ux=x2-x1; uy=y2-y1
                un=math.hypot(ux,uy)
                if un<1e-8:
                    continue
                ux/=un; uy/=un

                if abs(float(q[2]))<1e-8:
                    vx=float(q[0]); vy=float(q[1])
                else:
                    vpx=float(q[0]/q[2]); vpy=float(q[1]/q[2])
                    mx=(x1+x2)*0.5; my=(y1+y2)*0.5
                    vx=vpx-mx; vy=vpy-my
                vn=math.hypot(vx,vy)
                if vn<1e-8:
                    continue
                vx/=vn; vy/=vn
                dot=max(-1.0,min(1.0,abs(ux*vx+uy*vy)))
                errs.append(math.degrees(math.acos(dot)))
        if not errs:
            return None
        return math.sqrt(sum(e*e for e in errs)/len(errs))

    def solve_camera_calibration(self):
        """Site-style pure-manual camera calibration.

        The six manual lines are observations.  We solve one shared intrinsic camera
        (principal point + focal length), back-project the X/Y/Z vanishing directions,
        fit the nearest orthonormal world-axis frame, then re-project one coherent
        X/Y/Z vanishing-point system.  No automatic image analysis or learned data is used.
        """
        if self.original is None or np is None:
            return False

        complete=[k for k in ('vp1','vp2','vp3') if self._persp_axis_complete.get(k,False)]
        if len(complete)<2:
            if hasattr(self,'camera_solve_label'):
                self.camera_solve_label.setText('X/Zなど2軸以上を確定してください。')
            return False

        obs={k:self._axis_observation_h(k) for k in complete}
        if any(obs[k] is None for k in complete):
            return False

        w=float(self.original.width); h=float(self.original.height)
        cx0=w*0.5; cy0=h*0.5

        # Initial focal estimate from any finite orthogonal pair using the centered-principal-point formula.
        f_candidates=[]
        for i,k1 in enumerate(complete):
            q1=obs[k1]
            if abs(float(q1[2]))<1e-8: continue
            v1=(float(q1[0]/q1[2]),float(q1[1]/q1[2]))
            for k2 in complete[i+1:]:
                q2=obs[k2]
                if abs(float(q2[2]))<1e-8: continue
                v2=(float(q2[0]/q2[2]),float(q2[1]/q2[2]))
                f2=-((v1[0]-cx0)*(v2[0]-cx0)+(v1[1]-cy0)*(v2[1]-cy0))
                if math.isfinite(f2) and f2>25.0:
                    f_candidates.append(math.sqrt(f2))
        f0=sorted(f_candidates)[len(f_candidates)//2] if f_candidates else w

        # With only two axes the principal point is under-constrained, so keep it centered.
        # With all three axes, search cx/cy/f for the K that makes the three back-projected
        # directions maximally orthogonal.
        def objective(cx,cy,f):
            dirs=[]
            for k in complete:
                d=self._camera_dir_from_h(obs[k],cx,cy,f)
                if d is None: return 1e9
                dirs.append(d)
            e=0.0
            n=0
            for i in range(len(dirs)):
                for j in range(i+1,len(dirs)):
                    dot=float(np.dot(dirs[i],dirs[j]))
                    e+=dot*dot; n+=1
            e/=max(1,n)
            # Light principal-point regularisation; enough to prevent implausible drift.
            e+=0.002*((cx-cx0)/max(w,1.0))**2
            e+=0.002*((cy-cy0)/max(h,1.0))**2
            return e

        if len(complete)>=3:
            best=(objective(cx0,cy0,f0),cx0,cy0,max(0.12*w,min(8*w,f0)))
            # Coarse search.
            for ox in (-0.15,-0.075,0.0,0.075,0.15):
                for oy in (-0.15,-0.075,0.0,0.075,0.15):
                    for fs in (0.35,0.5,0.7,1.0,1.4,2.0,3.0,5.0):
                        cx=cx0+ox*w; cy=cy0+oy*h; f=fs*w
                        val=objective(cx,cy,f)
                        if val<best[0]:
                            best=(val,cx,cy,f)
            _,cx,cy,f=best
            # Pattern search refinement.
            sx,sy,sf=0.06*w,0.06*h,0.22
            for _ in range(42):
                improved=False
                base=objective(cx,cy,f)
                candidates=[
                    (cx+sx,cy,f),(cx-sx,cy,f),
                    (cx,cy+sy,f),(cx,cy-sy,f),
                    (cx,cy,f*math.exp(sf)),(cx,cy,f*math.exp(-sf)),
                ]
                for tx,ty,tf in candidates:
                    if not (cx0-0.25*w<=tx<=cx0+0.25*w and cy0-0.25*h<=ty<=cy0+0.25*h and 0.12*w<=tf<=8*w):
                        continue
                    val=objective(tx,ty,tf)
                    if val+1e-12<base:
                        cx,cy,f=tx,ty,tf; base=val; improved=True
                if not improved:
                    sx*=0.62; sy*=0.62; sf*=0.62
                    if sx<0.05 and sy<0.05 and sf<1e-4:
                        break
        else:
            cx,cy,f=cx0,cy0,max(0.12*w,min(8*w,f0))

        # Back-project observed directions with solved K.
        raw={k:self._camera_dir_from_h(obs[k],cx,cy,f) for k in complete}

        # Fit the nearest orthonormal set to the observed world-axis directions.
        # World order is X, Y(up), Z; internal keys are vp1, vp3, vp2.
        world_order=['vp1','vp3','vp2']
        present=[k for k in world_order if k in raw]
        M=np.column_stack([raw[k] for k in present])
        U,_,Vt=np.linalg.svd(M,full_matrices=False)
        Q=U@Vt
        solved_dirs={k:Q[:,i] for i,k in enumerate(present)}

        # If only X and Z are present, derive a coherent Y direction for the grid.
        if len(present)==2:
            if set(present)=={'vp1','vp2'}:
                x=solved_dirs['vp1']; z=solved_dirs['vp2']
                y=np.cross(z,x); y/=max(1e-12,float(np.linalg.norm(y)))
                solved_dirs['vp3']=y
            elif set(present)=={'vp1','vp3'}:
                x=solved_dirs['vp1']; y=solved_dirs['vp3']
                z=np.cross(x,y); z/=max(1e-12,float(np.linalg.norm(z)))
                solved_dirs['vp2']=z
            elif set(present)=={'vp2','vp3'}:
                z=solved_dirs['vp2']; y=solved_dirs['vp3']
                x=np.cross(y,z); x/=max(1e-12,float(np.linalg.norm(x)))
                solved_dirs['vp1']=x

        projected={k:self._project_camera_dir(d,cx,cy,f) for k,d in solved_dirs.items()}

        # Push the coherent camera solution into the existing display layer.
        self.camera_inf_dir={'vp1':None,'vp2':None,'vp3':None}
        for key,attr in [('vp1','vp1'),('vp2','vp2'),('vp3','vp3')]:
            q=projected.get(key)
            if q is None: continue
            # Treat directions with very small projective w as infinity.
            scale=max(1.0,math.hypot(float(q[0]),float(q[1])))
            is_inf=abs(float(q[2])) < 1e-5*scale
            self.vp_at_infinity[key]=bool(is_inf)
            if is_inf:
                dx=float(q[0]); dy=float(q[1]); n=math.hypot(dx,dy)
                if n>1e-9:
                    self.camera_inf_dir[key]=(dx/n,dy/n)
            else:
                px=float(q[0]/q[2]); py=float(q[1]/q[2])
                setattr(self,attr,(px/w,py/h))

        self.vp3_at_infinity=self.vp_at_infinity.get('vp3',False)

        # Horizon / eye-level from coherent X and Z when both are finite.
        if not self.vp_at_infinity.get('vp1',False) and not self.vp_at_infinity.get('vp2',False):
            x1,y1=self.vp1; x2,y2=self.vp2
            self.eye_level_y=(y1+(0.5-x1)*(y2-y1)/(x2-x1)) if abs(x2-x1)>1e-12 else (y1+y2)*0.5

        err=self._camera_solution_fit_error_deg(projected)
        self.camera_solve_error=err
        self.camera_solution={
            'cx':cx,'cy':cy,'fpx':f,
            'hfov':math.degrees(2.0*math.atan(w/(2.0*f))),
            'vfov':math.degrees(2.0*math.atan(h/(2.0*f))),
            'eq35':36.0*f/w,
            'projected':{k:[float(x) for x in q] for k,q in projected.items()},
            'error_deg':err,
        }

        if hasattr(self,'camera_solve_label'):
            errtxt='—' if err is None else f'{err:.2f}°'
            self.camera_solve_label.setText(
                f'解決済み  |  Error {errtxt}  |  '
                f'Lens {self.camera_solution["eq35"]:.0f}mm eq.  |  '
                f'H-FOV {self.camera_solution["hfov"]:.1f}°'
            )

        self.update_perspective_labels()
        self.refresh()
        return True

    def solve_perspective_axis(self,name):
        """Update the current manual axis observation, then solve one shared camera."""
        q=self._axis_observation_h(name)
        if q is None or self.original is None:
            return False
        w=float(self.original.width); h=float(self.original.height)

        # Raw per-axis preview before the shared camera solution is available.
        scale=max(1.0,math.hypot(float(q[0]),float(q[1])))
        is_inf=abs(float(q[2])) < 1e-5*scale
        self.vp_at_infinity[name]=bool(is_inf)
        if is_inf:
            n=math.hypot(float(q[0]),float(q[1]))
            if n>1e-9:
                self.camera_inf_dir[name]=(float(q[0])/n,float(q[1])/n)
        else:
            px=float(q[0]/q[2]); py=float(q[1]/q[2])
            if name=='vp1': self.vp1=(px/w,py/h)
            elif name=='vp2': self.vp2=(px/w,py/h)
            else: self.vp3=(px/w,py/h)

        self.vp3_at_infinity=self.vp_at_infinity.get('vp3',False)

        # Shared camera solve becomes available from two completed axes onward.
        if sum(1 for k in ('vp1','vp2','vp3') if self._persp_axis_complete.get(k,False))>=2:
            self.solve_camera_calibration()
        else:
            self.update_perspective_labels()
            if hasattr(self,'canvas'): self.canvas.update()
        return True

    def solve_all_perspective_axes(self):
        for n in ('vp1','vp2','vp3'): self.solve_perspective_axis(n)
    def reset_zoom(self):
        self.view_zoom=1.0; self.update_zoom_label(); self.refresh()
    def update_zoom_label(self):
        if hasattr(self,'zoom_label'): self.zoom_label.setText(f'{round(self.view_zoom*100):d}%')

    def save_perspective(self):
        """Store only manually created perspective state for this image."""
        if 0<=self.current_index<len(self.paths):
            import copy
            self.perspective_by_image[str(self.paths[self.current_index])]={
                'vp1':tuple(self.vp1),
                'vp2':tuple(self.vp2),
                'vp3':tuple(self.vp3),
                'eye':float(self.eye_level_y),
                'lines':copy.deepcopy(self.perspective_lines),
                'complete':dict(self._persp_axis_complete),
                'infinity':dict(getattr(self,'vp_at_infinity',{'vp1':False,'vp2':False,'vp3':False})),
                'vp3_at_infinity':bool(getattr(self,'vp3_at_infinity',False)),
                'camera_solution':getattr(self,'camera_solution',None),
            }

    def reset_perspective(self):
        self.vp1=(-0.30,0.50)
        self.vp2=(1.30,0.50)
        self.vp3=(0.50,-0.65)
        self.eye_level_y=0.50
        self.view_zoom=1.0
        self.active_perspective_axis='vp1'
        self.perspective_step=0
        self._persp_axis_complete={'vp1':False,'vp2':False,'vp3':False}
        self._persp_anchor_touched={}
        self.vp_at_infinity={'vp1':False,'vp2':False,'vp3':False}
        self.camera_solution=None
        self.camera_inf_dir={'vp1':None,'vp2':None,'vp3':None}
        self.camera_solve_error=None
        self.vp3_at_infinity=False
        self.show_perspective_grid_default=True
        self.perspective_lines=self.default_perspective_lines()
        self.update_perspective_panel_state()
        self.update_perspective_labels()
        self.save_perspective()
        self.refresh()

    def update_perspective_labels(self):
        inf=getattr(self,'vp_at_infinity',{'vp1':False,'vp2':False,'vp3':False})
        def vtxt(key,xy,label):
            if inf.get(key,False):
                return f'{label}: ∞'
            return f'{label}: ({xy[0]:.2f}, {xy[1]:.2f})'
        txt=(vtxt('vp1',self.vp1,'X軸（水平・左右方向）')+'  /  '+
             vtxt('vp2',self.vp2,'Z軸（奥行き方向）')+'\n'+
             vtxt('vp3',self.vp3,'Y軸（垂直・上下方向）'))
        if getattr(self,'camera_solution',None):
            err=self.camera_solution.get('error_deg')
            if err is not None:
                txt+=f'\nSolve error: {err:.2f}°'
        if hasattr(self,'persp_label'):
            self.persp_label.setText(txt)
        if hasattr(self,'analysis_perspective'):
            self.analysis_perspective.setText(txt)
        self.update_lens_estimate()

    def _manual_axis_vp_normalized(self, name):
        """Return the raw VP from the user's two manual calibration lines.

        This bypasses the orthonormalized camera solution, so lens estimation can be
        evaluated independently from the grid solver.
        """
        if self.original is None:
            return None
        lines=self.perspective_lines.get(name,[])
        if len(lines)<2:
            return None
        ip=infinite_line_intersection(lines[0][0],lines[0][1],lines[1][0],lines[1][1])
        if ip is None:
            return None
        x,y=float(ip[0]),float(ip[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        return (x,y)

    def _lens_from_manual_xz(self):
        """Primary lens estimate from raw manual X/Z vanishing points."""
        if self.original is None:
            return None
        if not (self._persp_axis_complete.get('vp1') and self._persp_axis_complete.get('vp2')):
            return None
        a=self._manual_axis_vp_normalized('vp1')
        b=self._manual_axis_vp_normalized('vp2')
        if a is None or b is None:
            return None
        fpx=self._pair_focal_pixels(a,b)
        if fpx is None or not math.isfinite(fpx):
            return None
        w=float(self.original.width); h=float(self.original.height)
        return {
            'fpx':fpx,
            'eq35':36.0*fpx/w,
            'hfov':math.degrees(2.0*math.atan(w/(2.0*fpx))),
            'vfov':math.degrees(2.0*math.atan(h/(2.0*fpx))),
            'vp1':a,'vp2':b,
        }

    def _lens_sensitivity_range(self, samples=96):
        """Monte-Carlo sensitivity of lens estimate to small manual-line placement error.

        The current perspective/grid solution is NOT modified. We perturb only copies of
        the four X/Z endpoints by a few image pixels and recompute the raw XZ lens.
        """
        if self.original is None:
            return None
        if not (self._persp_axis_complete.get('vp1') and self._persp_axis_complete.get('vp2')):
            return None

        w=float(self.original.width); h=float(self.original.height)
        if w < 2 or h < 2:
            return None

        base_x=self.perspective_lines.get('vp1',[])
        base_z=self.perspective_lines.get('vp2',[])
        if len(base_x)<2 or len(base_z)<2:
            return None

        # ~4 px 1-sigma, capped relative to image size. This represents careful manual placement.
        sx=min(4.0/max(w,1.0),0.004)
        sy=min(4.0/max(h,1.0),0.004)
        vals=[]
        rng=random.Random(137)

        def perturbed_vp(lines):
            pls=[]
            for ln in lines[:2]:
                p1=(ln[0][0]+rng.gauss(0,sx), ln[0][1]+rng.gauss(0,sy))
                p2=(ln[1][0]+rng.gauss(0,sx), ln[1][1]+rng.gauss(0,sy))
                pls.append((p1,p2))
            return infinite_line_intersection(pls[0][0],pls[0][1],pls[1][0],pls[1][1])

        for _ in range(max(24,int(samples))):
            vx=perturbed_vp(base_x)
            vz=perturbed_vp(base_z)
            if vx is None or vz is None:
                continue
            try:
                fpx=self._pair_focal_pixels(vx,vz)
            except Exception:
                fpx=None
            if fpx is None or not math.isfinite(fpx):
                continue
            eq35=36.0*fpx/w
            if 4.0 <= eq35 <= 400.0:
                vals.append(eq35)

        if len(vals)<12:
            return None
        vals=sorted(vals)
        def q(frac):
            pos=(len(vals)-1)*frac
            lo=int(math.floor(pos)); hi=int(math.ceil(pos))
            if lo==hi:return vals[lo]
            t=pos-lo
            return vals[lo]*(1-t)+vals[hi]*t
        return {
            'p10':q(0.10),
            'p50':q(0.50),
            'p90':q(0.90),
            'spread':q(0.90)-q(0.10),
            'n':len(vals),
        }

    def _pair_focal_pixels(self, a, b):
        """Focal length in pixels from an orthogonal VP pair with principal point at image center."""
        if self.original is None:
            return None
        w=float(self.original.width); h=float(self.original.height)
        ax=(a[0]-0.5)*w; ay=(a[1]-0.5)*h
        bx=(b[0]-0.5)*w; by=(b[1]-0.5)*h
        f2=-(ax*bx + ay*by)
        if not math.isfinite(f2) or f2 <= 1.0:
            return None
        return math.sqrt(f2)

    def estimate_lens(self):
        """Lens estimate decoupled from Camera Solver.

        Primary = raw manual X/Z VP pair.
        Camera Solver focal length = secondary diagnostic only.
        Y/VP3 never pulls the primary lens value.
        """
        if self.original is None:
            return None

        primary=self._lens_from_manual_xz()
        if primary is None:
            return None

        eq35=float(primary['eq35'])
        hfov=float(primary['hfov'])
        vfov=float(primary['vfov'])

        # Independent camera-solver focal value for comparison only.
        camera_eq=None
        if getattr(self,'camera_solution',None):
            try:
                camera_eq=float(self.camera_solution.get('eq35'))
            except Exception:
                camera_eq=None

        sens=self._lens_sensitivity_range()
        if sens is not None:
            lo=max(4.0,float(sens['p10']))
            hi=float(sens['p90'])
            rel=(hi-lo)/max(eq35,1e-6)
            if rel < 0.18:
                lens_conf='高'
            elif rel < 0.40:
                lens_conf='中'
            else:
                lens_conf='低'
        else:
            # Conservative fallback: do not equate camera Solve error with lens confidence.
            lo=max(4.0,eq35*0.78)
            hi=eq35*1.22
            lens_conf='中'

        # Compare Camera Solver f only as a diagnostic.
        camera_delta=None
        if camera_eq is not None and math.isfinite(camera_eq):
            camera_delta=abs(camera_eq-eq35)/max(eq35,1e-6)

        if eq35 < 20: kind='超広角'
        elif eq35 < 35: kind='広角'
        elif eq35 < 60: kind='標準'
        elif eq35 < 100: kind='中望遠'
        else: kind='望遠'

        common=[12,14,16,18,20,21,24,28,32,35,40,50,58,65,85,100,135,200]
        candidates=sorted(common,key=lambda mm:abs(math.log(max(mm,1)/max(eq35,1))))[:3]
        candidates=sorted(candidates)

        reason='手動X/Z消失点を主推定'
        if camera_eq is not None:
            reason+=f' / Camera Solve比較 {camera_eq:.1f}mm'
            if camera_delta is not None and camera_delta>0.25:
                reason+='（差が大きいため要注意）'

        return {
            'eq35':eq35,
            'base35':eq35,
            'lo':lo,
            'hi':hi,
            'hfov':hfov,
            'vfov':vfov,
            'kind':kind,
            'confidence':lens_conf,
            'pairs':[('X×Z raw',primary['fpx'])],
            'spread':None,
            'reason':reason,
            'candidates':candidates,
            'autoq':None,
            'camera_eq35':camera_eq,
            'sensitivity':sens,
        }

    def update_lens_estimate(self):
        targets=[x for x in (getattr(self,'analysis_lens',None),getattr(self,'persp_lens',None)) if x is not None]
        detail_targets=[x for x in (getattr(self,'lens_detail',None),getattr(self,'persp_lens_detail',None)) if x is not None]
        if not targets:
            return
        if not (self._persp_axis_complete.get('vp1') and self._persp_axis_complete.get('vp2')):
            for t in targets: t.setText('VP1 と VP2 を確定すると推定を開始します。')
            for d in detail_targets: d.setText('2点透視を主推定、VP3は検証として使用します。')
            return
        est=self.estimate_lens()
        if est is None:
            for t in targets: t.setText('レンズ推定不可\nVP1/VP2 が直交方向として成立していない可能性があります。基準線を見直してください。')
            return
        cand=' / '.join(f'{x}mm' for x in est['candidates'])
        lens_text=(
            f"推定焦点距離： 約 {est['eq35']:.0f} mm（35mm換算）\n"
            f"推定レンジ： {est['lo']:.0f}–{est['hi']:.0f} mm\n"
            f"候補： {cand}\n"
            f"水平画角： 約 {est['hfov']:.1f}°  /  垂直画角： 約 {est['vfov']:.1f}°\n"
            f"レンズ傾向： {est['kind']}  /  レンズ推定信頼度： {est['confidence']}"
        )
        for t in targets: t.setText(lens_text)
        details=[]
        for label,val in est['pairs']:
            if val is not None:
                details.append(f"{label}: {36.0*val/max(float(self.original.width),1.0):.1f}mm相当")
        if est.get('camera_eq35') is not None:
            details.append(f"Camera Solver側: {est['camera_eq35']:.1f}mm相当")
        sens=est.get('sensitivity')
        if sens is not None:
            details.append(f"入力感度: {sens['p10']:.1f}–{sens['p90']:.1f}mm（10–90%）")
        details.append(f"判定理由: {est['reason']}")
        detail_text=' / '.join(details)
        detail_text += '\n※ Solve errorはパース整合度、レンズ推定信頼度とは別です。'
        for d in detail_targets: d.setText(detail_text)

    def comp_edit_toggled(self,on):
        if not on:
            self.selected_comp_guide=None; self.reset_comp_btn.setEnabled(False)
        self.refresh()
    def comp_guide_visibility_changed(self,on):
        sender=self.sender()
        mapping={self.show_radiating:'radiating',self.show_tunnel:'tunnel',self.show_golden_triangle:'golden_triangle',self.show_circle:'circle',self.show_cshape:'cshape',self.show_vshape:'vshape',self.show_double_diagonal:'double_diagonal',self.show_scurve:'scurve',self.show_lshape:'lshape',self.show_pyramid:'pyramid'}
        name=mapping.get(sender)
        if not on and self.selected_comp_guide==name:
            self.selected_comp_guide=None; self.reset_comp_btn.setEnabled(False)
        self.refresh()
    def reset_selected_comp_guide(self):
        name=self.selected_comp_guide
        defaults={
            'radiating': {'center': (.5,.5)}, 'tunnel': {'points': [(.18,.18),(.82,.18),(.82,.82),(.18,.82)]},
            'golden_triangle': {'points': [(0.,1.),(1.,0.),(0.,0.),(.42,1.),(.62,0.),(1.,1.)]},
            'circle': {'center': (.5,.5), 'rx': .34, 'ry': .42}, 'cshape': {'center': (.5,.5), 'rx': .34, 'ry': .42},
            'vshape': {'points': [(.18,0.),(.5,1.),(.82,0.)]}, 'double_diagonal': {'points': [(0.,.15),(.68,1.),(0.,.62),(1.,.08)]},
            'scurve': {'points': [(.72,.14),(.18,.05),(.18,.46),(.52,.48),(.86,.50),(.83,.91),(.28,.86)]},
            'lshape': {'points': [(.20,.14),(.20,.84),(.82,.84)]}, 'pyramid': {'points': [(.5,.12),(.14,.88),(.86,.88)]},
        }
        if name:
            import copy; self.comp_guides[name]=copy.deepcopy(defaults[name]); self.refresh()

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

    def choose_frame_color(self):
        c=QColorDialog.getColor(QColor(self.frame_color),self,'フレーム色')
        if c.isValid(): self.frame_color=c.name(); self.refresh()




if __name__=='__main__':
    app=QApplication(sys.argv); app.setApplicationName('Movie Shot Analyzer'); w=MovieShotAnalyzer(); w.show(); sys.exit(app.exec())
