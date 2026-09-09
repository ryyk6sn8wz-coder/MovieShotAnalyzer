from __future__ import annotations
import math, sys
from pathlib import Path
from PIL import Image, ImageEnhance
from PySide6.QtCore import QRectF, Qt, QPointF
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QMainWindow, QPushButton, QScrollArea, QSlider, QDoubleSpinBox,
    QVBoxLayout, QWidget, QTabWidget
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
            fill=QColor(self.owner.point_color); fill.setAlpha(round(255*self.owner.point_alpha.value()/100))
            p.setBrush(fill); p.setPen(Qt.PenStyle.NoPen)
            r=self.owner.point_size.val()/2
            for pt in pts[:120]: p.drawEllipse(QRectF(pt.x()-r,pt.y()-r,r*2,r*2))
        # Composition-guide edit handles. They appear only in edit mode and only for the selected guide.
        if self.owner.edit_comp_guides.isChecked() and self.owner.selected_comp_guide:
            self._draw_comp_handles(p,fr,self.owner.selected_comp_guide)
        # Free helper endpoints. Show only for selected free line or while dragging it.
        sel=self.owner.selected_helper
        if sel and sel[0]=='free' and 0<=sel[1]<len(self.owner.helper_free):
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
            if self.owner.manual_frame.isChecked() and not self.owner.lock_frame.isChecked():
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
    def _draw_perspective(self,p):
        """VanishPoint-style calibration: two 2-anchor lines solve each VP."""
        if self.pixmap is None:return
        vp_defs=(('VP1',self.owner.vp1,'#00d4ff','vp1'),('VP2',self.owner.vp2,'#ff4fa3','vp2'),('VP3',self.owner.vp3,'#7ee787','vp3'))
        # Horizon/eye level is solved from VP1 and VP2 rather than dragged independently.
        h1=self._image_norm_to_point(*self.owner.vp1); h2=self._image_norm_to_point(*self.owner.vp2)
        ec=QColor('#ffe66d'); ec.setAlpha(190); ep=QPen(ec); ep.setWidthF(1.4); ep.setStyle(Qt.PenStyle.DashLine); p.setPen(ep)
        hdx=h2.x()-h1.x(); hdy=h2.y()-h1.y(); hln=max(1e-6,math.hypot(hdx,hdy)); hext=10000.0/hln
        ha=QPointF(h1.x()-hdx*hext,h1.y()-hdy*hext); hb=QPointF(h1.x()+hdx*hext,h1.y()+hdy*hext); p.drawLine(ha,hb)
        eye_y=self.image_rect.top()+self.image_rect.height()*self.owner.eye_level_y
        # Two calibration segments per axis. Only the currently edited segment gets white anchors.
        for label,xy,color,key in vp_defs:
            for li,line in enumerate(self.owner.perspective_lines[key]):
                active=(key==self.owner.active_perspective_axis and li==self.owner.perspective_step)
                if key==self.owner.active_perspective_axis and self.owner.perspective_step==0 and li==1:
                    continue
                c=QColor(color); c.setAlpha(235 if active else 85); pen=QPen(c); pen.setWidthF(2.4 if active else 1.2); p.setPen(pen)
                a=self._image_norm_to_point(*line[0]); b=self._image_norm_to_point(*line[1]); p.drawLine(a,b)
                dx=b.x()-a.x(); dy=b.y()-a.y(); ln=math.hypot(dx,dy)
                if ln>1e-6:
                    ext=10000.0/ln; cc=QColor(color); cc.setAlpha(95 if active else 35); xp=QPen(cc); xp.setWidthF(1.2 if active else .8); xp.setStyle(Qt.PenStyle.DashLine); p.setPen(xp)
                    p.drawLine(QPointF(a.x()-dx*ext,a.y()-dy*ext),QPointF(a.x()+dx*ext,a.y()+dy*ext))
                if active and self.owner.show_perspective_handles.isChecked():
                    outline=QPen(QColor(color)); outline.setWidthF(2.0); p.setPen(outline); p.setBrush(QColor('#ffffff'))
                    for ptxy in line:
                        hp=self._image_norm_to_point(*ptxy); p.drawEllipse(QRectF(hp.x()-7,hp.y()-7,14,14))
        # solved VP markers
        for label,xy,color,key in vp_defs:
            vp=self._image_norm_to_point(*xy); c=QColor(color); c.setAlpha(245); p.setBrush(c); p.setPen(Qt.PenStyle.NoPen); p.drawEllipse(QRectF(vp.x()-7,vp.y()-7,14,14))
            p.setPen(QColor('#f5f7fa')); p.drawText(QRectF(vp.x()+10,vp.y()-12,58,24),Qt.AlignmentFlag.AlignVCenter,label)
        p.setPen(QColor('#ffe66d')); p.drawText(QRectF(8,eye_y-23,120,20),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'EYE LEVEL')

    def _perspective_hit(self,pos):
        if not self.owner.show_perspective.isChecked() or self.pixmap is None:return None
        if self.owner.show_perspective_handles.isChecked():
            name=self.owner.active_perspective_axis; li=self.owner.perspective_step
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
    def _update_cursor(self,pos):
        hit=self._hit(pos)
        if not hit:
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
        hit=self._hit(e.position()); self.drag_item=hit
        if not hit and self.owner.edit_comp_guides.isChecked():
            self.owner.selected_comp_guide=None; self.owner.reset_comp_btn.setEnabled(False); self.update()
        if hit:
            typ=hit[0]
            if typ in ('perspective_vp','perspective_anchor','eye_level'):
                self._drag_start=e.position()
                self._persp_drag_orig=(tuple(self.owner.vp1),tuple(self.owner.vp2),tuple(self.owner.vp3),float(self.owner.eye_level_y))
            if typ in ('comp_handle','comp_line'):
                self.owner.selected_comp_guide=hit[1]; self.owner.reset_comp_btn.setEnabled(True); self.owner.selected_helper=None; self.owner.update_helper_buttons(); self.update()
                if typ=='comp_line' or (typ=='comp_handle' and hit[1]=='tunnel' and hit[2]>=4):
                    import copy
                    self._drag_start=e.position(); self._comp_drag_orig=copy.deepcopy(self.owner.comp_guides[hit[1]])
            if typ in ('v','h','free_line','free_end'):
                kind='free' if typ.startswith('free') else typ
                self.owner.selected_helper=(kind,hit[1]); self.owner.update_helper_buttons(); self.update()
            if typ=='free_line':
                fr=self.frame_rect(); self._drag_start=e.position(); self._drag_orig=self.owner.helper_free[hit[1]]
            elif typ in ('frame_edge','frame_move'):
                self._drag_start=e.position(); self._frame_drag_orig=[tuple(q) for q in self.owner.frame_quad]
    def mouseMoveEvent(self,e):
        if not self.drag_item:
            self._update_cursor(e.position()); return
        fr=self.frame_rect(); typ=self.drag_item[0]; pos=e.position()
        if typ=='perspective_vp':
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
            lines=[[tuple(pt) for pt in line] for line in self.owner.perspective_lines[name]]; lines[li][ei]=(nx,ny); self.owner.perspective_lines[name]=lines
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
        if self.drag_item and self.drag_item[0] in ('perspective_vp','perspective_anchor','eye_level'):
            if self.drag_item[0]=='perspective_anchor':
                name,li,ei=self.drag_item[1],self.drag_item[2],self.drag_item[3]
                key=(name,li); touched=self.owner._persp_anchor_touched.setdefault(key,set()); touched.add(ei)
                if li==0 and touched=={0,1}:
                    self.owner.perspective_step=1; self.owner._persp_anchor_touched.pop((name,1),None); self.owner.update_perspective_panel_state()
            self.owner.save_perspective()
        self.drag_item=None

