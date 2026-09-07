import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import math, csv, json

EXTS={".jpg",".jpeg",".png",".webp",".bmp"}

def intersect(l1,l2):
    (x1,y1,x2,y2),(x3,y3,x4,y4)=l1,l2
    den=(x1-x2)*(y3-y4)-(y1-y2)*(x3-x4)
    if abs(den)<1e-9:return None
    px=((x1*y2-y1*x2)*(x3-x4)-(x1-x2)*(x3*y4-y3*x4))/den
    py=((x1*y2-y1*x2)*(y3-y4)-(y1-y2)*(x3*y4-y3*x4))/den
    return px,py

def detect_lines(im):
    # Lightweight line detection using OpenCV if available.
    try:
        import cv2, numpy as np
        a=np.array(im.convert("RGB"))
        g=cv2.cvtColor(a,cv2.COLOR_RGB2GRAY)
        edges=cv2.Canny(g,50,150,apertureSize=3)
        lines=cv2.HoughLinesP(edges,1,np.pi/180,threshold=max(30,min(im.size)//10),
                              minLineLength=max(40,min(im.size)//8),maxLineGap=12)
        out=[]
        if lines is not None:
            for q in lines[:,0]:
                x1,y1,x2,y2=map(float,q)
                ang=math.degrees(math.atan2(y2-y1,x2-x1))
                length=math.hypot(x2-x1,y2-y1)
                if length>40 and (abs(ang)<80 or abs(abs(ang)-90)<80):
                    out.append((x1,y1,x2,y2,ang,length))
        return out
    except Exception:
        return []

def estimate(im):
    w,h=im.size
    lines=detect_lines(im)
    # Classify strong near-horizontal / near-vertical lines.
    hs=[x for x in lines if abs(x[4])<12 and x[5]>max(w,h)*.10]
    vs=[x for x in lines if abs(abs(x[4])-90)<12 and x[5]>max(w,h)*.10]
    vp_h=None; vp_v=None
    if len(hs)>=2:
        pts=[]
        for i in range(min(len(hs),30)):
            for j in range(i+1,min(len(hs),30)):
                p=intersect(hs[i][:4],hs[j][:4])
                if p: pts.append(p)
        if pts:
            # median intersection, robust-ish
            xs=sorted(p[0] for p in pts); ys=sorted(p[1] for p in pts)
            vp_h=(xs[len(xs)//2],ys[len(ys)//2])
    if len(vs)>=2:
        pts=[]
        for i in range(min(len(vs),30)):
            for j in range(i+1,min(len(vs),30)):
                p=intersect(vs[i][:4],vs[j][:4])
                if p: pts.append(p)
        if pts:
            xs=sorted(p[0] for p in pts); ys=sorted(p[1] for p in pts)
            vp_v=(xs[len(xs)//2],ys[len(ys)//2])
    # Heuristic classification. True VP estimation from arbitrary movie frames needs
    # selecting orthogonal line families; this keeps the UI honest rather than inventing precision.
    if vp_h and vp_v:
        perspective="2点候補"
        vps=[vp_h,vp_v]
    elif vp_h or vp_v:
        perspective="1点候補"
        vps=[vp_h or vp_v]
    else:
        perspective="判定不能"
        vps=[]
    # Horizontal/vertical line intersections can be used as a rough vanishing-line proxy.
    # Lens estimation is only meaningful when two orthogonal VPs are known and principal point
    # is near the image center. Approximate 35mm-equivalent horizontal FOV from VP geometry.
    focal="推定不能"
    fov="推定不能"
    if vp_h and vp_v:
        cx,cy=w/2,h/2
        # For a rectilinear camera, focal in pixel units can be estimated from orthogonal VPs.
        # f^2 = -(vx-cx)(ux-cx) -(vy-cy)(uy-cy)
        f2=-(vp_h[0]-cx)*(vp_v[0]-cx)-(vp_h[1]-cy)*(vp_v[1]-cy)
        if f2>0:
            f=math.sqrt(f2)
            hfov=2*math.degrees(math.atan((w/2)/f))
            # 35mm still-image convention, 36mm sensor width.
            mm=36/(2*math.tan(math.radians(hfov/2)))
            if 10<=mm<=300:
                focal=f"約 {mm:.0f}mm"
                fov=f"約 {hfov:.0f}°"
    # Rough eye level: if a horizontal vanishing point is within image bounds, use its y.
    eye="推定不能"
    if vp_h and -2*w<vp_h[0]<3*w and -2*h<vp_h[1]<3*h:
        eye=f"{max(0,min(100,vp_h[1]/h*100)):.0f}%"
    return {"perspective":perspective,"vps":vps,"focal":focal,"fov":fov,"eye":eye}

def draw_guides(im, selected, analysis):
    w,h=im.size
    o=Image.new("RGBA",(w,h),(0,0,0,0)); d=ImageDraw.Draw(o)
    col=(255,80,40,165); lw=max(2,round(min(w,h)/500))
    def L(p): d.line(p,fill=col,width=lw)
    if "thirds" in selected:
        for x in (w/3,2*w/3): L([(x,0),(x,h)])
        for y in (h/3,2*h/3): L([(0,y),(w,y)])
    if "golden" in selected:
        phi=(1+math.sqrt(5))/2
        for x in (w/phi,w-w/phi): L([(x,0),(x,h)])
        for y in (h/phi,h-h/phi): L([(0,y),(w,y)])
    if "center" in selected:
        L([(w/2,0),(w/2,h)]); L([(0,h/2),(w,h/2)])
    if "diagonal" in selected:
        L([(0,0),(w,h)]); L([(w,0),(0,h)])
    if "triangle" in selected:
        # Two main diagonals forming an apex at center-top and base corners.
        L([(0,h),(w/2,0),(w,h)])
    if "symmetry" in selected:
        L([(w/2,0),(w/2,h)])
    if "spiral" in selected:
        # Golden spiral approximation via quarter-arc construction.
        phi=(1+math.sqrt(5))/2
        # Draw a sequence of golden rectangles/arcs.
        box=[0,0,w,h]
        # Approximate using cubic polyline sampled from a logarithmic spiral.
        cx,cy=w/2,h/2
        a=min(w,h)*0.015
        b=math.log(phi)/(math.pi/2)
        pts=[]
        for i in range(900):
            t=i/899*math.pi*4
            r=a*math.exp(b*t)
            x=cx+r*math.cos(t); y=cy+r*math.sin(t)
            if -w*.1<x<w*1.1 and -h*.1<y<h*1.1: pts.append((x,y))
        if len(pts)>1: d.line(pts,fill=col,width=lw)
    if "perspective" in selected and analysis:
        for vp in analysis["vps"]:
            vx,vy=vp
            d.ellipse((vx-7,vy-7,vx+7,vy+7),fill=(255,80,40,220))
            for x,y in ((0,0),(w,0),(0,h),(w,h),(w/2,h/2)):
                L([(x,y),(vx,vy)])
        if analysis.get("eye")!="推定不能" and analysis["vps"]:
            y=float(analysis["eye"].replace("%",""))/100*h
            L([(0,y),(w,y)])
    return Image.alpha_composite(im.convert("RGBA"),o).convert("RGB")

class App:
    def __init__(self,root):
        self.root=root; root.title("Movie Shot Analyzer"); root.geometry("820x680")
        self.files=[]; self.folder=None
        frm=ttk.Frame(root,padding=16); frm.pack(fill="both",expand=True)
        ttk.Label(frm,text="Movie Shot Analyzer",font=("Segoe UI",18,"bold")).pack(anchor="w")
        ttk.Label(frm,text="映画キャプチャを一括で構図・パース分析し、ガイド付き画像を保存します。").pack(anchor="w",pady=(4,14))
        b=ttk.Frame(frm); b.pack(fill="x")
        ttk.Button(b,text="画像フォルダを選択",command=self.pick).pack(side="left")
        self.status=ttk.Label(b,text="未選択"); self.status.pack(side="left",padx=12)
        box=ttk.LabelFrame(frm,text="ガイド",padding=10); box.pack(fill="x",pady=12)
        self.vars={}
        names=[("thirds","三分割"),("golden","黄金比"),("spiral","黄金螺旋"),("triangle","三角構図"),("diagonal","対角線"),("center","中央構図"),("symmetry","対称構図"),("perspective","推定パース")]
        for i,(k,n) in enumerate(names):
            v=tk.BooleanVar(value=k in {"thirds","perspective"})
            self.vars[k]=v; ttk.Checkbutton(box,text=n,variable=v).grid(row=i//4,column=i%4,sticky="w",padx=8,pady=5)
        self.info=ttk.LabelFrame(frm,text="分析結果（最初の画像）",padding=10); self.info.pack(fill="x",pady=8)
        self.result=ttk.Label(self.info,text="画像を選択してください。",justify="left"); self.result.pack(anchor="w")
        ttk.Button(frm,text="一括分析・ガイド付き画像を作成",command=self.run).pack(fill="x",pady=12)
        self.log=tk.Text(frm,height=14,state="disabled"); self.log.pack(fill="both",expand=True)

    def pick(self):
        f=filedialog.askdirectory(title="映画キャプチャのフォルダを選択")
        if not f:return
        self.folder=Path(f); self.files=[p for p in self.folder.iterdir() if p.suffix.lower() in EXTS]
        self.status.config(text=f"{len(self.files)}枚")
        if self.files:
            try:
                a=estimate(Image.open(self.files[0]))
                self.result.config(text=f"透視: {a['perspective']}\nレンズ: {a['focal']}\n画角: {a['fov']}\nアイレベル: {a['eye']}")
            except Exception as e:self.result.config(text=f"解析できません: {e}")

    def run(self):
        if not self.files:
            messagebox.showwarning("画像なし","先に画像フォルダを選択してください。"); return
        selected={k for k,v in self.vars.items() if v.get()}
        out=self.folder/"analyzed"; out.mkdir(exist_ok=True)
        rows=[]
        self.log.config(state="normal"); self.log.delete("1.0","end")
        for p in self.files:
            try:
                im=Image.open(p).convert("RGB"); a=estimate(im)
                outname=out/(p.stem+"_guided.jpg")
                draw_guides(im,selected,a).save(outname,quality=95)
                rows.append([p.name,a["perspective"],a["focal"],a["fov"],a["eye"]])
                self.log.insert("end",f"OK  {p.name}  | {a['perspective']} | {a['focal']}\n")
                self.root.update()
            except Exception as e:
                self.log.insert("end",f"ERROR {p.name}: {e}\n")
        with open(out/"analysis.csv","w",newline="",encoding="utf-8-sig") as f:
            w=csv.writer(f); w.writerow(["file","perspective","focal_length_35mm_est","horizontal_fov","eye_level"]); w.writerows(rows)
        self.log.insert("end",f"\n完了: {len(rows)}枚\n保存先: {out}\n")
        self.log.config(state="disabled")
        messagebox.showinfo("完了",f"{len(rows)}枚を処理しました。\n{out}")

if __name__=="__main__":
    App(tk.Tk()).root.mainloop()
