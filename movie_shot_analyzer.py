from __future__ import annotations
import math, sys, json, os, copy
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
        self._pan_drag_start=None; self._pan_origin=(0.0,0.0)
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
        panx,pany=getattr(self.owner,'view_pan',(0.0,0.0))
        x=av.left()+(av.width()-sw)/2+panx
        y=av.top()+(av.height()-sh)/2+pany
        self.image_rect=QRectF(x,y,sw,sh)
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
                # Keep endpoint handles for BOTH calibration lines while this axis is selected.
                # This is intentionally VanishPoint-like: after line 2 appears (and after VP solve),
                # line 1 never becomes 'dead'. The user can keep tuning all four white points.
                handles_visible = (
                    not getattr(self.owner,'export_render_mode',False)
                    and key == self.owner.active_perspective_axis
                    and self.owner.show_perspective_handles.isChecked()
                    and (complete or touched or drawing_this)
                )
                if handles_visible:
                    outline=QPen(QColor(color)); outline.setWidthF(2.0); p.setPen(outline); p.setBrush(QColor('#ffffff'))
                    for ptxy in line:
                        hp=self._image_norm_to_point(*ptxy); p.drawEllipse(QRectF(hp.x()-5,hp.y()-5,10,10))
        # Optional refinement observations for X/Z/Y. Kept lighter than base strokes.
        for rkey in ('vp1','vp2','vp3'):
            extras=getattr(self.owner,'perspective_extra_lines',{}).get(rkey,[])
            if extras:
                basew=self.owner.perspective_line_width.value.value(); c=QColor(self.owner.vp_ray_colors.get(rkey,'#ffffff'))
                c.setAlpha(round(255*self.owner.perspective_alpha.value()/100*0.45)); pen=QPen(c); pen.setWidthF(max(0.5,basew)); pen.setStyle(Qt.PenStyle.DashLine); p.setPen(pen)
                for seg in extras: p.drawLine(self._image_norm_to_point(*seg[0]),self._image_norm_to_point(*seg[1]))
        if self.drag_item and self.drag_item[0]=='axis_refine_draw':
            rkey=self.drag_item[1]; seg=getattr(self,'_axis_refine_preview',None)
            if seg:
                c=QColor(self.owner.vp_ray_colors.get(rkey,'#ffffff')); c.setAlpha(220); pen=QPen(c); pen.setWidthF(max(1.0,self.owner.perspective_line_width.value.value()*2.0)); p.setPen(pen)
                p.drawLine(self._image_norm_to_point(*seg[0]),self._image_norm_to_point(*seg[1]))

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
        for name,xy in (('vp1',self.owner.vp1),('vp2',self.owner.vp2),('vp3',self.owner.vp3)):
            if not self.owner._persp_axis_complete.get(name,False): continue
            if getattr(self.owner,'vp_at_infinity',{}).get(name,False): continue
            pt=self._image_norm_to_point(*xy)
            if math.hypot(pos.x()-pt.x(),pos.y()-pt.y()) < 14:
                return ('perspective_vp',name)
        if self.owner.show_perspective_handles.isChecked():
            name=self.owner.active_perspective_axis
            complete=self.owner._persp_axis_complete.get(name,False)
            # Hit-test every visible endpoint on the selected axis, not only the current
            # pencil step. Once line 2 exists, line 1 remains fully editable.
            for li,line in enumerate(self.owner.perspective_lines[name][:2]):
                touched=bool(self.owner._persp_anchor_touched.get((name,li),set()))
                if not complete and not touched:
                    continue
                for ei,xy in enumerate(line):
                    pt=self._image_norm_to_point(*xy)
                    if math.hypot(pos.x()-pt.x(),pos.y()-pt.y())<15:
                        return ('perspective_anchor',name,li,ei)
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

        # H latch or held Space = view-only hand/pan tool.
        if getattr(self.owner,'hand_tool_latched',False) or getattr(self.owner,'space_pan_active',False):
            self.drag_item=('view_pan',)
            self._pan_drag_start=QPointF(pos)
            self._pan_origin=tuple(getattr(self.owner,'view_pan',(0.0,0.0)))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            e.accept(); return

        # Pencil-style calibration has canvas priority while the Perspective tab is active.
        # Existing white endpoint handles still win so a finished line can be fine-tuned.
        phit=self._perspective_hit(pos)
        in_image=self.image_rect.contains(pos)
        pencil_on=(hasattr(self.owner,'perspective_pencil') and self.owner.perspective_pencil.isChecked())
        persp_tab=(hasattr(self.owner,'right_tabs') and self.owner.right_tabs.currentIndex()==0)

        # One-shot X/Z/Y refinement stroke, stored separately from the two base lines.
        refine_key=getattr(self.owner,'axis_refine_mode',None)
        if persp_tab and pencil_on and in_image and refine_key:
            self.owner.push_perspective_undo(); nx,ny=self._point_to_image_norm(pos)
            self.drag_item=('axis_refine_draw',refine_key); self._persp_draw_start=(nx,ny); self._axis_refine_preview=[(nx,ny),(nx,ny)]
            self.setCursor(self._get_pencil_cursor()); self.update(); return

        if persp_tab and pencil_on and in_image and not phit:
            name=self.owner.active_perspective_axis
            # Once an axis is complete, a fresh pencil stroke is always an auxiliary
            # observation.  This prevents an accidental third stroke from resetting
            # the two authoritative base lines and removes the need to pre-select a count.
            if self.owner._persp_axis_complete.get(name,False):
                arr=self.owner.perspective_extra_lines.setdefault(name,[])
                if len(arr)>=6:
                    self.owner.statusBar().showMessage('補助線は各軸最大6本です',1800); return
                self.owner.push_perspective_undo(); nx,ny=self._point_to_image_norm(pos)
                self.drag_item=('axis_refine_draw',name); self._persp_draw_start=(nx,ny); self._axis_refine_preview=[(nx,ny),(nx,ny)]
                self.setCursor(self._get_pencil_cursor()); self.update(); return
            self.owner.push_perspective_undo()
            li=self.owner.perspective_step
            nx,ny=self._point_to_image_norm(pos)
            lines=[[tuple(pt) for pt in line] for line in self.owner.perspective_lines[name]]
            if li==1:
                locked=getattr(self.owner,'_persp_line1_locked',{}).get(name)
                if locked is not None:
                    lines[0]=[tuple(locked[0]),tuple(locked[1])]
                    self.owner._persp_anchor_touched[(name,0)]={0,1}
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
                self.owner.push_perspective_undo()
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
        if typ=='view_pan':
            if self._pan_drag_start is not None:
                dx=pos.x()-self._pan_drag_start.x(); dy=pos.y()-self._pan_drag_start.y()
                self.owner.view_pan=(self._pan_origin[0]+dx,self._pan_origin[1]+dy)
                self.update()
            return
        if typ=='axis_refine_draw':
            nx,ny=self._point_to_image_norm(pos); nx=max(-3.0,min(4.0,nx)); ny=max(-2.0,min(3.0,ny))
            self._axis_refine_preview=[tuple(self._persp_draw_start),(nx,ny)]; self.update(); return
        if typ=='perspective_draw':
            name,li=self.drag_item[1],self.drag_item[2]
            nx,ny=self._point_to_image_norm(pos)
            nx=max(-3.0,min(4.0,nx)); ny=max(-2.0,min(3.0,ny))
            lines=[[tuple(pt) for pt in line] for line in self.owner.perspective_lines[name]]
            lines[li]=[tuple(self._persp_draw_start),(nx,ny)]
            self.owner.perspective_lines[name]=lines
            # Keep pencil dragging lightweight. Solve VP/grid once on mouse release.
        elif typ=='perspective_vp':
            name=self.drag_item[1]; nx,ny=self._point_to_image_norm(pos)
            nx=max(-3.0,min(4.0,nx)); ny=max(-2.0,min(3.0,ny))
            if name=='vp1': self.owner.vp1=(nx,ny)
            elif name=='vp2': self.owner.vp2=(nx,ny)
            else: self.owner.vp3=(nx,ny)
            self.owner.vp_at_infinity[name]=False
            if name=='vp3': self.owner.vp3_at_infinity=False
            self.owner.align_calibration_lines_to_vp(name)
            self.owner.recompute_lens_from_current_vps()
            self.owner.update_perspective_labels()
        elif typ=='perspective_anchor':
            name,li,ei=self.drag_item[1],self.drag_item[2],self.drag_item[3]
            nx,ny=self._point_to_image_norm(pos); nx=max(-3.0,min(4.0,nx)); ny=max(-2.0,min(3.0,ny))
            # Move only the selected white anchor. While line 1 is being positioned,
            # do not let the hidden/default line 2 affect VP or eye-level calculations.
            lines=[[tuple(pt) for pt in line] for line in self.owner.perspective_lines[name]]
            lines[li][ei]=(nx,ny)
            self.owner.perspective_lines[name]=lines
            # Line 1 and line 2 are equally editable once both exist. Re-solve the VP
            # live while either line is adjusted. Keep the safety copy in sync so a
            # later line-2 redraw can never resurrect an older version of line 1.
            if li==0:
                self.owner._persp_line1_locked[name]=[tuple(lines[0][0]),tuple(lines[0][1])]
            other_ready=bool(self.owner._persp_anchor_touched.get((name,1-li),set()))
            if self.owner._persp_axis_complete.get(name,False) or other_ready:
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
        if self.drag_item and self.drag_item[0]=='view_pan':
            self.drag_item=None; self._pan_drag_start=None
            if getattr(self.owner,'hand_tool_latched',False) or getattr(self.owner,'space_pan_active',False):
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.unsetCursor()
            e.accept(); return
        if self.drag_item and self.drag_item[0].startswith('frame_'): self.owner.save_frame()

        if self.drag_item and self.drag_item[0]=='axis_refine_draw':
            key=self.drag_item[1]; line=getattr(self,'_axis_refine_preview',None); self.owner.axis_refine_mode=None
            if line is not None:
                dx=line[1][0]-line[0][0]; dy=line[1][1]-line[0][1]
                if math.hypot(dx,dy)>=0.0005:
                    arr=self.owner.perspective_extra_lines.setdefault(key,[])
                    if len(arr)<6: arr.append([tuple(line[0]),tuple(line[1])])
                    self.owner.recompute_after_axis_refinement(key); self.owner.save_perspective()
            self._axis_refine_preview=None; self.drag_item=None; self.owner.update_axis_refine_labels(); self.owner.refresh(); return

        if self.drag_item and self.drag_item[0]=='perspective_draw':
            name,li=self.drag_item[1],self.drag_item[2]
            line=self.owner.perspective_lines[name][li]
            dx=line[1][0]-line[0][0]; dy=line[1][1]-line[0][1]
            # Ignore accidental clicks; a real pencil stroke needs a measurable drag.
            if math.hypot(dx,dy) >= 0.0005:
                self.owner._persp_anchor_touched[(name,li)]={0,1}
                if li==0:
                    # Persist an independent safety copy before line 2 is armed.
                    self.owner._persp_line1_locked[name]=[tuple(line[0]),tuple(line[1])]
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
                if li==0:
                    # Always retain the edited first line as the authoritative copy.
                    line0=self.owner.perspective_lines[name][0]
                    self.owner._persp_line1_locked[name]=[tuple(line0[0]),tuple(line0[1])]
                    # Only arm a fresh line 2 the very first time line 1 is completed.
                    # If line 2 already exists, editing line 1 must NEVER erase/reset it.
                    line2_ready=bool(self.owner._persp_anchor_touched.get((name,1),set()))
                    if touched=={0,1} and not line2_ready and not self.owner._persp_axis_complete.get(name,False):
                        self.owner.begin_second_perspective_line(name)
                        self.owner.perspective_step=1
                        self.owner._persp_anchor_touched[(name,1)]=set()
                        self.owner.update_perspective_panel_state()
                    elif line2_ready or self.owner._persp_axis_complete.get(name,False):
                        self.owner.solve_perspective_axis(name)
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
        super().__init__(); self.setWindowTitle('Movie Shot Analyzer — v2.0.2 UI/Thumbnail Hotfix'); self.resize(1500,920); self.setMinimumSize(1050,680); self.setAcceptDrops(True)
        self.paths=[]; self.current_index=-1; self.original=None; self.frame_quad=[(0.,0.),(1.,0.),(1.,1.),(0.,1.)]; self.frames={}
        self.perspective_by_image={}
        # Pure Manual Perspective: no automatic-analysis data and no learning data
        # are loaded into the perspective engine. Only the current manual strokes count.
        self.perspective_source='manual'
        self.vp1=(-0.30,0.50); self.vp2=(1.30,0.50); self.vp3=(0.50,-0.65); self.eye_level_y=0.50; self.view_zoom=1.0
        self.view_pan=(0.0,0.0); self.hand_tool_latched=False; self.space_pan_active=False
        self._perspective_undo=[]; self._perspective_redo=[]; self._restoring_perspective_history=False
        self.active_perspective_axis='vp1'; self.perspective_step=0
        # First-stroke safety copy: once line 1 is released it is kept separately,
        # so beginning line 2 can never overwrite or visually lose it.
        self._persp_line1_locked={'vp1':None,'vp2':None,'vp3':None}
        # Optional extra observations for Z/depth. They refine VP2 without replacing
        # the original two calibration strokes.
        self.perspective_extra_lines={'vp1':[],'vp2':[],'vp3':[]}
        self.axis_refine_mode=None
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
        # Bottom thumbnail view is built lazily in small batches and cached.
        # Rebuilding every thumbnail on each shot change caused severe UI stalls.
        self.thumb_buttons=[]
        self._thumb_build_token=0
        self._thumb_build_index=0
        # Thumbnail filmstrip is virtual/lazy: buttons are cheap placeholders and only
        # thumbnails currently visible in the horizontal viewport are decoded.
        self._thumb_icon_cache={}
        self._thumb_visible_timer=None
        self._persp_anchor_touched={}; self._persp_axis_complete={'vp1':False,'vp2':False,'vp3':False}; self._build_ui(); self._style(); self.statusBar().showMessage('v2.0.5 — fixed XYZ labels + reliable thumbnail jump')
    def section(self,lay,text):
        lab=QLabel(text); lab.setObjectName('section'); lay.addWidget(lab)
    def _build_ui(self):
        root=QWidget(); self.setCentralWidget(root)
        # Capture Left/Right/F before child widgets consume them. Numeric/text controls keep their own arrow behavior.
        app=QApplication.instance()
        if app is not None: app.installEventFilter(self)
        root_v=QVBoxLayout(root); root_v.setContentsMargins(8,8,8,8); root_v.setSpacing(6)
        main_row=QWidget(); outer=QHBoxLayout(main_row); outer.setContentsMargins(0,0,0,0); outer.setSpacing(8)
        root_v.addWidget(main_row,1)
        cw=QWidget(); cw.setObjectName('controlsWidget'); c=QVBoxLayout(cw); c.setContentsMargins(6,6,6,6); c.setSpacing(2)
        title=QLabel('Movie Shot Analyzer'); title.setObjectName('appTitle'); c.addWidget(title)
        sub=QLabel('Perspective Tool + Lens Solver v2.0.2'); sub.setObjectName('subtitle'); c.addWidget(sub)
        a=QPushButton('画像を開く'); a.clicked.connect(self.choose_images); b=QPushButton('フォルダを開く'); b.clicked.connect(self.choose_folder); c.addWidget(a); c.addWidget(b)
        self.file_label=QLabel('画像未選択'); self.file_label.setWordWrap(True); self.file_label.setObjectName('fileLabel'); c.addWidget(self.file_label)
        nav=QHBoxLayout(); self.prev_button=QPushButton('◀ 前'); self.next_button=QPushButton('次 ▶'); self.prev_button.clicked.connect(self.prev_image); self.next_button.clicked.connect(self.next_image); nav.addWidget(self.prev_button); nav.addWidget(self.next_button); c.addLayout(nav)
        keyhint=QLabel('←→ 画像　F 画像優先　Space/H パン　Ctrl/Cmd+Z Undo'); keyhint.setObjectName('note'); keyhint.setWordWrap(True); c.addWidget(keyhint)

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
        c.addStretch(1)
        scroll=QScrollArea(); scroll.setObjectName('controlScroll'); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); scroll.setWidget(cw); scroll.setMinimumWidth(225); scroll.setMaximumWidth(255); self.left_panel=scroll; outer.addWidget(scroll,0)
        self.left_toggle=QPushButton('‹'); self.left_toggle.setObjectName('panelToggle'); self.left_toggle.setFixedWidth(22); self.left_toggle.setToolTip('左パネルを折りたたむ'); self.left_toggle.clicked.connect(self.toggle_left_panel); outer.addWidget(self.left_toggle,0)
        self.canvas=ImageCanvas(self); outer.addWidget(self.canvas,1)
        self.right_toggle=QPushButton('›'); self.right_toggle.setObjectName('panelToggle'); self.right_toggle.setFixedWidth(22); self.right_toggle.setToolTip('右パネルを折りたたむ'); self.right_toggle.clicked.connect(self.toggle_right_panel); outer.addWidget(self.right_toggle,0)
        self._build_right_tabs(outer)
        self._build_bottom_view(root_v)
        self._update_nav(); self.update_perspective_labels(); self.update_perspective_panel_state()

    def _make_toggle_button(self,text,checked=False,tooltip=''):
        b=QPushButton(text); b.setCheckable(True); b.setChecked(checked)
        if tooltip: b.setToolTip(tooltip)
        return b

    def _build_right_tabs(self,outer):
        panel=QWidget(); panel.setObjectName('rightPanel'); panel.setMinimumWidth(360); panel.setMaximumWidth(410); self.right_panel=panel
        r=QVBoxLayout(panel); r.setContentsMargins(8,8,8,8); r.setSpacing(6)
        self.right_tabs=QTabWidget(); self.right_tabs.setObjectName('rightTabs'); r.addWidget(self.right_tabs)
        self._build_perspective_tab(); self._build_composition_tab()
        outer.addWidget(panel,0)

    def _build_perspective_tab(self):
        tab=QWidget(); outer=QVBoxLayout(tab); outer.setContentsMargins(0,0,0,0)
        body=QWidget(); body.setMinimumWidth(0); body.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
        lay=QVBoxLayout(body); lay.setContentsMargins(7,6,7,6); lay.setSpacing(4)
        self.show_perspective=QCheckBox(); self.show_perspective.setChecked(True); self.show_perspective.hide()
        self.show_perspective_handles=QCheckBox(); self.show_perspective_handles.setChecked(True); self.show_perspective_handles.hide()
        self.perspective_pencil=QCheckBox(); self.perspective_pencil.setChecked(True); self.perspective_pencil.hide()
        self.show_perspective_grid=QCheckBox(); self.show_perspective_grid.setChecked(True); self.show_perspective_grid.hide()
        axisrow=QHBoxLayout(); axisrow.setSpacing(5); self.axis_buttons={}
        tips={'vp1':'X軸（水平・左右方向）。2本の基準線からVP1を決めます。確定後はVP1自体をドラッグできます。','vp2':'Z軸（奥行き方向）。2本の基準線からVP2を決めます。X＋Zがレンズ推定に使われます。','vp3':'Y軸（垂直・上下方向）。作画用VP3。レンズ推定には影響しません。'}
        for key,label in [('vp1','X 水平'),('vp2','Z 奥行'),('vp3','Y 垂直')]:
            b=QPushButton(label); b.setCheckable(True); b.setMinimumWidth(0); b.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed); b.setToolTip(tips[key]); b.clicked.connect(lambda checked,k=key:self.set_perspective_axis(k)); axisrow.addWidget(b,1); self.axis_buttons[key]=b
        lay.addLayout(axisrow)
        self.persp_step_label=QLabel('X 1本目：画像上をドラッグ'); self.persp_step_label.setObjectName('fileLabel'); self.persp_step_label.setWordWrap(True); lay.addWidget(self.persp_step_label)
        self.persp_status_label=QLabel('X —   Z —   Y —'); self.persp_status_label.setObjectName('note'); self.persp_status_label.setWordWrap(True); lay.addWidget(self.persp_status_label)
        row=QHBoxLayout(); row.setSpacing(5); resetaxis=QPushButton('選択軸リセット'); resetaxis.clicked.connect(self.reset_active_perspective_axis); resetall=QPushButton('全リセット'); resetall.clicked.connect(self.reset_perspective); row.addWidget(resetaxis,1); row.addWidget(resetall,1); lay.addLayout(row)
        self.axis_refine_labels={}; self.axis_refine_buttons={}; self.axis_refine_clear_buttons={}
        refine_row=QHBoxLayout(); refine_row.setSpacing(4)
        for key,label,desc in [('vp1','X','水平'),('vp2','Z','奥行'),('vp3','Y','垂直')]:
            box=QHBoxLayout(); box.setSpacing(2)
            lab=QLabel(f'{label}補0'); lab.setObjectName('note'); lab.setMinimumWidth(0); lab.setMaximumWidth(36); lab.setToolTip(f'{label}軸（{desc}）の補助線本数。軸確定後は画像上をそのままドラッグして補助線を追加できます。'); box.addWidget(lab); self.axis_refine_labels[key]=lab
            add=QPushButton('+'); add.setFixedWidth(24); add.setToolTip(f'{label}軸（{desc}）の補助線入力を開始。軸確定後はボタンを押さず直接描いても追加されます。最大6本。'); add.clicked.connect(lambda checked=False,k=key:self.arm_axis_refinement_line(k)); box.addWidget(add); self.axis_refine_buttons[key]=add
            clear=QPushButton('×'); clear.setFixedWidth(24); clear.setToolTip(f'{label}補助線を全削除'); clear.clicked.connect(lambda checked=False,k=key:self.clear_axis_refinement_lines(k)); box.addWidget(clear); self.axis_refine_clear_buttons[key]=clear
            refine_row.addLayout(box,1)
        lay.addLayout(refine_row)
        self.section(lay,'グリッド / 放射線')
        self.vp_ray_checks={}; self.vp_ray_count_labels={}; self.vp_ray_color_buttons={}
        for key,label in [('vp1','X'),('vp2','Z'),('vp3','Y')]:
            row=QHBoxLayout(); row.setSpacing(4)
            chk=QCheckBox(); chk.setChecked(self.vp_ray_visible[key]); chk.setFixedWidth(22); chk.toggled.connect(lambda v,k=key:self.set_vp_ray_visible(k,v)); row.addWidget(chk); self.vp_ray_checks[key]=chk
            axis_lab=QLabel(label); axis_lab.setAlignment(Qt.AlignmentFlag.AlignCenter); axis_lab.setFixedWidth(18); axis_lab.setStyleSheet('padding:0; min-height:0;'); row.addWidget(axis_lab)
            minus=QPushButton('−'); minus.setFixedWidth(31); minus.clicked.connect(lambda checked=False,k=key:self.change_vp_ray_count(k,-1)); row.addWidget(minus)
            val=QLabel(str(self.vp_ray_counts[key])); val.setAlignment(Qt.AlignmentFlag.AlignCenter); val.setFixedWidth(28); val.setStyleSheet('padding:0; min-height:0;'); row.addWidget(val); self.vp_ray_count_labels[key]=val
            plus=QPushButton('+'); plus.setFixedWidth(31); plus.clicked.connect(lambda checked=False,k=key:self.change_vp_ray_count(k,1)); row.addWidget(plus)
            col=QPushButton('色'); col.setFixedWidth(38); col.clicked.connect(lambda checked=False,k=key:self.choose_vp_ray_color(k)); row.addWidget(col); self.vp_ray_color_buttons[key]=col
            row.addStretch(1); lay.addLayout(row)
        self._update_vp_color_buttons()
        row=QHBoxLayout(); row.addWidget(QLabel('線幅')); self.perspective_line_width=StepControl(0.5,5.0,0.5,0.5); self.perspective_line_width.value.valueChanged.connect(self.refresh); row.addWidget(self.perspective_line_width); lay.addLayout(row)
        row=QHBoxLayout(); row.addWidget(QLabel('透明度')); self.perspective_alpha=QSlider(Qt.Orientation.Horizontal); self.perspective_alpha.setRange(0,100); self.perspective_alpha.setValue(70); self.perspective_alpha.valueChanged.connect(self.refresh); row.addWidget(self.perspective_alpha,1); self.perspective_alpha_label=QLabel('70%'); self.perspective_alpha_label.setFixedWidth(38); self.perspective_alpha.valueChanged.connect(lambda v:self.perspective_alpha_label.setText(f'{v}%')); row.addWidget(self.perspective_alpha_label); lay.addLayout(row)
        self.section(lay,'カメラ / レンズ')
        self.persp_lens=QLabel('X＋Zを確定するとレンズ推定を開始します。'); self.persp_lens.setObjectName('fileLabel'); self.persp_lens.setWordWrap(True); self.persp_lens.setMinimumWidth(0); lay.addWidget(self.persp_lens)
        self.camera_solve_label=QLabel('主点：画像中央 / Yは作画用として独立'); self.camera_solve_label.setObjectName('note'); self.camera_solve_label.setWordWrap(True); self.camera_solve_label.hide()
        self.shot_analysis=QLabel('ショット分析：X＋Z確定後に表示します。'); self.shot_analysis.setObjectName('fileLabel'); self.shot_analysis.setWordWrap(True); self.shot_analysis.setMinimumWidth(0); lay.addWidget(self.shot_analysis)
        row=QHBoxLayout(); solve_btn=QPushButton('再計算'); solve_btn.clicked.connect(self.solve_camera_calibration); row.addWidget(solve_btn); self.persp_detail_toggle=QPushButton('詳細 ▼'); self.persp_detail_toggle.setCheckable(True); self.persp_detail_toggle.toggled.connect(self.toggle_perspective_details); row.addWidget(self.persp_detail_toggle); lay.addLayout(row)
        self.persp_detail_widget=QWidget(); detail_lay=QVBoxLayout(self.persp_detail_widget); detail_lay.setContentsMargins(0,0,0,0); detail_lay.setSpacing(4); self.persp_label=QLabel('未解決'); self.persp_label.setObjectName('note'); self.persp_label.setWordWrap(True); detail_lay.addWidget(self.persp_label); self.persp_lens_detail=QLabel('レンズはX/Zの現在VPのみを使用。Y/VP3はレンズ値を変更しません。'); self.persp_lens_detail.setObjectName('note'); self.persp_lens_detail.setWordWrap(True); detail_lay.addWidget(self.persp_lens_detail); self.persp_detail_widget.setVisible(False); lay.addWidget(self.persp_detail_widget)
        self.section(lay,'表示')
        row=QHBoxLayout(); row.setSpacing(4); row.addWidget(QLabel('領域')); self.workspace_scale=QSlider(Qt.Orientation.Horizontal); self.workspace_scale.setRange(100,400); self.workspace_scale.setValue(100); self.workspace_scale.valueChanged.connect(self.refresh); row.addWidget(self.workspace_scale,1); self.workspace_label=QLabel('100%'); self.workspace_label.setFixedWidth(40); self.workspace_scale.valueChanged.connect(lambda v:self.workspace_label.setText(f'{v}%')); row.addWidget(self.workspace_label); row.addWidget(QLabel('Zoom')); self.zoom_label=QLabel('100%'); self.zoom_label.setFixedWidth(42); row.addWidget(self.zoom_label); zreset=QPushButton('100%'); zreset.setFixedWidth(48); zreset.clicked.connect(self.reset_zoom); row.addWidget(zreset); lay.addLayout(row)
        lay.addStretch(1); outer.addWidget(body); self.right_tabs.addTab(tab,'パース・レンズ')

    def toggle_perspective_details(self,on):
        if hasattr(self,'persp_detail_widget'): self.persp_detail_widget.setVisible(bool(on))
        if hasattr(self,'persp_detail_toggle'): self.persp_detail_toggle.setText('詳細 ▲' if on else '詳細 ▼')

    def _build_composition_tab(self):
        tab=QWidget(); outer=QVBoxLayout(tab); outer.setContentsMargins(0,0,0,0)
        body=QWidget(); c=QVBoxLayout(body); c.setContentsMargins(7,6,7,6); c.setSpacing(4)
        self.section(c,'基本ガイド')
        basic=QGridLayout(); basic.setSpacing(6)
        specs=[('show_thirds','三分割',True),('show_cross','十字',False),('show_golden','黄金比',False),('show_spiral','黄金螺旋',False),('show_diagonal','対角線',False),('show_triangle','三角構図',False),('show_symmetry','対称軸',False)]
        for i,(attr,label,checked) in enumerate(specs):
            b=self._make_toggle_button(label,checked); b.toggled.connect(self.refresh); setattr(self,attr,b); basic.addWidget(b,i//3,i%3)
        c.addLayout(basic)
        note=QLabel('基本ガイドの直線同士の交点には、塗りつぶし○を表示できます。'); note.setObjectName('note'); note.setWordWrap(True); c.addWidget(note)

        self.section(c,'追加の構図')
        add=QGridLayout(); add.setSpacing(6)
        specs=[('show_radiating','放射構図'),('show_tunnel','トンネル'),('show_golden_triangle','ゴールデントライアングル'),('show_circle','円構図'),('show_cshape','C字構図'),('show_vshape','V字構図'),('show_double_diagonal','ダブル対角線'),('show_scurve','S字構図'),('show_lshape','L字構図'),('show_pyramid','ピラミッド構図')]
        for i,(attr,label) in enumerate(specs):
            b=self._make_toggle_button(label,False); b.toggled.connect(self.comp_guide_visibility_changed); setattr(self,attr,b); add.addWidget(b,i//3,i%3)
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
        c.addStretch(1); outer.addWidget(body); self.right_tabs.addTab(tab,'構図ガイド')

    def _build_bottom_view(self, root_v):
        self.bottom_view=QWidget(); self.bottom_view.setObjectName('bottomView'); self.bottom_view.setFixedHeight(112)
        bl=QVBoxLayout(self.bottom_view); bl.setContentsMargins(6,4,6,4); bl.setSpacing(3)
        head=QHBoxLayout(); title=QLabel('ショット一覧'); title.setObjectName('section'); head.addWidget(title); self.bottom_count=QLabel('0枚'); self.bottom_count.setObjectName('note'); head.addWidget(self.bottom_count); head.addStretch(1)
        close=QPushButton('下ビューを隠す'); close.setFixedWidth(92); close.clicked.connect(lambda:self.bottom_view.setVisible(False)); head.addWidget(close); bl.addLayout(head)
        self.thumb_scroll=QScrollArea(); self.thumb_scroll.setWidgetResizable(True); self.thumb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); self.thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded); self.thumb_scroll.setFixedHeight(78)
        self.thumb_body=QWidget(); self.thumb_layout=QHBoxLayout(self.thumb_body); self.thumb_layout.setContentsMargins(2,2,2,2); self.thumb_layout.setSpacing(6); self.thumb_layout.addStretch(1); self.thumb_scroll.setWidget(self.thumb_body); bl.addWidget(self.thumb_scroll)
        root_v.addWidget(self.bottom_view,0)

    def _clear_bottom_view(self):
        if not hasattr(self,'thumb_layout'): return
        self._thumb_build_token += 1
        self.thumb_buttons=[]
        self._thumb_icon_cache={}
        while self.thumb_layout.count():
            item=self.thumb_layout.takeAt(0); w=item.widget()
            if w is not None: w.deleteLater()

    def _refresh_bottom_view(self):
        """Create a lightweight clickable filmstrip without decoding every image.

        All shot buttons are created immediately as cheap placeholders. Only thumbnails
        that are currently visible (plus a small margin) are decoded and cached. This
        keeps folders with hundreds of shots responsive.
        """
        if not hasattr(self,'thumb_layout'): return
        self._clear_bottom_view()
        if hasattr(self,'bottom_count'): self.bottom_count.setText(f'{len(self.paths)}枚')
        token=self._thumb_build_token

        # Creating buttons is cheap; image decoding is deferred until the button is visible.
        for i,p in enumerate(self.paths):
            b=QPushButton(str(i+1))
            b.setCheckable(True); b.setChecked(i==self.current_index)
            b.setFixedSize(104,64); b.setToolTip(f'{i+1}: {p.name}')
            b.setProperty('shotIndex', i)
            # Capture the index directly.  Using sender()/dynamic properties proved unreliable
            # in some Windows/PySide6 packaged builds when buttons were updated lazily.
            b.clicked.connect(lambda checked=False, idx=i: self._jump_to_image(idx))
            self.thumb_layout.addWidget(b)
            self.thumb_buttons.append(b)
        self.thumb_layout.addStretch(1)

        # Populate only the visible thumbnails. Scrolling schedules another small batch.
        bar=self.thumb_scroll.horizontalScrollBar()
        try:
            bar.valueChanged.disconnect(self._schedule_visible_thumbnails)
        except Exception:
            pass
        bar.valueChanged.connect(self._schedule_visible_thumbnails)
        QTimer.singleShot(0, self._update_bottom_selection)
        QTimer.singleShot(0, self._schedule_visible_thumbnails)

    def _schedule_visible_thumbnails(self, *args):
        if not hasattr(self,'thumb_scroll') or not self.thumb_buttons: return
        # Coalesce rapid scroll events so dragging the scrollbar never decodes hundreds.
        token=self._thumb_build_token
        QTimer.singleShot(35, lambda t=token: self._populate_visible_thumbnails(t))

    def _populate_visible_thumbnails(self, token=None):
        if token is not None and token != self._thumb_build_token: return
        if not self.thumb_buttons or not self.paths: return
        try:
            bar=self.thumb_scroll.horizontalScrollBar()
            x=max(0,bar.value())
            vw=max(1,self.thumb_scroll.viewport().width())
        except Exception:
            x=0; vw=900
        cell=110  # 104px button + 6px spacing
        first=max(0, x//cell - 2)
        last=min(len(self.thumb_buttons), (x+vw)//cell + 4)
        # Decode at most the currently visible neighborhood. Icons remain cached.
        for i in range(first,last):
            if i in self._thumb_icon_cache:
                continue
            p=self.paths[i]
            pm=None
            try:
                with Image.open(p) as im:
                    try:
                        im.draft('RGB',(192,112))
                    except Exception:
                        pass
                    thumb=im.convert('RGB')
                    thumb.thumbnail((96,56), Image.Resampling.BILINEAR)
                    pm=pil_to_pixmap(thumb)
            except Exception:
                pm=None
            self._thumb_icon_cache[i]=pm
            if i < len(self.thumb_buttons):
                b=self.thumb_buttons[i]
                if pm is not None:
                    b.setText(''); b.setIcon(pm); b.setIconSize(pm.size())
                else:
                    b.setText(str(i+1))

    def _update_bottom_selection(self):
        if not hasattr(self,'thumb_buttons'): return
        current_button=None
        for b in self.thumb_buttons:
            idx=b.property('shotIndex')
            is_current=(idx == self.current_index)
            b.blockSignals(True); b.setChecked(is_current); b.blockSignals(False)
            if is_current: current_button=b
        # Keep selected shot visible, then decode only its new visible neighborhood.
        if current_button is not None and hasattr(self,'thumb_scroll'):
            QTimer.singleShot(0, lambda w=current_button: self.thumb_scroll.ensureWidgetVisible(w, 24, 0))
            QTimer.singleShot(15, self._schedule_visible_thumbnails)

    def _on_thumbnail_clicked(self, checked=False, index=None):
        if index is not None:
            self._jump_to_image(int(index)); return
        b=self.sender()
        if b is None: return
        idx=b.property('shotIndex')
        try:
            idx=int(idx)
        except (TypeError, ValueError):
            return
        self._jump_to_image(idx)

    def _jump_to_image(self,index):
        if not (0 <= index < len(self.paths)):
            return
        if index == self.current_index:
            self._update_bottom_selection()
            return
        # Preserve only the current shot, then load only the clicked shot.
        # No other source image or thumbnail is reopened here.
        self._save_current_frame()
        self.save_current_perspective()
        self.current_index=index
        self.load_current()
        self._update_bottom_selection()

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

    def _perspective_history_state(self):
        return {
            'vp1':tuple(self.vp1),'vp2':tuple(self.vp2),'vp3':tuple(self.vp3),
            'eye':float(self.eye_level_y),
            'lines':copy.deepcopy(self.perspective_lines),
            'complete':dict(self._persp_axis_complete),
            'touched':copy.deepcopy(self._persp_anchor_touched),
            'active':self.active_perspective_axis,
            'step':int(self.perspective_step),
            'infinity':dict(getattr(self,'vp_at_infinity',{'vp1':False,'vp2':False,'vp3':False})),
            'vp3_at_infinity':bool(getattr(self,'vp3_at_infinity',False)),
            'extra_lines':copy.deepcopy(getattr(self,'perspective_extra_lines',{'vp1':[],'vp2':[],'vp3':[]})),
            'line1_locked':copy.deepcopy(getattr(self,'_persp_line1_locked',{'vp1':None,'vp2':None,'vp3':None})),
        }

    def push_perspective_undo(self):
        if self._restoring_perspective_history:return
        self._perspective_undo.append(self._perspective_history_state())
        if len(self._perspective_undo)>80:self._perspective_undo.pop(0)
        self._perspective_redo.clear()

    def _restore_perspective_history_state(self,st):
        self._restoring_perspective_history=True
        try:
            self.vp1=tuple(st['vp1']); self.vp2=tuple(st['vp2']); self.vp3=tuple(st['vp3'])
            self.eye_level_y=float(st['eye']); self.perspective_lines=copy.deepcopy(st['lines']); self._persp_axis_complete=dict(st['complete']); self._persp_anchor_touched=copy.deepcopy(st['touched']); self.active_perspective_axis=st.get('active','vp1'); self.perspective_step=int(st.get('step',0)); self.vp_at_infinity=dict(st.get('infinity',{'vp1':False,'vp2':False,'vp3':False})); self.vp3_at_infinity=bool(st.get('vp3_at_infinity',False)); self.perspective_extra_lines=copy.deepcopy(st.get('extra_lines',{'vp1':[],'vp2':[],'vp3':[]})); [self.perspective_extra_lines.setdefault(k,[]) for k in ('vp1','vp2','vp3')]; self._persp_line1_locked=copy.deepcopy(st.get('line1_locked',{'vp1':None,'vp2':None,'vp3':None}))
            self.axis_refine_mode=None; self.update_axis_refine_labels(); self.camera_solution=None; self.camera_inf_dir={'vp1':None,'vp2':None,'vp3':None}; self.camera_solve_error=None
            self.recompute_lens_from_current_vps(); self.update_perspective_labels(); self.update_perspective_panel_state(); self.save_perspective(); self.refresh()
        finally:
            self._restoring_perspective_history=False

    def undo_perspective(self):
        if not self._perspective_undo:
            self.statusBar().showMessage('パース：これ以上戻せません',1400); return
        self._perspective_redo.append(self._perspective_history_state())
        self._restore_perspective_history_state(self._perspective_undo.pop())
        self.statusBar().showMessage('パースを1操作戻しました',1200)

    def redo_perspective(self):
        if not self._perspective_redo:
            self.statusBar().showMessage('パース：やり直せる操作がありません',1400); return
        self._perspective_undo.append(self._perspective_history_state())
        self._restore_perspective_history_state(self._perspective_redo.pop())
        self.statusBar().showMessage('パースを1操作やり直しました',1200)

    def update_axis_refine_labels(self):
        if not hasattr(self,'axis_refine_labels'): return
        names={'vp1':'X','vp2':'Z','vp3':'Y'}
        for key,lab in self.axis_refine_labels.items():
            n=len(getattr(self,'perspective_extra_lines',{}).get(key,[]))
            waiting = getattr(self,'axis_refine_mode',None)==key
            lab.setText(f'{names[key]}補{n}' + ('●' if waiting else ''))

    def update_z_refine_label(self):
        # Backward-compatible alias used by older call sites.
        self.update_axis_refine_labels()

    def arm_axis_refinement_line(self,key):
        arr=self.perspective_extra_lines.setdefault(key,[])
        names={'vp1':'X','vp2':'Z','vp3':'Y'}
        if len(arr)>=6:
            self.statusBar().showMessage(f'{names[key]}補助線は最大6本です',2200); return
        if not self._persp_axis_complete.get(key,False):
            self.statusBar().showMessage(f'先に{names[key]}軸の基本2本を確定してください',2500); return
        self.active_perspective_axis=key
        self.axis_refine_mode=key
        self.update_perspective_panel_state(); self.update_axis_refine_labels()
        self.statusBar().showMessage(f'{names[key]}補助線：同じ方向のエッジを1本ドラッグしてください',3000)

    def arm_z_refinement_line(self): self.arm_axis_refinement_line('vp2')

    def clear_axis_refinement_lines(self,key):
        if not self.perspective_extra_lines.get(key): return
        self.push_perspective_undo(); self.perspective_extra_lines[key]=[]; self.axis_refine_mode=None
        self.recompute_after_axis_refinement(key); self.update_axis_refine_labels(); self.save_perspective(); self.refresh()

    def clear_z_refinement_lines(self): self.clear_axis_refinement_lines('vp2')

    def recompute_after_axis_refinement(self,key):
        if self._persp_axis_complete.get(key):
            q=self._axis_observation_h(key)
            if q is not None and abs(float(q[2]))>=1e-9 and self.original is not None:
                w=float(self.original.width); h=float(self.original.height)
                xy=(float(q[0]/q[2])/w,float(q[1]/q[2])/h)
                if key=='vp1': self.vp1=xy
                elif key=='vp2': self.vp2=xy
                else: self.vp3=xy; self.vp3_at_infinity=False
                self.vp_at_infinity[key]=False
        # Lens remains strictly X+Z. Y extras only stabilize the drawing grid.
        self.recompute_lens_from_current_vps(); self.update_perspective_labels(); self.update_perspective_panel_state()

    def recompute_after_z_refinement(self): self.recompute_after_axis_refinement('vp2')

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
        """Arm line 2 without ever mutating the confirmed first stroke."""
        lines=[[tuple(pt) for pt in line] for line in self.perspective_lines[name]]
        locked=self._persp_line1_locked.get(name)
        if locked is not None:
            lines[0]=[tuple(locked[0]),tuple(locked[1])]
            self._persp_anchor_touched[(name,0)]={0,1}
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
            self._persp_line1_locked[nxt]=None
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
        lab={'vp1':'X','vp2':'Z','vp3':'Y'}.get(self.active_perspective_axis,self.active_perspective_axis.upper())
        if hasattr(self,'persp_step_label'):
            if self._persp_axis_complete.get(self.active_perspective_axis,False):
                self.persp_step_label.setText(f'{lab} 完了：4つの白○またはVP●をドラッグして微調整')
            elif self.perspective_step==0:
                self.persp_step_label.setText(f'{lab} 1本目：短いエッジでもOK・ドラッグして基準線')
            else:
                self.persp_step_label.setText(f'{lab} 2本目：1本目の白○もそのまま調整できます')
    def reset_active_perspective_axis(self):
        defaults=self.default_perspective_lines(); name=self.active_perspective_axis
        import copy; self.perspective_lines[name]=copy.deepcopy(defaults[name]); self._persp_axis_complete[name]=False; self.vp_at_infinity[name]=False; self.vp3_at_infinity=self.vp_at_infinity['vp3']; self._persp_anchor_touched[(name,0)]=set(); self._persp_anchor_touched.pop((name,1),None); self._persp_line1_locked[name]=None
        self.perspective_extra_lines[name]=[]; self.axis_refine_mode=None; self.update_axis_refine_labels()
        self.perspective_step=0; self.recompute_lens_from_current_vps(); self.update_perspective_labels(); self.update_perspective_panel_state(); self.save_perspective(); self.refresh()

    def _style(self):
        self.setStyleSheet('''QMainWindow,QWidget{background:#20242b;color:#e8edf3}#controlsWidget{background:#20242b}#controlScroll{border:1px solid #343a43;background:#20242b}#appTitle{font-size:20px;font-weight:700}#panelTitle{font-size:18px;font-weight:700}#rightPanel{background:#1b1f26;border:1px solid #343a43}QTabWidget::pane{border:1px solid #343a43;background:#1b1f26}QTabBar::tab{background:#272d36;border:1px solid #3e4652;padding:6px 10px;margin-right:2px}QTabBar::tab:selected{background:#2d6cdf;color:white}QPushButton:checked{background:#2d6cdf;border-color:#68a0ff;color:white}#subtitle,#note{color:#aeb7c4}#section{font-size:14px;font-weight:700;color:#d9e2ec;margin-top:8px;border-top:1px solid #3b424d;padding-top:8px}#fileLabel{background:#171a20;border:1px solid #343a43;border-radius:5px;padding:5px}QPushButton{background:#303641;border:1px solid #48505d;border-radius:5px;padding:5px}QPushButton:hover{background:#3a424f}#panelToggle{padding:2px;font-size:17px;font-weight:700;background:#252b34;border-radius:3px}QPushButton:disabled{color:#69717c;background:#272b32}QLabel{min-height:18px;padding-top:1px;padding-bottom:1px}QCheckBox{min-height:20px;padding:1px}QDoubleSpinBox{background:#171a20;border:1px solid #48505d;padding:4px}QSlider::groove:horizontal{height:4px;background:#3b424d}QSlider::handle:horizontal{width:14px;margin:-5px 0;background:#8ab4f8;border-radius:7px}QScrollBar:vertical{background:#20242b;width:12px}QScrollBar::handle:vertical{background:#4a5260;min-height:28px;border-radius:5px}QStatusBar{background:#171a20;color:#aeb7c4}''')
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
        et=event.type()
        focus=QApplication.focusWidget()
        editing=isinstance(focus,(QDoubleSpinBox,QLineEdit,QSlider))

        if et==QEvent.Type.KeyPress:
            key=event.key(); mods=event.modifiers()
            ctrl_or_cmd=bool(mods & (Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.MetaModifier))

            if key==Qt.Key.Key_Z and ctrl_or_cmd and not editing:
                if mods & Qt.KeyboardModifier.ShiftModifier:
                    self.redo_perspective()
                else:
                    self.undo_perspective()
                return True

            if key==Qt.Key.Key_Space and not editing:
                if not event.isAutoRepeat():
                    self.space_pan_active=True
                    if hasattr(self,'canvas') and self.canvas.drag_item is None:
                        self.canvas.setCursor(Qt.CursorShape.OpenHandCursor)
                return True

            if key==Qt.Key.Key_H and not editing and mods==Qt.KeyboardModifier.NoModifier:
                if not event.isAutoRepeat():
                    self.hand_tool_latched=not self.hand_tool_latched
                    if hasattr(self,'canvas'):
                        if self.hand_tool_latched:
                            self.canvas.setCursor(Qt.CursorShape.OpenHandCursor)
                        elif not self.space_pan_active:
                            self.canvas.unsetCursor()
                    self.statusBar().showMessage(
                        '手のひらツール ON（Hで解除）' if self.hand_tool_latched else '手のひらツール OFF',1800)
                return True

            if mods==Qt.KeyboardModifier.NoModifier and not editing:
                if key==Qt.Key.Key_Left:
                    self.prev_image(); return True
                if key==Qt.Key.Key_Right:
                    self.next_image(); return True
                if key==Qt.Key.Key_F:
                    self.toggle_focus_view(); return True

        elif et==QEvent.Type.KeyRelease:
            if event.key()==Qt.Key.Key_Space and not editing:
                if not event.isAutoRepeat():
                    self.space_pan_active=False
                    if hasattr(self,'canvas') and self.canvas.drag_item is None:
                        if self.hand_tool_latched:
                            self.canvas.setCursor(Qt.CursorShape.OpenHandCursor)
                        else:
                            self.canvas.unsetCursor()
                return True

        return super().eventFilter(obj,event)

    def keyPressEvent(self,e):
        key=e.key(); focus=QApplication.focusWidget()
        editing=isinstance(focus,(QDoubleSpinBox,QLineEdit,QSlider))
        mods=e.modifiers()
        ctrl_or_cmd=bool(mods & (Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.MetaModifier))
        if key==Qt.Key.Key_Z and ctrl_or_cmd and not editing:
            if mods & Qt.KeyboardModifier.ShiftModifier:self.redo_perspective()
            else:self.undo_perspective()
            e.accept(); return
        if key==Qt.Key.Key_H and not editing and mods==Qt.KeyboardModifier.NoModifier:
            self.hand_tool_latched=not self.hand_tool_latched
            if self.hand_tool_latched:self.canvas.setCursor(Qt.CursorShape.OpenHandCursor)
            elif not self.space_pan_active:self.canvas.unsetCursor()
            e.accept(); return
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
        self.paths=uniq; self.current_index=0; self.load_current(); self._refresh_bottom_view()
    def load_current(self):
        if not(0<=self.current_index<len(self.paths)):return
        p=self.paths[self.current_index]
        try:
            with Image.open(p) as src:self.original=src.convert('RGB').copy()
            self.view_pan=(0.0,0.0); self._perspective_undo.clear(); self._perspective_redo.clear()
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
            self.perspective_extra_lines=copy.deepcopy(pd.get('extra_lines',{'vp1':[],'vp2':[],'vp3':[]})); [self.perspective_extra_lines.setdefault(k,[]) for k in ('vp1','vp2','vp3')]
            self._persp_line1_locked=copy.deepcopy(pd.get('line1_locked',{'vp1':None,'vp2':None,'vp3':None}))
            # Backfill safety copies for older saved states.
            for k in ('vp1','vp2','vp3'):
                if self._persp_line1_locked.get(k) is None and self._persp_axis_complete.get(k,False):
                    self._persp_line1_locked[k]=copy.deepcopy(self.perspective_lines[k][0])
            self.axis_refine_mode=None
            self.active_perspective_axis='vp1'; self.perspective_step=1 if self._persp_axis_complete.get('vp1',False) else 0
            self.update_z_refine_label()
            self.recompute_lens_from_current_vps()
            self.update_perspective_panel_state(); self.update_perspective_labels()
            self.update_display(); self.file_label.setText(f'{p.name}\n{self.current_index+1} / {len(self.paths)}\n{self.original.width} × {self.original.height} px'); self.statusBar().showMessage(str(p))
        except Exception as ex:self.file_label.setText(f'読み込み失敗: {p.name}\n{ex}')
        self._update_nav(); self._update_bottom_selection()
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
    def _line_h_from_segment(self,seg):
        if self.original is None or np is None:
            return None
        w=float(self.original.width); h=float(self.original.height)
        (x1,y1),(x2,y2)=seg
        p1=np.array([x1*w,y1*h,1.0],dtype=float)
        p2=np.array([x2*w,y2*h,1.0],dtype=float)
        l=np.cross(p1,p2)
        n=math.hypot(float(l[0]),float(l[1]))
        if n<1e-9:return None
        return l/n

    def _robust_vp_from_segments(self,segments):
        """Homogeneous least-squares VP with light IRLS outlier suppression."""
        if np is None:return None
        L=[]
        for seg in segments:
            l=self._line_h_from_segment(seg)
            if l is not None:L.append(l)
        if len(L)<2:return None
        L=np.asarray(L,dtype=float)
        weights=np.ones(len(L),dtype=float)
        q=None
        for _ in range(4):
            A=(L*weights[:,None]).T @ L
            vals,vecs=np.linalg.eigh(A)
            q=vecs[:,int(np.argmin(vals))]
            n=float(np.linalg.norm(q))
            if n<1e-12:return None
            q=q/n
            r=np.abs(L@q)
            med=float(np.median(r))
            scale=max(1e-7,1.4826*med)
            cutoff=2.5*scale
            weights=np.where(r<=cutoff,1.0,cutoff/np.maximum(r,1e-12))
        return q

    def _axis_observation_h(self,name):
        """Return manual-axis VP as homogeneous pixel point.

        Every axis can additionally use up to six refinement strokes. X/Z extras
        stabilize lens estimation; Y extras stabilize only the drawing grid.
        """
        if self.original is None:
            return None
        lines=list(self.perspective_lines.get(name,[]))
        lines += list(getattr(self,'perspective_extra_lines',{}).get(name,[]))
        return self._robust_vp_from_segments(lines)

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
            lines=list(self.perspective_lines.get(key,[]))
            lines += list(getattr(self,'perspective_extra_lines',{}).get(key,[]))
            for seg in lines:
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

    def recompute_lens_from_current_vps(self):
        if self.original is None:return False
        if not (self._persp_axis_complete.get('vp1',False) and self._persp_axis_complete.get('vp2',False)):
            self.camera_solution=None; self.camera_solve_error=None
            if hasattr(self,'camera_solve_label'): self.camera_solve_label.setText('主点：画像中央 / X＋Zでレンズ解析 / Yは作画用')
            return False
        if self.vp_at_infinity.get('vp1',False) or self.vp_at_infinity.get('vp2',False):
            self.camera_solution=None; self.camera_solve_error=None
            if hasattr(self,'camera_solve_label'): self.camera_solve_label.setText('XまたはZが平行方向のためレンズ推定は保留です。')
            return False
        fpx=self._pair_focal_pixels(self.vp1,self.vp2)
        if fpx is None:
            self.camera_solution=None; self.camera_solve_error=None
            if hasattr(self,'camera_solve_label'): self.camera_solve_label.setText('X/Zの配置では中心主点モデルのレンズ解が成立しません。')
            return False
        w=float(self.original.width); h=float(self.original.height); x1,y1=self.vp1; x2,y2=self.vp2
        self.eye_level_y=(y1+(0.5-x1)*(y2-y1)/(x2-x1)) if abs(x2-x1)>1e-12 else (y1+y2)*0.5
        self.camera_solve_error=self._xz_orthogonality_error_deg(fpx)
        self.camera_solution={'cx':w*0.5,'cy':h*0.5,'fpx':fpx,'hfov':math.degrees(2.0*math.atan(w/(2.0*fpx))),'vfov':math.degrees(2.0*math.atan(h/(2.0*fpx))),'eq35':36.0*fpx/w,'error_deg':self.camera_solve_error}
        if hasattr(self,'camera_solve_label'): self.camera_solve_label.setText(f'主点：画像中央  |  {self.camera_solution["eq35"]:.1f}mm eq.  |  H-FOV {self.camera_solution["hfov"]:.1f}°')
        return True

    def _xz_orthogonality_error_deg(self,fpx):
        if self.original is None or fpx is None or np is None:return None
        w=float(self.original.width); h=float(self.original.height); cx=w*0.5; cy=h*0.5
        def d(v):
            a=np.array([(v[0]*w-cx)/fpx,(v[1]*h-cy)/fpx,1.0],dtype=float); n=float(np.linalg.norm(a)); return a/n if n>1e-12 else None
        dx=d(self.vp1); dz=d(self.vp2)
        if dx is None or dz is None:return None
        dot=max(-1.0,min(1.0,abs(float(np.dot(dx,dz))))); return abs(90.0-math.degrees(math.acos(dot)))

    def solve_camera_calibration(self):
        ok=self.recompute_lens_from_current_vps(); self.update_perspective_labels(); self.refresh(); return ok

    def solve_perspective_axis(self,name):
        q=self._axis_observation_h(name)
        if q is None or self.original is None:return False
        w=float(self.original.width); h=float(self.original.height); scale=max(1.0,math.hypot(float(q[0]),float(q[1]))); is_inf=abs(float(q[2])) < 1e-5*scale; self.vp_at_infinity[name]=bool(is_inf); self.camera_inf_dir[name]=None
        if is_inf:
            n=math.hypot(float(q[0]),float(q[1]));
            if n>1e-9:self.camera_inf_dir[name]=(float(q[0])/n,float(q[1])/n)
        else:
            px=float(q[0]/q[2]); py=float(q[1]/q[2]); v=(px/w,py/h)
            if name=='vp1': self.vp1=v
            elif name=='vp2': self.vp2=v
            else: self.vp3=v
        self.vp3_at_infinity=self.vp_at_infinity.get('vp3',False); self.recompute_lens_from_current_vps(); self.update_perspective_labels();
        if hasattr(self,'canvas'): self.canvas.update()
        return True

    def align_calibration_lines_to_vp(self,name):
        """Rotate the two visible calibration strokes so they pass through a dragged VP.

        Segment midpoints and lengths are preserved. This mirrors the feel of a
        VanishPoint-style editor: moving the VP updates the ruler geometry instead of
        leaving the old strokes pointing somewhere else.
        """
        if self.original is None or self.vp_at_infinity.get(name,False):return
        vp=self.vp1 if name=='vp1' else (self.vp2 if name=='vp2' else self.vp3)
        w=max(1.0,float(self.original.width)); h=max(1.0,float(self.original.height))
        out=[]
        for seg in self.perspective_lines.get(name,[])[:2]:
            (x1,y1),(x2,y2)=seg
            p1=(x1*w,y1*h); p2=(x2*w,y2*h); mx=(p1[0]+p2[0])*0.5; my=(p1[1]+p2[1])*0.5
            ln=math.hypot(p2[0]-p1[0],p2[1]-p1[1])
            dx=vp[0]*w-mx; dy=vp[1]*h-my; dn=math.hypot(dx,dy)
            if ln<1e-6 or dn<1e-6:
                out.append([tuple(seg[0]),tuple(seg[1])]); continue
            ux,uy=dx/dn,dy/dn; half=ln*0.5
            a=((mx-ux*half)/w,(my-uy*half)/h); b=((mx+ux*half)/w,(my+uy*half)/h)
            out.append([a,b])
        if len(out)==2:
            self.perspective_lines[name]=out
            self._persp_line1_locked[name]=copy.deepcopy(out[0])
            self._persp_anchor_touched[(name,0)]={0,1}; self._persp_anchor_touched[(name,1)]={0,1}

    def solve_all_perspective_axes(self):
        for n in ('vp1','vp2','vp3'): self.solve_perspective_axis(n)
    def reset_zoom(self):
        self.view_zoom=1.0; self.view_pan=(0.0,0.0); self.update_zoom_label(); self.refresh()
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
                'extra_lines':copy.deepcopy(getattr(self,'perspective_extra_lines',{'vp1':[],'vp2':[],'vp3':[]})),
                'line1_locked':copy.deepcopy(getattr(self,'_persp_line1_locked',{'vp1':None,'vp2':None,'vp3':None})),
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
        self._persp_line1_locked={'vp1':None,'vp2':None,'vp3':None}
        self.perspective_extra_lines={'vp1':[],'vp2':[],'vp3':[]}; self.axis_refine_mode=None
        self.update_z_refine_label()
        self.update_perspective_panel_state()
        self.update_perspective_labels()
        self.save_perspective()
        self.refresh()

    def update_perspective_labels(self):
        inf=getattr(self,'vp_at_infinity',{'vp1':False,'vp2':False,'vp3':False})
        def vtxt(key,xy,label):
            if not self._persp_axis_complete.get(key,False):return f'{label}: —'
            if inf.get(key,False):return f'{label}: ∞'
            return f'{label}: ({xy[0]:.3f}, {xy[1]:.3f})'
        txt=vtxt('vp1',self.vp1,'X / VP1')+'\n'+vtxt('vp2',self.vp2,'Z / VP2')+'\n'+vtxt('vp3',self.vp3,'Y / VP3')
        if hasattr(self,'persp_label'):self.persp_label.setText(txt)
        if hasattr(self,'analysis_perspective'):self.analysis_perspective.setText(txt)
        if hasattr(self,'persp_status_label'):
            marks=[]
            for k,l in [('vp1','X'),('vp2','Z'),('vp3','Y')]: marks.append(f'{l} '+('∞' if inf.get(k,False) and self._persp_axis_complete.get(k,False) else ('✓' if self._persp_axis_complete.get(k,False) else '—')))
            extra=''
            if getattr(self,'camera_solution',None):extra=f'   |   {self.camera_solution["eq35"]:.1f}mm / {self.camera_solution["hfov"]:.1f}°'
            self.persp_status_label.setText('   '.join(marks)+extra)
        self.update_lens_estimate()

    def _manual_axis_vp_normalized(self, name):
        if self.original is None or not self._persp_axis_complete.get(name,False):return None
        if self.vp_at_infinity.get(name,False):return None
        v=self.vp1 if name=='vp1' else (self.vp2 if name=='vp2' else self.vp3)
        if not (math.isfinite(v[0]) and math.isfinite(v[1])):return None
        return (float(v[0]),float(v[1]))

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

    def _axis_base_angle_deg(self, name):
        """Acute angle between the two manually drawn base lines.

        A very small angle means the VP is extremely far away and focal-length
        recovery becomes ill-conditioned even when a numerical solution exists.
        """
        if self.original is None:return None
        lines=self.perspective_lines.get(name,[])[:2]
        if len(lines)<2:return None
        vec=[]
        w=float(self.original.width); h=float(self.original.height)
        for seg in lines:
            dx=(seg[1][0]-seg[0][0])*w; dy=(seg[1][1]-seg[0][1])*h
            n=math.hypot(dx,dy)
            if n<1e-8:return None
            vec.append((dx/n,dy/n))
        dot=max(-1.0,min(1.0,abs(vec[0][0]*vec[1][0]+vec[0][1]*vec[1][1])))
        return math.degrees(math.acos(dot))

    def _rotate_segment(self, seg, angle_deg):
        """Rotate a normalized image segment around its midpoint in pixel space."""
        w=float(self.original.width); h=float(self.original.height)
        x1,y1=seg[0][0]*w,seg[0][1]*h; x2,y2=seg[1][0]*w,seg[1][1]*h
        mx=(x1+x2)*0.5; my=(y1+y2)*0.5
        a=math.radians(angle_deg); ca=math.cos(a); sa=math.sin(a)
        out=[]
        for x,y in ((x1,y1),(x2,y2)):
            dx=x-mx; dy=y-my
            rx=mx+dx*ca-dy*sa; ry=my+dx*sa+dy*ca
            out.append((rx/w,ry/h))
        return out

    def _vp_from_two_segments(self, seg1, seg2):
        q=self._robust_vp_from_segments([seg1,seg2])
        if q is None or self.original is None:return None
        scale=max(1.0,math.hypot(float(q[0]),float(q[1])))
        if abs(float(q[2])) < 1e-7*scale:return None
        w=float(self.original.width); h=float(self.original.height)
        return (float(q[0]/q[2])/w,float(q[1]/q[2])/h)

    def _axis_observation_angle_spread_deg(self,name):
        """Maximum acute orientation separation among all observations for one axis."""
        if self.original is None:return None
        lines=list(self.perspective_lines.get(name,[])) + list(getattr(self,'perspective_extra_lines',{}).get(name,[]))
        if len(lines)<2:return None
        w=float(self.original.width); h=float(self.original.height); dirs=[]
        for seg in lines:
            dx=(seg[1][0]-seg[0][0])*w; dy=(seg[1][1]-seg[0][1])*h; n=math.hypot(dx,dy)
            if n<1e-8:continue
            dirs.append((dx/n,dy/n))
        if len(dirs)<2:return None
        best=0.0
        for i in range(len(dirs)):
            for j in range(i+1,len(dirs)):
                dot=max(-1.0,min(1.0,abs(dirs[i][0]*dirs[j][0]+dirs[i][1]*dirs[j][1])))
                best=max(best,math.degrees(math.acos(dot)))
        return best

    def _lens_sensitivity_range(self, samples=180):
        """Lens uncertainty from all X/Z observations, including auxiliary strokes.

        Each run perturbs every observed edge slightly, then recomputes each VP using
        robust multi-line fitting.  With 3+ observations this measures repeatability
        substantially better than perturbing only the original two lines.
        """
        if self.original is None:return None
        if not (self._persp_axis_complete.get('vp1') and self._persp_axis_complete.get('vp2')):return None
        lx=list(self.perspective_lines.get('vp1',[])[:2]) + list(getattr(self,'perspective_extra_lines',{}).get('vp1',[]))
        lz=list(self.perspective_lines.get('vp2',[])[:2]) + list(getattr(self,'perspective_extra_lines',{}).get('vp2',[]))
        if len(lx)<2 or len(lz)<2:return None
        w=float(self.original.width); h=float(self.original.height); vals=[]; failed=0; rng=random.Random(137)
        sigma_deg=0.20
        def solve(lines):
            q=self._robust_vp_from_segments(lines)
            if q is None:return None
            scale=max(1.0,math.hypot(float(q[0]),float(q[1])))
            if abs(float(q[2])) < 1e-7*scale:return None
            return (float(q[0]/q[2])/w,float(q[1]/q[2])/h)
        for _ in range(max(64,int(samples))):
            px=[self._rotate_segment(seg,rng.gauss(0,sigma_deg)) for seg in lx]
            pz=[self._rotate_segment(seg,rng.gauss(0,sigma_deg)) for seg in lz]
            va=solve(px); vb=solve(pz)
            if va is None or vb is None:
                failed+=1; continue
            fpx=self._pair_focal_pixels(va,vb)
            if fpx is None or not math.isfinite(fpx):
                failed+=1; continue
            eq35=36.0*fpx/w
            if 4.0<=eq35<=400.0: vals.append(eq35)
            else: failed+=1
        vals=sorted(vals)
        def qv(frac):
            if not vals:return None
            pos=(len(vals)-1)*frac; lo=int(math.floor(pos)); hi=int(math.ceil(pos))
            if lo==hi:return vals[lo]
            t=pos-lo; return vals[lo]*(1-t)+vals[hi]*t
        base={'n':len(vals),'failed_ratio':failed/max(1,failed+len(vals)),
              'x_angle':self._axis_observation_angle_spread_deg('vp1'),
              'z_angle':self._axis_observation_angle_spread_deg('vp2'),
              'x_lines':len(lx),'z_lines':len(lz)}
        if len(vals)<12:
            base.update({'unstable':True})
            if len(vals)>=4: base.update({'p10':qv(0.10),'p50':qv(0.50),'p90':qv(0.90),'spread':qv(0.90)-qv(0.10)})
            return base
        base.update({'p10':qv(0.10),'p50':qv(0.50),'p90':qv(0.90),'spread':qv(0.90)-qv(0.10),'unstable':False})
        return base

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

    def _legacy_lens_estimate(self):
        q=self._lens_from_manual_xz(); return None if q is None else float(q['eq35'])

    def estimate_lens(self):
        """Lens estimate decoupled from Camera Solver.

        Primary = current X/Z VP markers.
        Principal point = image centre.
        Y/VP3 is independent and never changes focal length.
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
        legacy_eq=self._legacy_lens_estimate()
        camera_eq=None
        if getattr(self,'camera_solution',None):
            try:
                camera_eq=float(self.camera_solution.get('eq35'))
            except Exception:
                camera_eq=None

        sens=self._lens_sensitivity_range()
        instability_reason=None
        if sens is not None and not sens.get('unstable',False):
            lo=max(4.0,float(sens['p10'])); hi=float(sens['p90'])
            rel=(hi-lo)/max(eq35,1e-6); fail=float(sens.get('failed_ratio',0.0))
            minang=min(x for x in (sens.get('x_angle'),sens.get('z_angle')) if x is not None) if any(x is not None for x in (sens.get('x_angle'),sens.get('z_angle'))) else 99.0
            if minang < 0.45:
                lens_conf='低'; instability_reason='基準線がほぼ平行で、消失点が極端に遠いため推定が不安定'
            elif rel < 0.20 and fail < 0.08 and minang >= 1.2:
                lens_conf='高'
            elif rel < 0.45 and fail < 0.25 and minang >= 0.65:
                lens_conf='中'
            else:
                lens_conf='低'; instability_reason='基準線のわずかな角度差で焦点距離が大きく変動'
        elif sens is not None:
            lo=max(4.0,eq35*0.55); hi=eq35*1.65; lens_conf='低'
            instability_reason='微小な基準線変化でレンズ解が成立しない場合が多い'
        else:
            lo=max(4.0,eq35*0.70); hi=eq35*1.35; lens_conf='低'
            instability_reason='入力感度を十分に評価できない'

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

        reason='現在のX/Z消失点 + 主点=画像中央'
        if camera_eq is not None:
            reason+=f' / XZ solver {camera_eq:.1f}mm'
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
            'legacy_eq35':legacy_eq,
            'sensitivity':sens,
            'instability_reason':instability_reason,
        }

    def _shot_analysis_text(self, eq35=None, hfov=None, confidence=None, fallback_feel=None):
        """Artist-facing shot/lens interpretation.

        This intentionally does not pretend to infer CU/MS/LS from focal length alone.
        Shot size depends on subject framing, while perspective/ compression can be
        described from FOV/lens geometry.
        """
        if hfov is None and eq35 is not None and eq35 > 0:
            hfov=math.degrees(2.0*math.atan(36.0/(2.0*eq35)))
        if hfov is not None and math.isfinite(hfov):
            if hfov >= 75:
                persp='かなり強い'; compression='かなり弱い'; feel='超広角'
            elif hfov >= 58:
                persp='強い'; compression='弱い'; feel='広角'
            elif hfov >= 40:
                persp='標準'; compression='標準'; feel='標準'
            elif hfov >= 24:
                persp='弱い'; compression='強い'; feel='中望遠'
            else:
                persp='かなり弱い'; compression='かなり強い'; feel='望遠'
            conf=f' / 判定信頼度：{confidence}' if confidence else ''
            return f'レンズ感：{feel}{conf}　｜　パース：{persp}　｜　圧縮：{compression}'
        feel=fallback_feel or '判定困難'
        return f'レンズ感：{feel}（参考）　｜　パース/圧縮：保留'

    def update_lens_estimate(self):
        targets=[x for x in (getattr(self,'analysis_lens',None),getattr(self,'persp_lens',None)) if x is not None]
        detail_targets=[x for x in (getattr(self,'lens_detail',None),getattr(self,'persp_lens_detail',None)) if x is not None]
        if not targets:
            return
        if not (self._persp_axis_complete.get('vp1') and self._persp_axis_complete.get('vp2')):
            for t in targets: t.setText('VP1 と VP2 を確定すると推定を開始します。')
            for d in detail_targets: d.setText('X/Zの2消失点から推定。Y/VP3は作画用でレンズ計算には使用しません。')
            if hasattr(self,'shot_analysis'): self.shot_analysis.setText('ショット分析：X＋Z確定後に表示します。')
            return
        est=self.estimate_lens()
        if est is None:
            # Exact focal length can fail when one direction is effectively at infinity.
            # Still provide an explicitly qualitative fallback from the sensitivity
            # ensemble, so the artist gets useful lens-feel guidance without false mm precision.
            sens=self._lens_sensitivity_range(samples=240)
            feel='判定困難'
            feel_range=None
            if sens is not None and sens.get('p50') is not None:
                mid=float(sens['p50']); lo=float(sens.get('p10',mid)); hi=float(sens.get('p90',mid))
                def k(mm):
                    if mm < 20:return '超広角'
                    if mm < 35:return '広角'
                    if mm < 60:return '標準'
                    if mm < 100:return '中望遠'
                    return '望遠'
                kl,km,kh=k(lo),k(mid),k(hi)
                # Keep the artist-facing qualitative result useful even when the
                # numerical interval is huge: report the median class plus at most
                # one adjacent class rather than e.g. '広角〜中望遠'.
                order=['超広角','広角','標準','中望遠','望遠']; idx=order.index(km)
                if kl==kh: feel=km
                elif idx>0 and k(lo)==order[idx-1]: feel=f'{order[idx-1]}〜{km}寄り'
                elif idx<len(order)-1 and k(hi)==order[idx+1]: feel=f'{km}〜{order[idx+1]}寄り'
                else: feel=f'{km}寄り'
                feel_range=(lo,hi)
            ax=sens.get('x_angle') if sens else None; az=sens.get('z_angle') if sens else None
            near_inf=any(a is not None and a < 0.45 for a in (ax,az))
            reason='一方のVPがほぼ無限遠で数値推定が不安定' if near_inf else '現在のX/Zでは直交2VPの焦点距離解が安定しない'
            txt=f'焦点距離：推定不可\nレンズ感（参考）：{feel}\n{reason}'
            if feel_range is not None:
                txt+=f'\n参考感度 {feel_range[0]:.0f}–{feel_range[1]:.0f}mm相当（数値確定には使用しません）'
            for t in targets:t.setText(txt)
            for d in detail_targets:d.setText('X/Zの微小角度変化からレンズ感だけを参考表示。Y/VP3はレンズ判定に使用しません。')
            if hasattr(self,'shot_analysis'): self.shot_analysis.setText(self._shot_analysis_text(fallback_feel=feel))
            return
        cand=' / '.join(f'{x}mm' for x in est['candidates'])
        sens=est.get('sensitivity') or {}
        lineinfo=''
        if sens.get('x_lines',2)>2 or sens.get('z_lines',2)>2:
            lineinfo=f"   X{sens.get('x_lines',2)}本/Z{sens.get('z_lines',2)}本"
        lens_text=(
            f"{est['eq35']:.1f}mm eq.   H-FOV {est['hfov']:.1f}°\n"
            f"感度 {est['lo']:.0f}–{est['hi']:.0f}mm   {est['kind']}   信頼度：{est['confidence']}{lineinfo}"
        )
        if est.get('instability_reason'):
            lens_text += f"\n⚠ {est['instability_reason']}"
        for t in targets: t.setText(lens_text)
        if hasattr(self,'shot_analysis'): self.shot_analysis.setText(self._shot_analysis_text(est['eq35'],est['hfov'],est['confidence']))
        details=[]
        for label,val in est['pairs']:
            if val is not None:
                details.append(f"{label}: {36.0*val/max(float(self.original.width),1.0):.1f}mm相当")
        if est.get('legacy_eq35') is not None:
            details.append(f"XZ再計算: {est['legacy_eq35']:.1f}mm相当")
        if est.get('camera_eq35') is not None:
            details.append(f"現在VP解: {est['camera_eq35']:.1f}mm相当")
        sens=est.get('sensitivity')
        if sens is not None and not sens.get('unstable',False):
            details.append(f"角度感度: {sens['p10']:.1f}–{sens['p90']:.1f}mm（10–90%） / 解失敗 {sens.get('failed_ratio',0)*100:.0f}%")
            if sens.get('x_angle') is not None and sens.get('z_angle') is not None:
                details.append(f"基準線交差角: X {sens['x_angle']:.2f}° / Z {sens['z_angle']:.2f}°")
        details.append(f"判定理由: {est['reason']}")
        detail_text=' / '.join(details)
        detail_text += '\n※ Y/VP3を動かしてもレンズ値は変化しません。'
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