class MovieShotAnalyzer(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle('Movie Shot Analyzer V5.7 Tabbed Workspace'); self.resize(1500,920); self.setMinimumSize(1050,680); self.setAcceptDrops(True)
        self.paths=[]; self.current_index=-1; self.original=None; self.frame_quad=[(0.,0.),(1.,0.),(1.,1.),(0.,1.)]; self.frames={}
        self.perspective_by_image={}
        self.vp1=(-0.30,0.50); self.vp2=(1.30,0.50); self.vp3=(0.50,-0.65); self.eye_level_y=0.50; self.view_zoom=1.0
        self.active_perspective_axis='vp1'; self.perspective_step=0
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
        self._persp_anchor_touched={}; self._build_ui(); self._style(); self.statusBar().showMessage('V5.7 — タブUI / 基本ガイド交点○ / 追加構図編集 / 2点→自動で2本目')
    def section(self,lay,text):
        lab=QLabel(text); lab.setObjectName('section'); lay.addWidget(lab)
    def _build_ui(self):
        root=QWidget(); self.setCentralWidget(root); outer=QHBoxLayout(root); outer.setContentsMargins(8,8,8,8); outer.setSpacing(8)
        cw=QWidget(); cw.setObjectName('controlsWidget'); c=QVBoxLayout(cw); c.setContentsMargins(12,12,12,12); c.setSpacing(7)
        title=QLabel('Movie Shot Analyzer'); title.setObjectName('appTitle'); c.addWidget(title)
        sub=QLabel('V5.7 / 構図＋パースワークスペース'); sub.setObjectName('subtitle'); c.addWidget(sub)
        a=QPushButton('画像を開く'); a.clicked.connect(self.choose_images); b=QPushButton('フォルダを開く'); b.clicked.connect(self.choose_folder); c.addWidget(a); c.addWidget(b)
        self.file_label=QLabel('画像未選択'); self.file_label.setWordWrap(True); self.file_label.setObjectName('fileLabel'); c.addWidget(self.file_label)
        nav=QHBoxLayout(); self.prev_button=QPushButton('◀ 前'); self.next_button=QPushButton('次 ▶'); self.prev_button.clicked.connect(self.prev_image); self.next_button.clicked.connect(self.next_image); nav.addWidget(self.prev_button); nav.addWidget(self.next_button); c.addLayout(nav)

        self.section(c,'実映像フレーム')
        self.show_frame=QCheckBox('フレーム枠を表示'); self.show_frame.setChecked(True); self.manual_frame=QCheckBox('自由変形ハンドルを使う'); self.manual_frame.setChecked(True); self.show_frame.toggled.connect(self.refresh); c.addWidget(self.show_frame); c.addWidget(self.manual_frame)
        self.lock_frame=QCheckBox('緑フレームを固定（誤操作防止）'); self.lock_frame.setChecked(True); self.lock_frame.toggled.connect(self.refresh); c.addWidget(self.lock_frame)
        row=QHBoxLayout(); auto=QPushButton('黒帯を自動検出'); auto.clicked.connect(self.auto_frame); reset=QPushButton('画像全体に戻す'); reset.clicked.connect(self.reset_frame); row.addWidget(auto); row.addWidget(reset); c.addLayout(row)
        fg=QGridLayout(); fg.addWidget(QLabel('フレーム太さ'),0,0); self.frame_width=StepControl(0.5,8.0,2.0,0.5); self.frame_width.value.valueChanged.connect(self.refresh); fg.addWidget(self.frame_width,0,1)
        fg.addWidget(QLabel('フレーム透明度'),1,0); self.frame_alpha=QSlider(Qt.Orientation.Horizontal); self.frame_alpha.setRange(0,100); self.frame_alpha.setValue(100); self.frame_alpha.valueChanged.connect(self.refresh); fg.addWidget(self.frame_alpha,1,1)
        fcbtn=QPushButton('フレーム色'); fcbtn.clicked.connect(self.choose_frame_color); fg.addWidget(fcbtn,2,0,1,2); c.addLayout(fg)

        self.section(c,'表示補正（元画像は変更しません）')
        self.sliders={}
        for key,label,lo,hi,val in [('brightness','明るさ',50,150,100),('contrast','コントラスト',50,150,100),('gamma','ガンマ',50,200,100),('saturation','彩度',0,200,100)]:
            row=QHBoxLayout(); row.addWidget(QLabel(label)); sld=QSlider(Qt.Orientation.Horizontal); sld.setRange(lo,hi); sld.setValue(val); v=QLabel(str(val)); v.setFixedWidth(32); sld.valueChanged.connect(lambda n,k=key,vl=v:(vl.setText(str(n)),self.update_display())); row.addWidget(sld,1); row.addWidget(v); c.addLayout(row); self.sliders[key]=sld
        resetdisp=QPushButton('表示補正をリセット'); resetdisp.clicked.connect(self.reset_display); c.addWidget(resetdisp); c.addStretch(1)
        scroll=QScrollArea(); scroll.setObjectName('controlScroll'); scroll.setWidgetResizable(True); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); scroll.setWidget(cw); scroll.setMinimumWidth(300); scroll.setMaximumWidth(350); outer.addWidget(scroll,0)
        self.canvas=ImageCanvas(self); outer.addWidget(self.canvas,1)
        self._build_right_tabs(outer)
        self._update_nav(); self.update_perspective_labels(); self.update_perspective_panel_state()

    def _make_toggle_button(self,text,checked=False,tooltip=''):
        b=QPushButton(text); b.setCheckable(True); b.setChecked(checked)
        if tooltip: b.setToolTip(tooltip)
        return b

    def _build_right_tabs(self,outer):
        panel=QWidget(); panel.setObjectName('rightPanel'); panel.setMinimumWidth(320); panel.setMaximumWidth(390)
        r=QVBoxLayout(panel); r.setContentsMargins(8,8,8,8); r.setSpacing(6)
        self.right_tabs=QTabWidget(); self.right_tabs.setObjectName('rightTabs'); r.addWidget(self.right_tabs)
        self._build_perspective_tab(); self._build_composition_tab(); self._build_analysis_tab()
        outer.addWidget(panel,0)

    def _build_perspective_tab(self):
        tab=QWidget(); lay=QVBoxLayout(tab); lay.setContentsMargins(10,10,10,10); lay.setSpacing(8)
        self.show_perspective=QCheckBox('パースを表示'); self.show_perspective.setChecked(True); self.show_perspective.toggled.connect(self.refresh); lay.addWidget(self.show_perspective)
        self.show_perspective_handles=QCheckBox('操作中の白○を表示'); self.show_perspective_handles.setChecked(True); self.show_perspective_handles.toggled.connect(self.refresh); lay.addWidget(self.show_perspective_handles)
        self.section(lay,'消失点')
        axisrow=QHBoxLayout(); self.axis_buttons={}
        tips={
            'vp1':'VP1を設定。最初の基準線は白○2点をドラッグして合わせます。2点とも動かすと自動で2本目へ進みます。',
            'vp2':'VP2を設定。別方向の平行エッジ2本から消失点を求めます。',
            'vp3':'VP3を設定。主に垂直方向の収束を2本の線から求めます。'}
        for key,label in [('vp1','VP1'),('vp2','VP2'),('vp3','VP3')]:
            b=QPushButton(label); b.setCheckable(True); b.setToolTip(tips[key]); b.clicked.connect(lambda checked,k=key:self.set_perspective_axis(k)); axisrow.addWidget(b); self.axis_buttons[key]=b
        lay.addLayout(axisrow)
        self.persp_step_label=QLabel('1本目：白○2点を合わせる'); self.persp_step_label.setObjectName('fileLabel'); lay.addWidget(self.persp_step_label)
        self.persp_label=QLabel('VP1 / VP2 / VP3 / Horizon'); self.persp_label.setObjectName('note'); self.persp_label.setWordWrap(True); lay.addWidget(self.persp_label)
        row=QHBoxLayout(); resetaxis=QPushButton('選択VPをリセット'); resetaxis.clicked.connect(self.reset_active_perspective_axis); resetall=QPushButton('全てリセット'); resetall.clicked.connect(self.reset_perspective); row.addWidget(resetaxis); row.addWidget(resetall); lay.addLayout(row)
        self.section(lay,'表示')
        row=QHBoxLayout(); row.addWidget(QLabel('作業領域')); self.workspace_scale=QSlider(Qt.Orientation.Horizontal); self.workspace_scale.setRange(100,400); self.workspace_scale.setValue(100); self.workspace_scale.valueChanged.connect(self.refresh); row.addWidget(self.workspace_scale,1); self.workspace_label=QLabel('100%'); self.workspace_label.setFixedWidth(46); self.workspace_scale.valueChanged.connect(lambda v:self.workspace_label.setText(f'{v}%')); row.addWidget(self.workspace_label); lay.addLayout(row)
        row=QHBoxLayout(); row.addWidget(QLabel('ホイールズーム')); self.zoom_label=QLabel('100%'); row.addWidget(self.zoom_label); zreset=QPushButton('100%'); zreset.clicked.connect(self.reset_zoom); row.addWidget(zreset); lay.addLayout(row)
        lay.addStretch(1); self.right_tabs.addTab(tab,'パース')

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
        grid.addWidget(QLabel('線の透明度'),1,0); self.guide_alpha=QSlider(Qt.Orientation.Horizontal); self.guide_alpha.setRange(0,100); self.guide_alpha.setValue(90); self.guide_alpha.valueChanged.connect(self.refresh); grid.addWidget(self.guide_alpha,1,1); self.guide_alpha_label=QLabel('90%'); self.guide_alpha_label.setFixedWidth(38); self.guide_alpha.valueChanged.connect(lambda v:self.guide_alpha_label.setText(f'{v}%')); grid.addWidget(self.guide_alpha_label,1,2); c.addLayout(grid)
        self.show_points=QCheckBox('基本ガイドの交点○を表示'); self.show_points.setChecked(True); self.show_points.toggled.connect(self.refresh); c.addWidget(self.show_points)
        grid=QGridLayout(); grid.addWidget(QLabel('○サイズ'),0,0); self.point_size=StepControl(2.0,30.0,8.0,0.5); self.point_size.value.valueChanged.connect(self.refresh); grid.addWidget(self.point_size,0,1); c.addLayout(grid)
        row=QHBoxLayout(); pc=QPushButton('○の色'); pc.clicked.connect(self.choose_point_color); row.addWidget(pc); row.addWidget(QLabel('○透明度')); self.point_alpha=QSlider(Qt.Orientation.Horizontal); self.point_alpha.setRange(0,100); self.point_alpha.setValue(100); self.point_alpha.valueChanged.connect(self.refresh); row.addWidget(self.point_alpha,1); self.point_alpha_label=QLabel('100%'); self.point_alpha_label.setFixedWidth(42); self.point_alpha.valueChanged.connect(lambda v:self.point_alpha_label.setText(f'{v}%')); row.addWidget(self.point_alpha_label); c.addLayout(row)
        c.addStretch(1); sc.setWidget(body); outer.addWidget(sc); self.right_tabs.addTab(tab,'構図ガイド')

    def _build_analysis_tab(self):
        tab=QWidget(); lay=QVBoxLayout(tab); lay.setContentsMargins(10,10,10,10); lay.setSpacing(8)
        self.section(lay,'ショット分析')
        self.analysis_summary=QLabel('画像を読み込むと、ここに構図タイプ候補・パース情報・画角/レンズ推定などをまとめて表示する予定です。'); self.analysis_summary.setObjectName('fileLabel'); self.analysis_summary.setWordWrap(True); lay.addWidget(self.analysis_summary)
        self.section(lay,'構図タイプ候補')
        lbl=QLabel('Balance / Unbalanced、フレーム内フレーム、視線誘導など、固定ガイドだけでは判断できない項目を画像内容から判定する領域です。'); lbl.setObjectName('note'); lbl.setWordWrap(True); lay.addWidget(lbl)
        self.section(lay,'パース結果')
        self.analysis_perspective=QLabel('VP1 / VP2 / VP3 / Eye Level'); self.analysis_perspective.setObjectName('note'); self.analysis_perspective.setWordWrap(True); lay.addWidget(self.analysis_perspective)
        lay.addStretch(1); self.right_tabs.addTab(tab,'ショット分析')

    def set_perspective_axis(self,name):
        self.active_perspective_axis=name; self.perspective_step=0; self._persp_anchor_touched.pop((name,0),None); self._persp_anchor_touched.pop((name,1),None); self.update_perspective_panel_state(); self.refresh()
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
        lab=self.active_perspective_axis.upper(); n=self.perspective_step+1
        if hasattr(self,'persp_step_label'):
            self.persp_step_label.setText(f'{lab}  {n}本目：白○2点をエッジに合わせる')
    def reset_active_perspective_axis(self):
        defaults=self.default_perspective_lines(); name=self.active_perspective_axis
        import copy; self.perspective_lines[name]=copy.deepcopy(defaults[name]); self.solve_perspective_axis(name); self.perspective_step=0; self.update_perspective_panel_state(); self.save_perspective(); self.refresh()

    def _style(self):
        self.setStyleSheet('''QMainWindow,QWidget{background:#20242b;color:#e8edf3}#controlsWidget{background:#20242b}#controlScroll{border:1px solid #343a43;background:#20242b}#appTitle{font-size:20px;font-weight:700}#panelTitle{font-size:18px;font-weight:700}#rightPanel{background:#1b1f26;border:1px solid #343a43}QTabWidget::pane{border:1px solid #343a43;background:#1b1f26}QTabBar::tab{background:#272d36;border:1px solid #3e4652;padding:9px 12px;margin-right:2px}QTabBar::tab:selected{background:#2d6cdf;color:white}QPushButton:checked{background:#2d6cdf;border-color:#68a0ff;color:white}#subtitle,#note{color:#aeb7c4}#section{font-size:14px;font-weight:700;color:#d9e2ec;margin-top:8px;border-top:1px solid #3b424d;padding-top:8px}#fileLabel{background:#171a20;border:1px solid #343a43;border-radius:5px;padding:8px}QPushButton{background:#303641;border:1px solid #48505d;border-radius:5px;padding:7px}QPushButton:hover{background:#3a424f}QPushButton:disabled{color:#69717c;background:#272b32}QCheckBox{padding:3px 1px}QDoubleSpinBox{background:#171a20;border:1px solid #48505d;padding:4px}QSlider::groove:horizontal{height:4px;background:#3b424d}QSlider::handle:horizontal{width:14px;margin:-5px 0;background:#8ab4f8;border-radius:7px}QScrollBar:vertical{background:#20242b;width:12px}QScrollBar::handle:vertical{background:#4a5260;min-height:28px;border-radius:5px}QStatusBar{background:#171a20;color:#aeb7c4}''')
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
            self.frame_quad=[tuple(q) for q in self.frames.get(str(p),[(0.,0.),(1.,0.),(1.,1.),(0.,1.)])]
            pd=self.perspective_by_image.get(str(p),{'vp1':(-0.30,0.50),'vp2':(1.30,0.50),'vp3':(0.50,-0.65),'eye':0.50,'lines':self.default_perspective_lines()})
            self.vp1=tuple(pd.get('vp1',(-0.30,0.50))); self.vp2=tuple(pd.get('vp2',(1.30,0.50))); self.vp3=tuple(pd.get('vp3',(0.50,-0.65))); self.eye_level_y=float(pd.get('eye',0.50))
            import copy
            self.perspective_lines=copy.deepcopy(pd.get('lines',self.default_perspective_lines()))
            self.update_perspective_labels()
            self.update_display(); self.file_label.setText(f'{p.name}\n{self.current_index+1} / {len(self.paths)}\n{self.original.width} × {self.original.height} px'); self.statusBar().showMessage(str(p))
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
        l,t,r,b=detect_frame(self.original); self.frame_quad=[(l,t),(r,t),(r,b),(l,b)]; self.save_frame(); self.canvas.update(); self.statusBar().showMessage('黒帯フレームを自動検出しました。必要なら緑の四隅または辺をドラッグしてください。',5000)
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
    def solve_perspective_axis(self,name):
        lines=self.perspective_lines.get(name,[])
        if len(lines)<2:return
        ip=infinite_line_intersection(lines[0][0],lines[0][1],lines[1][0],lines[1][1])
        if ip is None:return
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
    def solve_all_perspective_axes(self):
        for n in ('vp1','vp2','vp3'): self.solve_perspective_axis(n)
    def reset_zoom(self):
        self.view_zoom=1.0; self.update_zoom_label(); self.refresh()
    def update_zoom_label(self):
        if hasattr(self,'zoom_label'): self.zoom_label.setText(f'{round(self.view_zoom*100):d}%')

    def save_perspective(self):
        if 0<=self.current_index<len(self.paths):
            import copy
            self.perspective_by_image[str(self.paths[self.current_index])]={'vp1':tuple(self.vp1),'vp2':tuple(self.vp2),'vp3':tuple(self.vp3),'eye':float(self.eye_level_y),'lines':copy.deepcopy(self.perspective_lines)}
    def reset_perspective(self):
        self.vp1=(-0.30,0.50); self.vp2=(1.30,0.50); self.vp3=(0.50,-0.65); self.eye_level_y=0.50; self.view_zoom=1.0
        self.active_perspective_axis='vp1'; self.perspective_step=0
        self.perspective_lines=self.default_perspective_lines()
        self.perspective_step=0; self.update_perspective_panel_state(); self.update_perspective_labels(); self.save_perspective(); self.refresh()
    def update_perspective_labels(self):
        if hasattr(self,'persp_label'):
            txt=f'VP1: ({self.vp1[0]:.2f}, {self.vp1[1]:.2f})  /  VP2: ({self.vp2[0]:.2f}, {self.vp2[1]:.2f})\nVP3: ({self.vp3[0]:.2f}, {self.vp3[1]:.2f})  /  Horizon@Center: {self.eye_level_y:.2f}'
            self.persp_label.setText(txt)
            if hasattr(self,'analysis_perspective'): self.analysis_perspective.setText(txt)

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
