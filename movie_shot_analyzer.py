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
    QLabel, QMainWindow, QPushButton, QScrollArea, QSlider, QDoubleSpinBox, QSpinBox, QLineEdit,
    QVBoxLayout, QWidget, QTabWidget, QMessageBox
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
    def _draw_lens_export_overlay(self,p):
        """Small single-line lens metadata at top-center. Export only."""
        if not getattr(self.owner,'export_render_mode',False):
            return
        if not hasattr(self.owner,'export_overlay_lens') or not self.owner.export_overlay_lens.isChecked():
            return
        if not (self.owner._persp_axis_complete.get('vp1',False) and self.owner._persp_axis_complete.get('vp2',False)):
            return
        try:
            est=self.owner.estimate_lens()
        except Exception:
            est=None
        if not est:
            return
        eq=est.get('eq35')
        hfov=est.get('hfov')
        conf=est.get('confidence','')
        rng=(est.get('lo'),est.get('hi')) if est.get('lo') is not None and est.get('hi') is not None else None
        parts=[]
        if eq is not None: parts.append(f'Lens {eq:.0f}mm eq.')
        if hfov is not None: parts.append(f'FOV {hfov:.1f}°')
        if rng and isinstance(rng,(list,tuple)) and len(rng)>=2:
            parts.append(f'Range {rng[0]:.0f}–{rng[1]:.0f}mm')
        if est.get('solve_error') is not None: parts.append(f"Solve err {est['solve_error']:.2f}°")
        if conf: parts.append(f'Confidence {conf}')
        text='  |  '.join(parts)
        if not text:return

        fs=int(self.owner.export_lens_font_size.value()) if hasattr(self.owner,'export_lens_font_size') else 11
        alpha=int(255*(self.owner.export_lens_alpha.value()/100.0)) if hasattr(self.owner,'export_lens_alpha') else 200
        font=p.font(); font.setPixelSize(fs); font.setBold(False); p.setFont(font)
        fm=p.fontMetrics(); tw=fm.horizontalAdvance(text); th=fm.height()
        cx=self.image_rect.center().x(); top=self.image_rect.top()+8
        box=QRectF(cx-tw/2-7,top,tw+14,th+6)
        bg=QColor('#101318'); bg.setAlpha(max(25,int(alpha*0.55)))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(bg); p.drawRoundedRect(box,4,4)
        fg=QColor('#f2f5f8'); fg.setAlpha(alpha); p.setPen(fg); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawText(box,Qt.AlignmentFlag.AlignCenter,text)

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
        if self.owner.show_perspective.isChecked() and (
                not getattr(self.owner,'export_render_mode',False)
                or not hasattr(self.owner,'export_overlay_perspective')
                or self.owner.export_overlay_perspective.isChecked()):
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
        self._draw_lens_export_overlay(p)

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
        if base_pair_complete:
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
        # V5.32: VP3 at infinity still represents a valid direction family.
        # Instead of a fan from a fake finite point, draw evenly spaced PARALLEL guides
        # aligned to the average direction of the two user calibration lines.
        if (base_pair_ready and self.owner._persp_axis_complete.get('vp3',False)
                and self.owner._vp3_solver_state().get('mode')=='infinity'
                and self.owner.vp_ray_visible.get('vp3',True)):
            lines=self.owner.perspective_lines.get('vp3',[])
            if len(lines)>=2:
                # V5.36: derive the Y/up guide direction from the solved camera horizon.
                # The user's VP3 strokes choose/validate the axis, but do not independently
                # rotate the entire grid. This keeps X/Y/Z mutually consistent.
                if lines:
                    ux,uy=self.owner._camera_consistent_vp3_direction()
                    # Normal to the parallel-line family.
                    nx=-uy; ny=ux
                    r=self.image_rect
                    corners=[QPointF(r.left(),r.top()),QPointF(r.right(),r.top()),
                             QPointF(r.right(),r.bottom()),QPointF(r.left(),r.bottom())]
                    projs=[c.x()*nx+c.y()*ny for c in corners]
                    lo,hi=min(projs),max(projs)
                    count=max(2,int(self.owner.vp_ray_counts.get('vp3',12)))
                    col=QColor(self.owner.vp_ray_colors['vp3'])
                    col.setAlpha(round(255*self.owner.perspective_alpha.value()/100))
                    pen=QPen(col); pen.setWidthF(self.owner.perspective_line_width.value.value()); p.setPen(pen)
                    radius=20000.0
                    for i in range(count):
                        t=lo+(hi-lo)*(i+0.5)/count
                        # point on line n·p=t
                        cx=nx*t; cy=ny*t
                        a=QPointF(cx-ux*radius,cy-uy*radius)
                        b=QPointF(cx+ux*radius,cy+uy*radius)
                        p.drawLine(a,b)
                    if hasattr(self.owner,'show_perspective_grid') and self.owner.show_perspective_grid.isChecked():
                        p.save(); p.setClipPath(self._frame_clip_path())
                        for i in range(count):
                            t=lo+(hi-lo)*(i+0.5)/count
                            cx=nx*t; cy=ny*t
                            p.drawLine(QPointF(cx-ux*radius,cy-uy*radius),
                                       QPointF(cx+ux*radius,cy+uy*radius))
                        p.restore()

        for label,xy,color,key in vp_defs:
            ready = base_pair_ready and (key in ('vp1','vp2') or (self.owner._persp_axis_complete.get('vp3',False) and self.owner._vp3_solver_state().get('mode')=='finite'))
            if not ready or not self.owner.vp_ray_visible.get(key,True):
                continue
            vp=self._image_norm_to_point(*xy); count=max(2,int(self.owner.vp_ray_counts.get(key,12)))
            rc=QColor(color); rc.setAlpha(round(255*self.owner.perspective_alpha.value()/100)); rp=QPen(rc); rp.setWidthF(self.owner.perspective_line_width.value.value()); p.setPen(rp)
            radius=20000.0

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

        # Optional source-line overlay from automatic detection.  These are not the
        # perspective grid itself; they show the image edges that supported the solve.
        if hasattr(self.owner,'show_auto_detected_lines') and self.owner.show_auto_detected_lines.isChecked():
            p.save(); p.setClipPath(self._frame_clip_path())
            for item in getattr(self.owner,'auto_detected_lines',[]):
                try:
                    key,a0,b0=item[0],item[1],item[2]
                    col=QColor(self.owner.vp_ray_colors.get(key,'#aab4c0')); col.setAlpha(round(255*self.owner.perspective_alpha.value()/100*0.60))
                    pen=QPen(col); pen.setWidthF(max(0.5,self.owner.perspective_line_width.value.value()*0.75)); p.setPen(pen)
                    p.drawLine(self._image_norm_to_point(*a0),self._image_norm_to_point(*b0))
                except Exception:
                    pass
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
                c=QColor(color); base_alpha=self.owner.perspective_alpha.value()/100
                # V5.33: once a VP is complete, both calibration lines use the same
                # confirmed appearance. Do not leave line 2 looking active.
                line_alpha=(0.55 if complete else (1.0 if active else 0.55))
                c.setAlpha(round(255*base_alpha*line_alpha)); pen=QPen(c)
                # While positioning a calibration line, make it slightly bolder.
                # Once the second line is confirmed, return it to the same thin weight
                # as the first confirmed line while keeping the handles available.
                basew=self.owner.perspective_line_width.value.value(); pen.setWidthF(max(1.0,basew*2.0) if (active and not complete) else basew); p.setPen(pen)
                a=self._image_norm_to_point(*line[0]); b=self._image_norm_to_point(*line[1]); p.drawLine(a,b)
                dx=b.x()-a.x(); dy=b.y()-a.y(); ln=math.hypot(dx,dy)
                # When VP3 is infinity, the two green calibration segments are enough.
                # Extending them across the workspace looks like a false finite VP3 grid.
                suppress_extension=(key=='vp3' and complete and getattr(self.owner,'vp3_at_infinity',False))
                if ln>1e-6 and not suppress_extension:
                    ext=10000.0/ln; cc=QColor(color); base_alpha=self.owner.perspective_alpha.value()/100; cc.setAlpha(round(255*base_alpha*(0.55 if active else 0.25))); xp=QPen(cc)
                    xp.setWidthF(max(0.75,basew*1.5) if (active and not complete) else max(0.5,basew*0.75)); xp.setStyle(Qt.PenStyle.DashLine); p.setPen(xp)
                    p.drawLine(QPointF(a.x()-dx*ext,a.y()-dy*ext),QPointF(a.x()+dx*ext,a.y()+dy*ext))
                show_handle=(active and self.owner.show_perspective_handles.isChecked())
                # V5.33: VP3 final confirmation should look finished. Keep the two
                # calibration lines, but remove the white endpoint points after VP3 completes.
                if key=='vp3' and complete:
                    show_handle=False
                if (not getattr(self.owner,'export_render_mode',False)) and show_handle:
                    outline=QPen(QColor(color)); outline.setWidthF(2.0); p.setPen(outline); p.setBrush(QColor('#ffffff'))
                    for ptxy in line:
                        hp=self._image_norm_to_point(*ptxy); p.drawEllipse(QRectF(hp.x()-5,hp.y()-5,10,10))
        # solved VP markers only; unsolved defaults stay invisible.
        for label,xy,color,key in vp_defs:
            if not self.owner._persp_axis_complete.get(key,False):
                continue
            # VP3 at infinity has no finite point to mark.
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
            # V5.33: completed VP3 points are intentionally hidden, so they must not
            # remain as invisible hit targets.
            if name=='vp3' and self.owner._persp_axis_complete.get('vp3',False):
                return None
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
            if (hasattr(self.owner,'right_tabs') and self.owner.right_tabs.currentIndex()==1
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
        persp_tab=(hasattr(self.owner,'right_tabs') and self.owner.right_tabs.currentIndex()==1)
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
        # V5.32: only VP3 gets lost-release recovery. VP1/VP2 retain V5.11 behavior.
        if (self.drag_item and self.drag_item[0]=='perspective_draw'
                and self.drag_item[1]=='vp3'
                and not (e.buttons() & Qt.MouseButton.LeftButton)):
            self._finish_vp3_pencil_stroke()
            self._update_cursor(e.position())
            return
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
    def _finish_vp3_pencil_stroke(self):
        """V5.32: robustly finalize only VP3 pencil strokes, including lost-release recovery."""
        if not self.drag_item or self.drag_item[0]!='perspective_draw' or self.drag_item[1]!='vp3':
            return False
        name,li=self.drag_item[1],self.drag_item[2]
        line=self.owner.perspective_lines[name][li]
        # Judge in screen pixels so short distant vertical edges are still valid.
        a=self._image_norm_to_point(*line[0]); b=self._image_norm_to_point(*line[1])
        pixel_len=math.hypot(b.x()-a.x(),b.y()-a.y())

        # Detach first. This prevents VP3 line 2 remaining attached to the cursor.
        self.drag_item=None
        if pixel_len < 1.0:
            self.owner.refresh()
            return True

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
            if hasattr(self.owner,'learn_current_perspective'):
                try:
                    self.owner.perspective_source='manual'
                    self.owner.learn_current_perspective('manual',axes=[name])
                except Exception:
                    pass
            self.owner._auto_restore_rays_after_manual_solve(name)
            self.owner.advance_after_axis_complete(name)

        self.owner.save_perspective()
        self.owner.refresh()
        return True

    def mouseReleaseEvent(self,e):
        if self.drag_item and self.drag_item[0].startswith('frame_'): self.owner.save_frame()
        if self.drag_item and self.drag_item[0]=='perspective_draw':
            if self.drag_item[1]=='vp3':
                self._finish_vp3_pencil_stroke()
                return
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
                    # V5.30: collect the user's confirmed manual lines as learning data,
                    # without letting learning/autodetection touch the manual input engine.
                    if hasattr(self.owner,'learn_current_perspective'):
                        try:
                            self.owner.perspective_source='manual'
                            self.owner.learn_current_perspective('manual',axes=[name])
                        except Exception:
                            pass
                    self.owner._auto_restore_rays_after_manual_solve(name)
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
        super().__init__(); self.setWindowTitle('Movie Shot Analyzer V6.0.2 Manual VP + Camera Info'); self.resize(1500,920); self.setMinimumSize(1050,680); self.setAcceptDrops(True)
        self.paths=[]; self.current_index=-1; self.original=None; self.frame_quad=[(0.,0.),(1.,0.),(1.,1.),(0.,1.)]; self.frames={}
        self.perspective_by_image={}
        self.learning_enabled=True
        self.learning_data={'version':1,'samples':[],'feature_mean':{'length':0.16,'border':0.25,'vertical':0.25},'count':0}
        self.perspective_source='manual'
        self._load_learning_data()
        self.auto_detected_lines=[]
        self.auto_analysis_quality=None
        self.auto_analysis_note=''
        self.auto_candidates=[]; self.auto_candidate_index=-1
        self.vp1=(-0.30,0.50); self.vp2=(1.30,0.50); self.vp3=(0.50,-0.65); self.eye_level_y=0.50; self.view_zoom=1.0
        self.active_perspective_axis='vp1'; self.perspective_step=0; self.vp3_at_infinity=False
        self._persp_axis_complete={'vp1':False,'vp2':False,'vp3':False}; self._persp_anchor_touched={}; self.vp3_at_infinity=False; self.show_perspective_grid_default=True
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
        sub=QLabel('V6.0.2 / Manual VP + Camera Info'); sub.setObjectName('subtitle'); c.addWidget(sub)
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
        self.export_overlay_perspective=QCheckBox('グリッド・基準線を含める'); self.export_overlay_perspective.setChecked(True); c.addWidget(self.export_overlay_perspective)
        self.export_overlay_lens=QCheckBox('レンズ/カメラ情報を上方中央に含める'); self.export_overlay_lens.setChecked(True); c.addWidget(self.export_overlay_lens)
        er=QHBoxLayout(); er.addWidget(QLabel('レンズ文字')); self.export_lens_font_size=QSpinBox(); self.export_lens_font_size.setRange(8,24); self.export_lens_font_size.setValue(11); self.export_lens_font_size.setSuffix(' px'); er.addWidget(self.export_lens_font_size); c.addLayout(er)
        er=QHBoxLayout(); er.addWidget(QLabel('レンズ情報透明度')); self.export_lens_alpha=QSlider(Qt.Orientation.Horizontal); self.export_lens_alpha.setRange(10,100); self.export_lens_alpha.setValue(78); er.addWidget(self.export_lens_alpha,1); self.export_lens_alpha_label=QLabel('78%'); self.export_lens_alpha.valueChanged.connect(lambda v:self.export_lens_alpha_label.setText(f'{v}%')); er.addWidget(self.export_lens_alpha_label); c.addLayout(er)
        export_current=QPushButton('現在の画像を書き出し'); export_current.clicked.connect(self.export_current_image); c.addWidget(export_current)
        self.export_batch_button=QPushButton('全画像を一括書き出し'); self.export_batch_button.clicked.connect(self.batch_export_images); c.addWidget(self.export_batch_button)
        self.export_cancel_button=QPushButton('一括書き出しをキャンセル'); self.export_cancel_button.clicked.connect(self.cancel_batch_export); self.export_cancel_button.setEnabled(False); c.addWidget(self.export_cancel_button)
        self.export_progress_label=QLabel('書き出し: 待機中'); self.export_progress_label.setObjectName('note'); self.export_progress_label.setWordWrap(True); c.addWidget(self.export_progress_label)
        export_note=QLabel('表示中の構図ガイド・手動パース・3軸グリッド・緑フレームを現在の色/太さ/透明度でPNG保存します。レンズ推定は上方中央に小さな横一列で出力できます。編集用ハンドルは出力しません。'); export_note.setObjectName('note'); export_note.setWordWrap(True); c.addWidget(export_note)
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
        panel=QWidget(); panel.setObjectName('rightPanel'); panel.setMinimumWidth(300); panel.setMaximumWidth(360); self.right_panel=panel
        r=QVBoxLayout(panel); r.setContentsMargins(8,8,8,8); r.setSpacing(6)
        self.right_tabs=QTabWidget(); self.right_tabs.setObjectName('rightTabs'); r.addWidget(self.right_tabs)
        self._build_composition_tab(); self._build_perspective_tab(); self._build_analysis_tab()
        outer.addWidget(panel,0)

    def _build_perspective_tab(self):
        tab=QWidget(); outer=QVBoxLayout(tab); outer.setContentsMargins(0,0,0,0)
        sc=QScrollArea(); sc.setWidgetResizable(True); sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body=QWidget(); lay=QVBoxLayout(body); lay.setContentsMargins(10,10,10,10); lay.setSpacing(9)
        # Display/input flags kept internally; explanatory block removed from top of tab.
        self.show_perspective=QCheckBox(); self.show_perspective.setChecked(True); self.show_perspective.hide()
        self.show_perspective_handles=QCheckBox(); self.show_perspective_handles.setChecked(True); self.show_perspective_handles.hide()
        self.perspective_pencil=QCheckBox(); self.perspective_pencil.setChecked(True); self.perspective_pencil.hide()
        self.show_perspective_grid=QCheckBox(); self.show_perspective_grid.setChecked(True); self.show_perspective_grid.hide()
        # V5.37: automatic perspective analysis is intentionally detached from the UI.
        # The implementation remains in source for a later, isolated reintroduction.
        self.auto_perspective_btn=None
        self.show_auto_detected_lines=QCheckBox()
        self.show_auto_detected_lines.setChecked(False)
        self.show_auto_detected_lines.hide()
        self.auto_analysis_label=QLabel()
        self.auto_analysis_label.hide()
        self.auto_candidate_buttons=[]
        self.section(lay,'カメラキャリブレーション')
        axisrow=QHBoxLayout(); self.axis_buttons={}
        tips={
            'vp1':'X軸方向。画像内の同じ実世界方向に沿う基準線を2本ドラッグします。',
            'vp2':'Z軸（奥行き）方向。同じ実世界方向に沿う基準線を2本ドラッグします。',
            'vp3':'Y軸（上方向・垂直）。同じ実世界方向に沿う基準線を2本ドラッグします。'}
        for key,label in [('vp1','X軸（水平・左右方向）'),('vp2','Z軸（奥行き方向）'),('vp3','Y軸（垂直・上下方向）')]:
            b=QPushButton(label); b.setCheckable(True); b.setToolTip(tips[key]); b.clicked.connect(lambda checked,k=key:self.set_perspective_axis(k)); axisrow.addWidget(b); self.axis_buttons[key]=b
        lay.addLayout(axisrow)
        self.persp_step_label=QLabel('1本目：画像上をドラッグして引く'); self.persp_step_label.setObjectName('fileLabel'); lay.addWidget(self.persp_step_label)
        self.persp_label=QLabel('X / Y / Z を同一カメラとして解きます'); self.persp_label.setObjectName('note'); self.persp_label.setWordWrap(True); lay.addWidget(self.persp_label)
        row=QHBoxLayout(); resetaxis=QPushButton('選択軸をリセット'); resetaxis.clicked.connect(self.reset_active_perspective_axis); resetall=QPushButton('全てリセット'); resetall.clicked.connect(self.reset_perspective); row.addWidget(resetaxis); row.addWidget(resetall); lay.addLayout(row)
        self.section(lay,'放射線')
        self.vp_ray_checks={}; self.vp_ray_count_labels={}; self.vp_ray_color_buttons={}
        for key,label in [('vp1','X 放射線'),('vp2','Z 放射線'),('vp3','Y（∞時は平行ガイド）')]:
            row=QHBoxLayout()
            chk=QCheckBox(label); chk.setChecked(self.vp_ray_visible[key]); chk.toggled.connect(lambda v,k=key:self.set_vp_ray_visible(k,v)); row.addWidget(chk); self.vp_ray_checks[key]=chk
            minus=QPushButton('−'); minus.setFixedWidth(34); minus.clicked.connect(lambda checked=False,k=key:self.change_vp_ray_count(k,-1)); row.addWidget(minus)
            val=QLabel(str(self.vp_ray_counts[key])); val.setAlignment(Qt.AlignmentFlag.AlignCenter); val.setFixedWidth(30); row.addWidget(val); self.vp_ray_count_labels[key]=val
            plus=QPushButton('+'); plus.setFixedWidth(34); plus.clicked.connect(lambda checked=False,k=key:self.change_vp_ray_count(k,1)); row.addWidget(plus)
            col=QPushButton('色'); col.setFixedWidth(42); col.clicked.connect(lambda checked=False,k=key:self.choose_vp_ray_color(k)); row.addWidget(col); self.vp_ray_color_buttons[key]=col
            lay.addLayout(row)
        self._update_vp_color_buttons()
        row=QHBoxLayout(); row.addWidget(QLabel('グリッド/放射線の太さ')); self.perspective_line_width=StepControl(0.5,5.0,0.5,0.5); self.perspective_line_width.value.valueChanged.connect(self.refresh); row.addWidget(self.perspective_line_width); lay.addLayout(row)
        row=QHBoxLayout(); row.addWidget(QLabel('グリッド/放射線の透明度')); self.perspective_alpha=QSlider(Qt.Orientation.Horizontal); self.perspective_alpha.setRange(0,100); self.perspective_alpha.setValue(70); self.perspective_alpha.valueChanged.connect(self.refresh); row.addWidget(self.perspective_alpha,1); self.perspective_alpha_label=QLabel('70%'); self.perspective_alpha_label.setFixedWidth(42); self.perspective_alpha.valueChanged.connect(lambda v:self.perspective_alpha_label.setText(f'{v}%')); row.addWidget(self.perspective_alpha_label); lay.addLayout(row)
        solve_btn=QPushButton('カメラ情報を計算')
        solve_btn.setToolTip('手動で確定したVPは動かさず、FOV・35mm換算レンズ・Solve errorを計算します。')
        solve_btn.clicked.connect(self.update_lens_estimate)
        lay.addWidget(solve_btn)
        self.section(lay,'学習')
        self.learning_check=QCheckBox('手動・修正パースを学習に使用'); self.learning_check.setChecked(True); self.learning_check.toggled.connect(self.set_learning_enabled); lay.addWidget(self.learning_check)
        self.learning_label=QLabel(f'学習データ：{self.learning_data.get("count",0)}件'); self.learning_label.setObjectName('note'); lay.addWidget(self.learning_label)
        lr=QHBoxLayout(); learn_now=QPushButton('現在の手動パースを学習'); learn_now.clicked.connect(lambda:self.learn_current_perspective('manual')); lr.addWidget(learn_now); reset_learn=QPushButton('学習をリセット'); reset_learn.clicked.connect(self.reset_learning_data); lr.addWidget(reset_learn); lay.addLayout(lr)
        self.section(lay,'カメラ推定結果')
        self.persp_lens=QLabel('X / Y / Z の基準線からカメラ・レンズを推定します。'); self.persp_lens.setObjectName('fileLabel'); self.persp_lens.setWordWrap(True); lay.addWidget(self.persp_lens)
        self.persp_lens_detail=QLabel('手動基準線を観測データとして使用し、軸の整合性とSolve errorを表示します。'); self.persp_lens_detail.setObjectName('note'); self.persp_lens_detail.setWordWrap(True); lay.addWidget(self.persp_lens_detail)
        self.section(lay,'表示設定')
        self.show_axis_baselines=QCheckBox('軸の基準線')
        self.show_axis_baselines.setChecked(True)
        self.show_axis_baselines.toggled.connect(self.refresh)
        lay.addWidget(self.show_axis_baselines)
        self.show_vp_markers=QCheckBox('消失点マーカー')
        self.show_vp_markers.setChecked(True)
        self.show_vp_markers.toggled.connect(self.refresh)
        lay.addWidget(self.show_vp_markers)
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

        self.section(c,'構図考察ガイド')
        add=QGridLayout(); add.setSpacing(6)
        specs=[('show_radiating','放射構図'),('show_tunnel','トンネル'),('show_golden_triangle','ゴールデントライアングル'),('show_circle','円構図'),('show_cshape','C字構図'),('show_vshape','V字構図'),('show_double_diagonal','ダブル対角線'),('show_scurve','S字構図'),('show_lshape','L字構図'),('show_pyramid','ピラミッド構図')]
        for i,(attr,label) in enumerate(specs):
            b=self._make_toggle_button(label,False); b.toggled.connect(self.comp_guide_visibility_changed); setattr(self,attr,b); add.addWidget(b,i//2,i%2)
        c.addLayout(add)
        self.edit_comp_guides=QCheckBox('構図考察ガイドを編集'); self.edit_comp_guides.setChecked(True); self.edit_comp_guides.toggled.connect(self.comp_edit_toggled); c.addWidget(self.edit_comp_guides)
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

    def _auto_restore_rays_after_manual_solve(self,name):
        """Stable behavior: X/Z rays appear when both are solved; Y appears after Y solve."""
        if name in ('vp1','vp2'):
            if (self._persp_axis_complete.get('vp1',False)
                    and self._persp_axis_complete.get('vp2',False)):
                for k in ('vp1','vp2'):
                    self.vp_ray_visible[k]=True
                    if hasattr(self,'vp_ray_checks') and k in self.vp_ray_checks:
                        self.vp_ray_checks[k].setChecked(True)
        elif name=='vp3':
            self.vp_ray_visible['vp3']=True
            if hasattr(self,'vp_ray_checks') and 'vp3' in self.vp_ray_checks:
                self.vp_ray_checks['vp3'].setChecked(True)

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
                if self.active_perspective_axis=='vp3':
                    if getattr(self,'vp3_at_infinity',False):
                        self.persp_step_label.setText('VP3 完了：∞（平行ガイド）')
                    else:
                        self.persp_step_label.setText('VP3 完了')
                else:
                    self.persp_step_label.setText(f'{lab} 完了：白○で微調整')
            elif self.perspective_step==0:
                self.persp_step_label.setText(f'{lab} 1本目：短いエッジでもOK・ドラッグして基準線')
            else:
                self.persp_step_label.setText(f'{lab} 2本目：短いエッジでもOK・離すと確定')
    def reset_active_perspective_axis(self):
        # Reset only the selected calibration axis.  Keep ray/grid visibility settings.
        # Start from truly blank geometry so old handles/default lines cannot intercept
        # the next pencil stroke or leave the axis in a half-complete state.
        name=self.active_perspective_axis
        self.perspective_lines[name]=[
            [(0.5,0.5),(0.5,0.5)],
            [(0.5,0.5),(0.5,0.5)]
        ]
        self._persp_axis_complete[name]=False
        self._persp_anchor_touched[(name,0)]=set()
        self._persp_anchor_touched.pop((name,1),None)
        self.perspective_step=0
        if name=='vp3':
            self.vp3_at_infinity=False
        self.update_perspective_panel_state()
        self.update_perspective_labels()
        self.save_perspective()
        self.refresh()

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
            self.auto_detected_lines=list(pd.get('auto_lines',[])); self.auto_analysis_quality=pd.get('auto_quality',None); self.auto_analysis_note=str(pd.get('auto_note',''))
            if hasattr(self,'auto_analysis_label'):
                if self.auto_analysis_quality is None: self.auto_analysis_label.setText('自動解析：未実行')
                else: self.auto_analysis_label.setText(f'自動解析：信頼度 {self.auto_analysis_quality:.0f}%'+(f' / {self.auto_analysis_note}' if self.auto_analysis_note else ''))
            self._persp_anchor_touched={}
            # A/B/C candidates belong to the current analysis run; never carry stale candidates to another image.
            self.auto_candidates=[]; self.auto_candidate_index=-1
            for b in getattr(self,'auto_candidate_buttons',[]):
                b.setEnabled(False); b.setChecked(False)
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
    def _frame_bbox_norm(self):
        xs=[q[0] for q in self.frame_quad]; ys=[q[1] for q in self.frame_quad]
        return (max(0.0,min(xs)),max(0.0,min(ys)),min(1.0,max(xs)),min(1.0,max(ys)))

    def _detect_segments_cv(self):
        """Detect long line segments and return normalized endpoints + length."""
        if self.original is None or cv2 is None or np is None:
            return []
        rgb=np.asarray(self.original.convert('RGB'))
        h0,w0=rgb.shape[:2]
        l,t,r,b=self._frame_bbox_norm()
        x0=max(0,min(w0-1,int(round(l*w0)))); y0=max(0,min(h0-1,int(round(t*h0))))
        x1=max(x0+2,min(w0,int(round(r*w0)))); y1=max(y0+2,min(h0,int(round(b*h0))))
        crop=rgb[y0:y1,x0:x1]
        if crop.size==0:return []
        ch,cw=crop.shape[:2]
        scale=min(1.0,1200.0/max(ch,cw))
        if scale<1.0:
            crop=cv2.resize(crop,(max(2,int(cw*scale)),max(2,int(ch*scale))),interpolation=cv2.INTER_AREA)
        gray=cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY)
        gray=cv2.GaussianBlur(gray,(5,5),0)
        med=float(np.median(gray)); low=int(max(20,0.66*med)); high=int(min(240,max(low+30,1.33*med)))
        edges=cv2.Canny(gray,low,high,L2gradient=True)
        hh,ww=edges.shape[:2]; diag=math.hypot(ww,hh)
        raw=cv2.HoughLinesP(edges,1,np.pi/360,threshold=max(28,int(diag*0.035)),minLineLength=max(24,int(diag*0.045)),maxLineGap=max(8,int(diag*0.012)))
        if raw is None:return []
        out=[]
        invs=1.0/scale
        for ln in raw[:,0,:]:
            xa,ya,xb,yb=[float(v) for v in ln]
            length=math.hypot(xb-xa,yb-ya)
            if length<diag*0.04: continue
            xa=xa*invs+x0; xb=xb*invs+x0; ya=ya*invs+y0; yb=yb*invs+y0
            a=(xa/w0,ya/h0); bb=(xb/w0,yb/h0)
            # Ignore near-frame-border detections; black bars and the green frame can dominate otherwise.
            mx=(a[0]+bb[0])*0.5; my=(a[1]+bb[1])*0.5
            
            # Architecture prior: long, clean, frame-spanning straight edges dominate.
            # Short/local edges (often people, clothing, hair and small props) are deliberately weak.
            nlen=length/diag
            border=min(mx,1.0-mx,my,1.0-my)
            span=max(abs(a[0]-bb[0]),abs(a[1]-bb[1]))
            structure=1.0 + min(2.5,nlen*8.0) + min(1.2,span*1.8)
            if nlen < 0.085: structure*=0.26
            elif nlen < 0.13: structure*=0.58
            # Long frame-spanning architecture is more trustworthy than local clusters around people.
            if span >= 0.28: structure*=1.28
            elif span >= 0.20: structure*=1.12
            center_dist=math.hypot(mx-0.5,my-0.5)
            if center_dist < 0.28 and nlen < 0.17 and span < 0.24:
                structure*=0.30
            # Dense short edges near the central subject area are frequently people/clothing/props.
            if 0.20 < mx < 0.80 and 0.16 < my < 0.88 and nlen < 0.105:
                structure*=0.42
            # Architectural frames often live near the image perimeter; modest bonus only.
            if border < 0.18: structure*=1.16
            # Personal adaptive prior learned from the user's accepted manual/corrected lines.
            if self.learning_enabled and self.learning_data.get('count',0)>0:
                pref=self.learning_data.get('feature_mean',{})
                plen=float(pref.get('length',0.16)); pborder=float(pref.get('border',0.25))
                length_fit=max(0.55,min(1.65,0.85+nlen/max(plen,0.05)*0.20))
                border_fit=max(0.72,min(1.28,1.18-abs(border-pborder)*0.8))
                structure*=length_fit*border_fit
            out.append({'a':a,'b':bb,'length':nlen,'mid':(mx,my),'structure':structure,'border':border})
        out.sort(key=lambda z:z['length'],reverse=True)
        # Hough often returns duplicates. Remove almost-collinear, nearby duplicates.
        ded=[]
        for seg in out:
            ax,ay=seg['a']; bx,by=seg['b']; ang=math.atan2(by-ay,bx-ax)%math.pi
            keep=True
            for prev in ded[-50:]:
                pax,pay=prev['a']; pbx,pby=prev['b']; pang=math.atan2(pby-pay,pbx-pax)%math.pi
                da=abs(ang-pang); da=min(da,math.pi-da)
                dm=math.hypot(seg['mid'][0]-prev['mid'][0],seg['mid'][1]-prev['mid'][1])
                if da<math.radians(1.2) and dm<0.025:
                    keep=False; break
            if keep: ded.append(seg)
            if len(ded)>=110: break
        return ded

    def _vp_candidate_score(self,vp,segments,err_limit=0.040):
        vx,vy=vp; support=[]; score=0.0; errs=[]
        for i,s in enumerate(segments):
            mx,my=s['mid']; dx=s['b'][0]-s['a'][0]; dy=s['b'][1]-s['a'][1]
            dl=math.hypot(dx,dy); rx=vx-mx; ry=vy-my; rl=math.hypot(rx,ry)
            if dl<1e-8 or rl<1e-8: continue
            err=abs(dx*ry-dy*rx)/(dl*rl)  # sin angular residual
            # Angular residual; relaxed mode widens this for low-contrast / sparse frames.
            if err<err_limit:
                w=s['length']*s.get('structure',1.0)*(1.0-err/err_limit)
                score+=w; support.append(i); errs.append(err)
        if len(support)<2:return (0.0,[],1.0)
        # Reward spatially distributed support rather than several duplicate edges from one object.
        mids=[segments[i]['mid'] for i in support]
        spread=0.0
        for i in range(min(len(mids),12)):
            for j in range(i+1,min(len(mids),12)):
                spread=max(spread,math.hypot(mids[i][0]-mids[j][0],mids[i][1]-mids[j][1]))
        score*=0.65+min(0.7,spread)
        return (score,support,sum(errs)/len(errs))

    def _find_vp_clusters(self,segments,max_clusters=9,relaxed=False):
        """Find diverse VP hypotheses. Relaxed mode is a fallback for sparse/low-contrast frames."""
        if len(segments)<2:return []
        min_seed_len=0.070 if relaxed else 0.095
        min_struct=1.40 if relaxed else 1.75
        seed_ids=[i for i,s in enumerate(segments) if s['length']>=min_seed_len or s.get('structure',1.0)>=min_struct]
        if len(seed_ids)<4: seed_ids=list(range(min(len(segments),110 if relaxed else 90)))
        seed_ids=seed_ids[:110 if relaxed else 90]
        candidates=[]
        min_cross=math.sin(math.radians(1.8 if relaxed else 3.0))
        bound=14.0 if relaxed else 10.0
        for ii in range(len(seed_ids)):
            i=seed_ids[ii]; a=segments[i]
            adx=a['b'][0]-a['a'][0]; ady=a['b'][1]-a['a'][1]; al=math.hypot(adx,ady)
            for jj in range(ii+1,len(seed_ids)):
                j=seed_ids[jj]; b=segments[j]
                bdx=b['b'][0]-b['a'][0]; bdy=b['b'][1]-b['a'][1]; bl=math.hypot(bdx,bdy)
                if al<1e-9 or bl<1e-9:continue
                sine=abs(adx*bdy-ady*bdx)/(al*bl)
                if sine<min_cross:continue
                ip=infinite_line_intersection(a['a'],a['b'],b['a'],b['b'])
                if ip is None:continue
                if not (-bound<=ip[0]<=1+bound and -bound<=ip[1]<=1+bound):continue
                candidates.append(ip)
        if not candidates:return []
        cap=1800 if relaxed else 1300
        if len(candidates)>cap:
            step=max(1,len(candidates)//cap); candidates=candidates[::step][:cap]
        scored=[]; err_limit=0.058 if relaxed else 0.040
        for vp in candidates:
            sc,supp,err=self._vp_candidate_score(vp,segments,err_limit)
            if len(supp)<2 or sc<(0.030 if relaxed else 0.055):continue
            long_support=sum(1 for gi in supp if segments[gi]['length']>=(0.085 if relaxed else 0.12))
            if long_support==0: sc*=0.60 if relaxed else 0.55
            mids=[segments[gi]['mid'] for gi in supp[:24]]
            if mids:
                xs=[m[0] for m in mids]; ys=[m[1] for m in mids]
                distribution=(max(xs)-min(xs))+(max(ys)-min(ys))
                sc*=0.82+min(0.50,distribution*0.50)
            scored.append((sc,vp,supp,err))
        scored.sort(key=lambda x:x[0],reverse=True)
        clusters=[]
        for sc,vp,supp,err in scored:
            duplicate=False
            for c in clusters:
                d=math.hypot(vp[0]-c['vp'][0],vp[1]-c['vp'][1])
                scale=(0.12 if relaxed else 0.18)+0.03*max(math.hypot(*vp),math.hypot(*c['vp']))
                overlap=len(set(supp)&set(c['support']))/max(1,min(len(supp),len(c['support'])))
                if d<scale and overlap>0.40:
                    duplicate=True; break
            if duplicate:continue
            angs=[]
            for gi in supp:
                ss=segments[gi]; dx=ss['b'][0]-ss['a'][0]; dy=ss['b'][1]-ss['a'][1]
                angs.append(abs(math.degrees(math.atan2(dy,dx)))%180)
            medvert=sorted([abs(a-90.0) for a in angs])[len(angs)//2] if angs else 90.0
            clusters.append({'vp':vp,'support':supp,'score':sc,'err':err,'vertical_dev':medvert})
            if len(clusters)>=max_clusters:break
        return clusters


    def _segment_angle_deg(self,s):
        dx=s['b'][0]-s['a'][0]; dy=s['b'][1]-s['a'][1]
        a=math.degrees(math.atan2(dy,dx))%180.0
        return a

    def _angle_distance_deg(self,a,b):
        d=abs(a-b)%180.0
        return min(d,180.0-d)

    def _direction_groups(self,segments,relaxed=False):
        """Cluster segments by dominant image direction before solving vanishing points.

        This intentionally changes the order of operations compared with V5.17:
        first find major architectural directions, then estimate one VP per direction.
        It helps prevent a dense local intersection around people/props from becoming
        a false global VP.
        """
        if not segments: return []
        binw=5.0
        bins=[0.0]*36
        for i,s in enumerate(segments):
            a=self._segment_angle_deg(s)
            # Long, structural and spatially useful edges dominate the histogram.
            w=max(0.001,s['length']*s.get('structure',1.0))
            if s['length']>=0.20: w*=1.45
            elif s['length']<0.08: w*=0.45
            mx,my=s['mid']
            if 0.20<mx<0.80 and 0.18<my<0.86 and s['length']<0.16:
                w*=0.34
            # Very short central edges should almost never define a global perspective family.
            if 0.28<mx<0.72 and 0.24<my<0.82 and s['length']<0.10:
                w*=0.28
            bi=int(a/binw)%36
            # small circular smoothing so one true family doesn't split on a bin edge
            bins[bi]+=w
            bins[(bi-1)%36]+=w*0.42
            bins[(bi+1)%36]+=w*0.42
        peaks=[]
        for i,v in enumerate(bins):
            if v<=0: continue
            if v>=bins[(i-1)%36] and v>=bins[(i+1)%36]:
                peaks.append((v,(i+0.5)*binw))
        peaks.sort(reverse=True)
        chosen=[]
        min_sep=12.0 if relaxed else 15.0
        for v,a in peaks:
            if all(self._angle_distance_deg(a,b)>min_sep for _,b in chosen):
                chosen.append((v,a))
            if len(chosen)>=6: break
        groups=[]
        win=18.0 if relaxed else 14.0
        for rank,(strength,peak) in enumerate(chosen):
            ids=[]
            for i,s in enumerate(segments):
                if self._angle_distance_deg(self._segment_angle_deg(s),peak)<=win:
                    ids.append(i)
            # Keep strongest first, but retain distributed architecture.
            ids=sorted(ids,key=lambda i:segments[i]['length']*segments[i].get('structure',1.0),reverse=True)[:42]
            if len(ids)<2: continue
            mids=[segments[i]['mid'] for i in ids[:20]]
            spreadx=max(m[0] for m in mids)-min(m[0] for m in mids) if mids else 0
            spready=max(m[1] for m in mids)-min(m[1] for m in mids) if mids else 0
            spatial=spreadx+spready
            # A group concentrated in one small patch is likely a person/object edge cluster.
            gscore=strength*(0.52+min(0.95,spatial*1.15))
            if spatial < (0.34 if relaxed else 0.42):
                gscore*=0.48
            medvert=self._angle_distance_deg(peak,90.0)
            groups.append({'ids':ids,'peak':peak,'score':gscore,'vertical_dev':medvert,'spatial':spatial,'rank':rank})
        groups.sort(key=lambda g:g['score'],reverse=True)
        return groups

    def _learned_direction_bonus(self,angle):
        """Small preference for directions the user repeatedly accepted manually.

        This is deliberately a soft prior: it can rank plausible architecture, but it cannot
        rescue a geometrically weak candidate.
        """
        if not self.learning_enabled or self.learning_data.get('count',0) < 2:
            return 0.0
        vals=[]
        for sm in self.learning_data.get('samples',[]):
            for f in sm.get('features',[]):
                try: vals.append(float(f.get('angle')))
                except Exception: pass
        if not vals:
            return 0.0
        # Circular 180-degree distance; nearest accepted directions get at most a modest bonus.
        d=min(self._angle_distance_deg(angle,v) for v in vals)
        if d <= 4.0: return 0.34
        if d <= 8.0: return 0.22
        if d <= 14.0: return 0.10
        return 0.0

    def _solve_direction_group_vp(self,group,segments,relaxed=False):
        """Robust weighted least-squares VP for one direction family."""
        ids=list(group.get('ids',[]))
        if len(ids)<2 or np is None: return None
        # Prefer long architectural members and iteratively reject outliers.
        active=ids[:]
        vp=None
        for _ in range(3):
            A=[]; B=[]; W=[]
            for i in active:
                s=segments[i]; x1,y1=s['a']; x2,y2=s['b']
                aa=y1-y2; bb=x2-x1; cc=x1*y2-x2*y1
                norm=math.hypot(aa,bb)
                if norm<1e-9: continue
                aa/=norm; bb/=norm; cc/=norm
                w=max(0.01,s['length']*s.get('structure',1.0))
                if s['length']>=0.18: w*=1.35
                A.append((aa,bb)); B.append(-cc); W.append(w)
            if len(A)<2:return None
            A=np.asarray(A,float); B=np.asarray(B,float); W=np.asarray(W,float)
            sw=np.sqrt(W)[:,None]
            try:
                sol,_,_,_=np.linalg.lstsq(A*sw,B*np.sqrt(W),rcond=None)
            except Exception:
                return None
            vp=(float(sol[0]),float(sol[1]))
            # Permit far-off-screen VPs, but reject numerical explosions.
            if not all(math.isfinite(v) for v in vp) or abs(vp[0])>24 or abs(vp[1])>24:
                return None
            residuals=[]
            for i in active:
                s=segments[i]; x1,y1=s['a']; x2,y2=s['b']
                dx=x2-x1; dy=y2-y1; dl=math.hypot(dx,dy)
                mx,my=s['mid']; rx=vp[0]-mx; ry=vp[1]-my; rl=math.hypot(rx,ry)
                err=1.0 if dl<1e-9 or rl<1e-9 else abs(dx*ry-dy*rx)/(dl*rl)
                residuals.append((err,i))
            residuals.sort()
            keep=max(2,int(len(residuals)*(0.78 if relaxed else 0.68)))
            new=[i for _,i in residuals[:keep]]
            if set(new)==set(active): break
            active=new
        if vp is None:return None
        errlim=0.080 if relaxed else 0.052
        sc,supp,err=self._vp_candidate_score(vp,[segments[i] for i in ids],errlim)
        # _vp_candidate_score returned local indices; map to global indices.
        supp_global=[ids[j] for j in supp]
        if len(supp_global)<2:return None
        # Global support may add other collinear architecture outside the initial angle window.
        gsc,gsupp,gerr=self._vp_candidate_score(vp,segments,errlim)
        if len(gsupp)>=len(supp_global):
            supp_global=gsupp; sc=gsc; err=gerr
        long_support=sum(1 for i in supp_global if segments[i]['length']>=0.11)
        if long_support<1 and not relaxed:return None
        score=sc + group.get('score',0.0)*0.28 + min(0.55,long_support*0.08)
        score += self._learned_direction_bonus(group.get('peak',0.0))
        # Reject compact/weak local families unless this is the relaxed fallback.
        if group.get('spatial',0.0) < (0.30 if relaxed else 0.40): score*=0.58
        return {'vp':vp,'support':supp_global,'score':score,'err':err,'vertical_dev':group.get('vertical_dev',90.0),'direction_peak':group.get('peak',0.0),'group_rank':group.get('rank',0),'group_spatial':group.get('spatial',0.0)}

    def _build_direction_cluster_candidates(self,segments,relaxed=False):
        """Create A/B/C from genuinely different direction-family combinations."""
        groups=self._direction_groups(segments,relaxed)
        solved=[]
        for g in groups:
            c=self._solve_direction_group_vp(g,segments,relaxed)
            if c is not None: solved.append(c)
        if not solved:return []
        vertical=[]; planar=[]
        for c in solved:
            # Near-vertical families are validation/VP3, not one of the main horizontal pair.
            if c['vertical_dev'] <= (13.0 if relaxed else 10.0): vertical.append(c)
            else: planar.append(c)
        if len(planar)<2:
            planar=[c for c in solved if c not in vertical] or solved[:]
        pairs=[]
        for i in range(len(planar)):
            for j in range(i+1,len(planar)):
                a,b=planar[i],planar[j]
                # Require genuinely different dominant image directions.
                d=self._angle_distance_deg(a.get('direction_peak',0),b.get('direction_peak',0))
                if d < (20.0 if relaxed else 25.0): continue
                sc=self._pair_candidate_score(a,b,segments,relaxed)
                if sc<=-1e8:continue
                # Distinct direction peaks and broad support are strongly rewarded.
                sc += min(0.75,d/65.0)
                sc += min(0.45,(a.get('group_spatial',0)+b.get('group_spatial',0))*0.22)
                pairs.append((sc,a,b,d))
        pairs.sort(key=lambda x:x[0],reverse=True)
        out=[]; used_sigs=[]
        # V5.22 deliberately refuses weak 2-direction solutions instead of drawing plausible-looking nonsense.
        pair_floor=1.18 if relaxed else 1.48
        for sc,a,b,d in pairs:
            if sc < pair_floor:
                continue
            # label vp1/vp2 by x, preserving the older UI convention
            hs=sorted((a,b),key=lambda c:c['vp'][0])
            sig=tuple(sorted((round(a.get('direction_peak',0)/5)*5,round(b.get('direction_peak',0)/5)*5)))
            if any(len(sig)==len(osig) and sum(abs(x-y) for x,y in zip(sig,osig))<16 for osig in used_sigs):
                continue
            item={'vp1':hs[0],'vp2':hs[1],'score':sc,'direction_signature':sig}
            if vertical:
                vv=max(vertical,key=lambda c:(c['score']-c['err']*5.0))
                # VP3 requires notably stronger evidence than the two main directions.
                if vv['err'] <= (0.042 if relaxed else 0.027) and len(vv['support'])>=3 and vv.get('group_spatial',0.0)>=0.38:
                    item['vp3']=vv
            out.append(item); used_sigs.append(sig)
            if len(out)>=3:break
        # If no reliable pair exists, keep exactly one strong direction when possible.
        # This is preferable to forcing a false 2/3-point perspective.
        if not out:
            strong=[]
            single_floor=0.72 if relaxed else 0.95
            for c in solved:
                long_support=sum(1 for gi in c.get('support',[]) if segments[gi]['length']>=0.11)
                if c['score']>=single_floor and len(c.get('support',[]))>=2 and long_support>=1 and c.get('group_spatial',0.0)>=0.34:
                    strong.append(c)
            strong.sort(key=lambda c:c['score'],reverse=True)
            if strong:
                c=strong[0]
                out=[{'vp1':c,'score':c['score'],'single':True,'direction_signature':(round(c.get('direction_peak',0)/5)*5,),'confidence_state':'1方向のみ'}]
        return out
    def _choose_two_support_lines(self,cluster,segments):
        ids=cluster['support']
        if not ids:return None
        ranked=sorted(ids,key=lambda i:segments[i]['length']*segments[i].get('structure',1.0),reverse=True)
        first=ranked[0]; second=None
        m0=segments[first]['mid']
        for i in ranked[1:]:
            if math.hypot(segments[i]['mid'][0]-m0[0],segments[i]['mid'][1]-m0[1])>0.10:
                second=i; break
        if second is None and len(ranked)>1: second=ranked[1]
        if second is None:return None
        def line(i):
            s=segments[i]; return [tuple(s['a']),tuple(s['b'])]
        return [line(first),line(second)]

    def _learning_path(self):
        base=Path(os.getenv('APPDATA') or (Path.home()/'.movie_shot_analyzer'))
        if os.getenv('APPDATA'): base=base/'MovieShotAnalyzer'
        base.mkdir(parents=True,exist_ok=True)
        return base/'perspective_learning.json'

    def _load_learning_data(self):
        try:
            fp=self._learning_path()
            if fp.exists():
                data=json.loads(fp.read_text(encoding='utf-8'))
                if isinstance(data,dict) and isinstance(data.get('samples',[]),list): self.learning_data=data
        except Exception:
            pass

    def _save_learning_data(self):
        try: self._learning_path().write_text(json.dumps(self.learning_data,ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception: pass

    def set_learning_enabled(self,on):
        self.learning_enabled=bool(on)
        self.statusBar().showMessage('パース学習：ON' if on else 'パース学習：OFF',3000)

    def reset_learning_data(self):
        self.learning_data={'version':1,'samples':[],'feature_mean':{'length':0.16,'border':0.25,'vertical':0.25},'count':0}
        self._save_learning_data()
        if hasattr(self,'learning_label'): self.learning_label.setText('学習データ：0件')
        self.statusBar().showMessage('パース学習データをリセットしました。',4000)

    def _line_features(self,line):
        (x1,y1),(x2,y2)=line; length=math.hypot(x2-x1,y2-y1)
        mx=(x1+x2)/2; my=(y1+y2)/2; border=min(mx,1-mx,my,1-my)
        ang=abs(math.degrees(math.atan2(y2-y1,x2-x1)))%180
        vertical=max(0.0,1.0-min(abs(ang-90.0),90.0)/90.0)
        return {'length':float(length),'border':float(max(0,min(.5,border))),'vertical':float(vertical),'angle':float(ang)}

    def learn_current_perspective(self,source='manual',axes=None):
        v521_family_record = self._v521_learning_record("manual")
        if not self.learning_enabled or self.original is None: return
        axes=axes or [k for k in ('vp1','vp2','vp3') if self._persp_axis_complete.get(k)]
        learned=[]
        for key in axes:
            if not self._persp_axis_complete.get(key): continue
            lines=self.perspective_lines.get(key,[])
            if len(lines)<2: continue
            feats=[self._line_features(line) for line in lines[:2]]
            sig=f"{key}:"+';'.join(f"{round(v,3)}" for line in lines[:2] for pt in line for v in pt)
            if any(x.get('signature')==sig for x in self.learning_data.get('samples',[])): continue
            learned.append({'source':source,'axis':key,'features':feats,'signature':sig})
        if not learned: return
        samples=self.learning_data.setdefault('samples',[]); samples.extend(learned); self.learning_data['samples']=samples[-500:]
        allf=[f for sm in self.learning_data['samples'] for f in sm.get('features',[])]
        if allf:
            self.learning_data['feature_mean']={k:sum(float(f.get(k,0)) for f in allf)/len(allf) for k in ('length','border','vertical')}
        self.learning_data['count']=len(self.learning_data['samples']); self._save_learning_data()
        if hasattr(self,'learning_label'): self.learning_label.setText(f"学習データ：{self.learning_data['count']}件")
        self.statusBar().showMessage(f"パースを学習しました（{source} / 合計 {self.learning_data['count']}件）",3500)

    def _pair_candidate_score(self,a,b,segments,relaxed=False):
        # Strong preference for two distinct, spatially distributed architectural directions.
        vx1,vy1=a['vp']; vx2,vy2=b['vp']
        sep=math.hypot(vx2-vx1,vy2-vy1)
        if sep < (0.30 if relaxed else 0.55): return -1e9
        shared=len(set(a['support']) & set(b['support']))
        if shared:
            if not relaxed or shared>1: return -1e9
            score_overlap_penalty=0.35
        else: score_overlap_penalty=0.0
        score=a['score']+b['score']-score_overlap_penalty
        score += min(0.9,sep*0.20)
        # Two perspective families converging inside the busy center are often local object/person edges.
        for vx,vy in ((vx1,vy1),(vx2,vy2)):
            if 0.18<vx<0.82 and 0.18<vy<0.82:
                score-=0.42
        # Off-screen VPs are normal for standard/telephoto architectural shots; do not penalize them.
        # Horizon should not be absurdly steep for the common architectural 2-point case.
        slope=abs(vy2-vy1)/max(abs(vx2-vx1),0.08)
        if slope>0.65: score-=min(1.2,(slope-0.65)*1.1)
        # Physical lens plausibility is a useful rejection test, not a hard truth.
        cx,cy=.5,.5
        f2=-((vx1-cx)*(vx2-cx)+(vy1-cy)*(vy2-cy))
        if f2<=0: score-=1.0
        else:
            f=math.sqrt(f2); eq=36.0*f
            if 14<=eq<=180: score+=0.45
            elif 8<=eq<=250: score+=0.10
            else: score-=0.5
        return score

    def _build_auto_candidates(self,segments,clusters,relaxed=False):
        """Build up to three genuinely different A/B/C solutions, not three near-duplicates."""
        vertical=[]; horizontal=[]
        for c in clusters:
            longv=sum(1 for i in c['support'] if segments[i]['length']>=(0.085 if relaxed else 0.10))
            if c['vertical_dev']<=(12.0 if relaxed else 10.0) and len(c['support'])>=2 and longv>=1:
                vertical.append(c)
            else: horizontal.append(c)
        if len(horizontal)<2: horizontal=clusters[:]
        pairs=[]
        for i in range(len(horizontal)):
            for j in range(i+1,len(horizontal)):
                sc=self._pair_candidate_score(horizontal[i],horizontal[j],segments,relaxed)
                if sc>-1e8: pairs.append((sc,horizontal[i],horizontal[j]))
        pairs.sort(key=lambda x:x[0],reverse=True)
        out=[]
        def similar_pair(item,other):
            a1,a2=item['vp1']['vp'],item['vp2']['vp']; b1,b2=other['vp1']['vp'],other['vp2']['vp']
            direct=max(math.hypot(a1[0]-b1[0],a1[1]-b1[1]),math.hypot(a2[0]-b2[0],a2[1]-b2[1]))
            support_a=set(item['vp1']['support'])|set(item['vp2']['support'])
            support_b=set(other['vp1']['support'])|set(other['vp2']['support'])
            overlap=len(support_a&support_b)/max(1,min(len(support_a),len(support_b)))
            return direct<0.30 or (direct<0.55 and overlap>0.62)
        for sc,a,b in pairs:
            hs=sorted((a,b),key=lambda c:c['vp'][0])
            item={'vp1':hs[0],'vp2':hs[1],'score':sc}
            if vertical:
                v=max(vertical,key=lambda c:c['score'])
                if v not in hs and v['vertical_dev']<=(10.0 if relaxed else 8.0) and v['err']<=(0.045 if relaxed else 0.028):
                    item['vp3']=v
            if any(similar_pair(item,o) for o in out): continue
            out.append(item)
            if len(out)>=3: break
        # Sparse frames: still expose one-direction hypotheses instead of appearing to do nothing.
        if not out and clusters:
            for c in clusters[:3]:
                out.append({'vp1':c,'score':c['score'],'single':True})
        return out

    def apply_auto_candidate(self,index):
        if not (0<=index<len(self.auto_candidates)): return
        cand=self.auto_candidates[index]; self.auto_candidate_index=index
        segments=getattr(self,'_last_auto_segments',[]); self.auto_detected_lines=[]; qualities=[]
        for key in ('vp1','vp2','vp3'):
            c=cand.get(key)
            if c is None:
                self._persp_axis_complete[key]=False
                continue
            lines=self._choose_two_support_lines(c,segments)
            if lines is None:
                self._persp_axis_complete[key]=False; continue
            self.perspective_lines[key]=lines
            ip=infinite_line_intersection(lines[0][0],lines[0][1],lines[1][0],lines[1][1]) or c['vp']
            ip=(max(-8.0,min(9.0,ip[0])),max(-7.0,min(8.0,ip[1])))
            if key=='vp1': self.vp1=ip
            elif key=='vp2': self.vp2=ip
            else: self.vp3=ip
            self._persp_axis_complete[key]=True
            self._persp_anchor_touched[(key,0)]={0,1}; self._persp_anchor_touched[(key,1)]={0,1}
            for gi in c['support']:
                ss=segments[gi]; self.auto_detected_lines.append((key,tuple(ss['a']),tuple(ss['b'])))
            long_support=sum(1 for gi in c['support'] if segments[gi]['length']>=0.12)
            q=12.0+min(28.0,long_support*5.5)+min(18.0,c['score']*12.0)-c['err']*380.0
            qualities.append(max(5.0,min(82.0,q)))
        if self._persp_axis_complete.get('vp1') and self._persp_axis_complete.get('vp2'):
            x1,y1=self.vp1; x2,y2=self.vp2
            self.eye_level_y=(y1+y2)/2 if abs(x2-x1)<1e-9 else y1+(0.5-x1)*(y2-y1)/(x2-x1)
        self.perspective_source='auto'; self.auto_analysis_quality=sum(qualities)/len(qualities) if qualities else 0.0
        axes=sum(1 for k in ('vp1','vp2','vp3') if self._persp_axis_complete.get(k))
        if cand.get('single'):
            self.auto_analysis_quality=min(self.auto_analysis_quality,58.0)
        state='1方向のみ・2方向は判定保留' if cand.get('single') else f'{axes}方向'
        self.auto_analysis_note=f'候補 {chr(65+index)} / {state} / 人物抑制＋構造線ファミリー優先 / 採用線群 {len(self.auto_detected_lines)}本'
        self.auto_analysis_label.setText(f'自動解析：信頼度 {self.auto_analysis_quality:.0f}% / {self.auto_analysis_note}')
        for i,b in enumerate(getattr(self,'auto_candidate_buttons',[])):
            b.blockSignals(True); b.setChecked(i==index); b.blockSignals(False)
        self.active_perspective_axis=('vp3' if self._persp_axis_complete.get('vp3') else ('vp2' if self._persp_axis_complete.get('vp2') else 'vp1')); self.perspective_step=1
        self.update_perspective_panel_state(); self.update_perspective_labels(); self.save_perspective(); self.refresh()

    def auto_analyze_perspective(self):
        """V5.23: conservative direction clustering with reject/one-direction states."""
        if self.original is None:
            self.statusBar().showMessage('先に画像を開いてください。',4000); return
        if cv2 is None or np is None:
            self.statusBar().showMessage('自動解析には OpenCV / NumPy が必要です。requirements.txt から再ビルドしてください。',7000); return
        self.statusBar().showMessage('方向クラスタから建築パースを解析中…')
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            segments=self._detect_segments_cv(); self._last_auto_segments=segments
            self.auto_candidates=self._build_direction_cluster_candidates(segments,False)
            relaxed_used=False
            if not self.auto_candidates or (len(self.auto_candidates)==1 and self.auto_candidates[0].get('single')):
                relaxed=self._build_direction_cluster_candidates(segments,True)
                if len(relaxed)>len(self.auto_candidates):
                    self.auto_candidates=relaxed; relaxed_used=True
            # V5.22 intentionally does not fall back to the old free-intersection solver.
            # If direction families are weak, report uncertainty instead of fabricating a VP pair.
            for i,b in enumerate(getattr(self,'auto_candidate_buttons',[])):
                b.setEnabled(i<len(self.auto_candidates))
            if not self.auto_candidates:
                self.auto_detected_lines=[]; self.auto_analysis_quality=0.0; self.auto_analysis_note=f'候補なし / 検出線 {len(segments)}本'
                self.auto_analysis_label.setText(f'自動解析：判定不能 / 有効なパース方向を検出できません / 検出線 {len(segments)}本')
                self.statusBar().showMessage(f'自動解析：検出線 {len(segments)} 本。信頼できるパース方向が不足しています。手動入力を使用してください。',8000); self.refresh(); return
            if relaxed_used:
                self.statusBar().showMessage('方向クラスタが不足したため緩和条件も使用しました。',4500)
            self.apply_auto_candidate(0)
            # Make the method visible in the UI so tests can distinguish V5.22 behaviour.
            sig=self.auto_candidates[0].get('direction_signature')
            if sig:
                self.auto_analysis_note += ' / 方向 ' + '-'.join(str(int(x))+'°' for x in sig)
                self.auto_analysis_label.setText(f'自動解析：信頼度 {self.auto_analysis_quality:.0f}% / {self.auto_analysis_note}')
            self.statusBar().showMessage(f'保守的パース解析完了：{len(self.auto_candidates)}候補。弱い方向は無理に採用しません。',7000)
        except Exception as ex:
            self.statusBar().showMessage(f'自動解析でエラー: {ex}',9000)
        finally:
            QApplication.restoreOverrideCursor()

    def _camera_consistent_vp3_direction(self):
        """Return a screen-space Y/up direction consistent with solved VP1+VP2.
        Uses the image-plane horizon normal as the level-camera vertical direction.
        The sign is chosen to agree with the user's VP3 calibration strokes.
        """
        # VP1/VP2 are stored in normalized image coordinates. Convert to canvas pixels.
        p1=self.canvas._image_norm_to_point(*self.vp1)
        p2=self.canvas._image_norm_to_point(*self.vp2)
        hx=p2.x()-p1.x(); hy=p2.y()-p1.y()
        hl=math.hypot(hx,hy)
        if hl<1e-6:
            return (0.0,1.0)
        # Normal to the actual VP horizon. This preserves camera roll.
        ux=-hy/hl; uy=hx/hl

        # Choose the same axial orientation as the user's two VP3 strokes.
        vx=vy=0.0
        for ln in self.perspective_lines.get('vp3',[])[:2]:
            a=self.canvas._image_norm_to_point(*ln[0]); b=self.canvas._image_norm_to_point(*ln[1])
            dx=b.x()-a.x(); dy=b.y()-a.y(); ll=math.hypot(dx,dy)
            if ll>1e-6:
                dx/=ll; dy/=ll
                if vx*dx+vy*dy < 0:
                    dx=-dx; dy=-dy
                vx+=dx; vy+=dy
        if vx*ux+vy*uy < 0:
            ux=-ux; uy=-uy
        return (ux,uy)

    def _vp3_solver_state(self):
        """Classify Y/up axis as finite VP or infinity and derive camera-consistent direction."""
        lines=self.perspective_lines.get('vp3',[])
        result={'mode':'unset','angle_deg':None,'direction':None,'finite_vp':None,'residual_deg':None}
        if len(lines)<2:
            return result

        def unit(line):
            (x1,y1),(x2,y2)=line
            dx=x2-x1; dy=y2-y1
            n=max(1e-9,math.hypot(dx,dy))
            return dx/n,dy/n

        u1=unit(lines[0]); u2=unit(lines[1])
        dot=max(-1.0,min(1.0,abs(u1[0]*u2[0]+u1[1]*u2[1])))
        delta=math.degrees(math.acos(dot))
        result['angle_deg']=delta

        # Camera-consistent Y direction inferred from solved X/Z horizon.
        try:
            cu=self._camera_consistent_vp3_direction()
            cn=max(1e-9,math.hypot(cu[0],cu[1]))
            cu=(cu[0]/cn,cu[1]/cn)
        except Exception:
            cu=(0.0,1.0)
        result['direction']=cu

        # Nearly parallel calibration lines => VP at infinity.
        # Keep threshold conservative so clearly converging Y lines still create a finite VP.
        if delta < 2.25:
            result['mode']='infinity'
            # residual between user's average Y direction and camera-consistent Y.
            sx=u1[0]+u2[0]; sy=u1[1]+u2[1]
            if math.hypot(sx,sy)<1e-9:
                avg=u1
            else:
                n=math.hypot(sx,sy); avg=(sx/n,sy/n)
            d=max(-1.0,min(1.0,abs(avg[0]*cu[0]+avg[1]*cu[1])))
            result['residual_deg']=math.degrees(math.acos(d))
            return result

        # Otherwise finite VP is valid if already solved by the existing robust line intersection.
        result['mode']='finite'
        result['finite_vp']=self.vp3
        # For finite VP, compare local direction from image center to VP against camera-consistent Y.
        try:
            w=float(self.original.width); h=float(self.original.height)
            vx=self.vp3[0]*w-w*0.5; vy=self.vp3[1]*h-h*0.5
            n=math.hypot(vx,vy)
            if n>1e-9:
                vv=(vx/n,vy/n)
                d=max(-1.0,min(1.0,abs(vv[0]*cu[0]+vv[1]*cu[1])))
                result['residual_deg']=math.degrees(math.acos(d))
        except Exception:
            pass
        return result


    def solve_perspective_axis(self,name):
        lines=self.perspective_lines.get(name,[])
        if len(lines)<2:return False
        if name=='vp3':
            # V5.35: stable two-mode VP3.
            # Work in image-pixel space so widescreen normalization cannot distort angles.
            iw=float(self.original.width if self.original is not None else 1)
            ih=float(self.original.height if self.original is not None else 1)
            def _ang(line):
                dx=(line[1][0]-line[0][0])*iw
                dy=(line[1][1]-line[0][1])*ih
                return math.degrees(math.atan2(dy,dx)) % 180.0
            a1,a2=_ang(lines[0]),_ang(lines[1])
            da=abs(a1-a2); da=min(da,180.0-da)
            # Near-parallel vertical calibration means a level/near-level camera:
            # use a camera-consistent Y direction instead of copying either hand-drawn tilt.
            # Clear convergence still solves a finite VP3.
            self.vp3_at_infinity=(da < 2.0)
            if self.vp3_at_infinity:
                self.update_perspective_labels()
                if hasattr(self,'canvas'): self.canvas.update()
                return True
        ip=infinite_line_intersection(lines[0][0],lines[0][1],lines[1][0],lines[1][1])
        if ip is None:return False
        x=max(-6.0,min(7.0,ip[0])); y=max(-5.0,min(6.0,ip[1]))
        if name=='vp1': self.vp1=(x,y)
        elif name=='vp2': self.vp2=(x,y)
        else: self.vp3=(x,y)
        # Eye level is the horizon through VP1/VP2; store its y at image center for the label.
        if name in ('vp1','vp2'):
            x1,y1=self.vp1; x2,y2=self.vp2
            if abs(x2-x1)>1e-9:self.eye_level_y=y1+(0.5-x1)*(y2-y1)/(x2-x1)
            else:self.eye_level_y=(y1+y2)/2.0
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
        if 0<=self.current_index<len(self.paths):
            import copy
            self.perspective_by_image[str(self.paths[self.current_index])]={'vp1':tuple(self.vp1),'vp2':tuple(self.vp2),'vp3':tuple(self.vp3),'eye':float(self.eye_level_y),'lines':copy.deepcopy(self.perspective_lines),'complete':dict(self._persp_axis_complete),'auto_lines':copy.deepcopy(self.auto_detected_lines),'auto_quality':self.auto_analysis_quality,'auto_note':self.auto_analysis_note}
    def reset_perspective(self):
        self.vp1=(-0.30,0.50); self.vp2=(1.30,0.50); self.vp3=(0.50,-0.65); self.eye_level_y=0.50; self.view_zoom=1.0
        self.active_perspective_axis='vp1'; self.perspective_step=0
        self._persp_axis_complete={'vp1':False,'vp2':False,'vp3':False}; self._persp_anchor_touched={}; self.vp3_at_infinity=False; self.show_perspective_grid_default=True
        self.auto_detected_lines=[]; self.auto_analysis_quality=None; self.auto_analysis_note=''
        if hasattr(self,'auto_analysis_label'): self.auto_analysis_label.setText('自動解析：未実行')
        self.perspective_lines=self.default_perspective_lines()
        self.perspective_step=0; self.update_perspective_panel_state(); self.update_perspective_labels(); self.save_perspective(); self.refresh()
    def update_perspective_labels(self):
        dx=(self.vp2[0]-self.vp1[0])*(self.original.width if self.original is not None else 1)
        dy=(self.vp2[1]-self.vp1[1])*(self.original.height if self.original is not None else 1)
        roll=math.degrees(math.atan2(dy,dx)) if abs(dx)+abs(dy)>1e-9 else 0.0
        vp3txt=('VP3: ∞（垂直線ほぼ平行）' if getattr(self,'vp3_at_infinity',False) else f'VP3: ({self.vp3[0]:.2f}, {self.vp3[1]:.2f})'); txt=f'VP1: ({self.vp1[0]:.2f}, {self.vp1[1]:.2f})  /  VP2: ({self.vp2[0]:.2f}, {self.vp2[1]:.2f})\n{vp3txt}  /  EyeLevelY: {self.eye_level_y:.2f}  /  Roll: {roll:+.1f}°'
        if hasattr(self,'persp_label'):
            self.persp_label.setText(txt)
        if hasattr(self,'analysis_perspective'):
            self.analysis_perspective.setText(txt)
        self.update_lens_estimate()

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
        """Robust 35mm-equivalent estimate: VP1×VP2 is primary; VP3 validates rather than dominates."""
        if self.original is None:
            return None
        complete=self._persp_axis_complete
        pairs=[]
        pair_map={}
        for label,a,b,key in [
            ('VP1×VP2',self.vp1,self.vp2,'base'),
            ('VP1×VP3',self.vp1,self.vp3,'v13'),
            ('VP2×VP3',self.vp2,self.vp3,'v23')]:
            needed = (key=='base' and complete.get('vp1') and complete.get('vp2')) or (key=='v13' and complete.get('vp1') and complete.get('vp3')) or (key=='v23' and complete.get('vp2') and complete.get('vp3'))
            if needed:
                val=self._pair_focal_pixels(a,b); pairs.append((label,val)); pair_map[key]=val
        base=pair_map.get('base')
        if base is None or not math.isfinite(base):
            return None
        w=float(self.original.width); h=float(self.original.height)
        base35=36.0*base/w
        validations=[]
        for k in ('v13','v23'):
            v=pair_map.get(k)
            if v is not None and math.isfinite(v): validations.append(v)
        # VP3 is a consistency check. Only validation estimates reasonably close to the
        # horizontal solution influence the final focal length; severe outliers are reported,
        # not averaged into the answer.
        accepted=[v for v in validations if abs(v-base)/max(base,1e-6) <= 0.38]
        if accepted:
            vmed=sorted(accepted)[len(accepted)//2]
            fpx=0.72*base+0.28*vmed
        else:
            fpx=base
        eq35=36.0*fpx/w
        hfov=math.degrees(2.0*math.atan(w/(2.0*fpx)))
        vfov=math.degrees(2.0*math.atan(h/(2.0*fpx)))
        discrepancies=[abs(v-base)/max(base,1e-6) for v in validations]
        validation_spread=max(discrepancies) if discrepancies else None
        autoq=self.auto_analysis_quality if self.auto_analysis_quality is not None else None
        if validations:
            close=sum(1 for d in discrepancies if d<0.18)
            if close==len(validations) and max(discrepancies)<0.18:
                confidence='高'; margin=0.14; reason='VP1/VP2とVP3検証がよく一致'
            elif any(d<0.30 for d in discrepancies):
                confidence='中'; margin=0.22; reason='水平VPは安定、VP3検証に一部差'
            else:
                confidence='低'; margin=0.32; reason='VP3との整合が低いため水平VPを優先'
        else:
            confidence='中'; margin=0.24; reason='VP1/VP2による2点透視推定'
        if autoq is not None:
            if autoq<45:
                confidence='低'; margin=max(margin,0.34); reason+=' / 自動検出信頼度が低い'
            elif autoq>=78 and confidence=='中':
                reason+=' / 自動検出は比較的安定'
        lo=max(4.0,eq35*(1.0-margin)); hi=eq35*(1.0+margin)
        if eq35 < 20: kind='超広角'
        elif eq35 < 35: kind='広角'
        elif eq35 < 60: kind='標準'
        elif eq35 < 100: kind='中望遠'
        else: kind='望遠'
        common=[12,14,16,18,20,21,24,28,32,35,40,50,58,65,85,100,135,200]
        candidates=sorted(common,key=lambda mm:abs(math.log(max(mm,1)/max(eq35,1))))[:3]
        candidates=sorted(candidates)
        # Angular residual proxy for the orthogonal-camera solve.
        # For each available orthogonal VP pair, ideal dot((vi-c),(vj-c)) + f^2 == 0.
        cx=w*0.5; cy=h*0.5
        residual_angles=[]
        vp_pairs=[]
        if complete.get('vp1') and complete.get('vp2'): vp_pairs.append((self.vp1,self.vp2))
        if complete.get('vp1') and complete.get('vp3') and not getattr(self,'vp3_at_infinity',False): vp_pairs.append((self.vp1,self.vp3))
        if complete.get('vp2') and complete.get('vp3') and not getattr(self,'vp3_at_infinity',False): vp_pairs.append((self.vp2,self.vp3))
        for va,vb in vp_pairs:
            ax=va[0]*w-cx; ay=va[1]*h-cy
            bx=vb[0]*w-cx; by=vb[1]*h-cy
            dot=ax*bx+ay*by+fpx*fpx
            na=math.sqrt(ax*ax+ay*ay+fpx*fpx); nb=math.sqrt(bx*bx+by*by+fpx*fpx)
            if na>1e-6 and nb>1e-6:
                residual_angles.append(math.degrees(math.asin(min(1.0,abs(dot)/(na*nb)))))
        solve_error=(sum(x*x for x in residual_angles)/len(residual_angles))**0.5 if residual_angles else None
        try:
            ystate=self._vp3_solver_state()
            yres=ystate.get('residual_deg')
            if yres is not None:
                solve_error=((solve_error or 0.0)**2 + yres**2)**0.5
        except Exception:
            pass
        return {'eq35':eq35,'base35':base35,'lo':lo,'hi':hi,'hfov':hfov,'vfov':vfov,'kind':kind,'confidence':confidence,'pairs':pairs,'spread':validation_spread,'reason':reason,'candidates':candidates,'autoq':autoq,'solve_error':solve_error}

    def update_lens_estimate(self):
        targets=[x for x in (getattr(self,'analysis_lens',None),getattr(self,'persp_lens',None)) if x is not None]
        detail_targets=[x for x in (getattr(self,'lens_detail',None),getattr(self,'persp_lens_detail',None)) if x is not None]
        if not targets:
            return
        if not (self._persp_axis_complete.get('vp1') and self._persp_axis_complete.get('vp2')):
            for t in targets: t.setText('X と Z を確定するとカメラ推定を開始します。')
            for d in detail_targets: d.setText('X/Zを主解、Y（垂直）は有限VP/∞を自動判定して整合性検証します。')
            return
        est=self.estimate_lens()
        if est is None:
            for t in targets: t.setText('レンズ推定不可\nVP1/VP2 が直交方向として成立していない可能性があります。基準線を見直してください。')
            return
        cand=' / '.join(f'{x}mm' for x in est['candidates'])
        lens_text=(
            f"推定焦点距離： 約 {est['eq35']:.0f} mm（35mm換算）\n"
            f"有力レンジ： {est['lo']:.0f}–{est['hi']:.0f} mm\n"
            f"候補： {cand}\n"
            f"水平画角： 約 {est['hfov']:.1f}°  /  垂直画角： 約 {est['vfov']:.1f}°\n"
            f"レンズ傾向： {est['kind']}  /  信頼度： {est['confidence']}" + (f"\nSolve error： {est['solve_error']:.2f}°" if est.get('solve_error') is not None else '')
        )
        for t in targets: t.setText(lens_text)
        details=[]
        for label,val in est['pairs']:
            if val is not None:
                details.append(f"{label}: {36.0*val/max(float(self.original.width),1.0):.1f}mm相当")
        note=' / '.join(details)
        if est['spread'] is not None:
            note += f"\nVP3検証差: {est['spread']*100:.1f}%"
        else:
            note += '\nVP3を確定すると3点透視として追加検証できます。'
        if est.get('autoq') is not None:
            note += f"\n自動パース信頼度: {est['autoq']:.0f}%"
        note += f"\n判定理由: {est['reason']}"
        detail_text=note + '\n前提：主点=画面中心・正方画素・直交VP。クロップや誇張パースでは誤差が増えます。'
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


    def _v520_postprocess_auto_candidates(self, candidates, segments, frame_w, frame_h):
        """V5.23: prefer distinct structural direction families; suppress weak VP3; keep uncertain shots conservative."""
        try:
            families = self._v520_family_cluster(segments)
        except Exception:
            families = []
        if not candidates:
            return candidates
        # Penalize suspicious in-frame/local solutions when their supporting family is known.
        for c in candidates:
            try:
                fams = c.get("families") or []
                score = float(c.get("score", 0.0))
                for vp_key, fam_idx in (("vp1",0),("vp2",1)):
                    if fam_idx < len(fams):
                        score *= self._v520_penalize_inside_local_vp(c.get(vp_key), fams[fam_idx].get("segments", []), frame_w, frame_h)
                c["score"] = score
            except Exception:
                pass
        candidates.sort(key=lambda c: float(c.get("score", 0.0)), reverse=True)
        # Suppress VP3 when the dominant vertical family is effectively parallel.
        try:
            verticals = sorted(
                [f for f in families if abs(((f["angle"]-90.0+90.0)%180.0)-90.0) <= 10.0],
                key=lambda f: f["weight"], reverse=True
            )
            if verticals and self._v520_vertical_family_is_parallel(verticals[0], frame_h):
                for c in candidates:
                    c["vp3"] = None
                    c["vp3_complete"] = False
                    c["vp3_reason"] = "縦線はほぼ平行：VP3は無限遠として扱います"
        except Exception:
            pass
        return candidates


if __name__=='__main__':
    app=QApplication(sys.argv); app.setApplicationName('Movie Shot Analyzer'); w=MovieShotAnalyzer(); w.show(); sys.exit(app.exec())
