
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, colorchooser
from pathlib import Path
from PIL import Image, ImageTk, ImageDraw
import math, csv, json, os

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None

EXTS = {".jpg",".jpeg",".png",".webp",".bmp",".tif",".tiff"}

def intersect(l1, l2):
    x1,y1,x2,y2 = l1
    x3,y3,x4,y4 = l2
    den=(x1-x2)*(y3-y4)-(y1-y2)*(x3-x4)
    if abs(den) < 1e-9:
        return None
    px=((x1*y2-y1*x2)*(x3-x4)-(x1-x2)*(x3*y4-y3*x4))/den
    py=((x1*y2-y1*x2)*(y3-y4)-(y1-y2)*(x3*y4-y3*x4))/den
    return px,py

def hex_rgba(h, a=180):
    h=h.lstrip("#")
    return tuple(int(h[i:i+2],16) for i in (0,2,4))+(a,)

def extend_line_to_rect(vp, direction, w, h):
    vx,vy=vp
    dx,dy=direction
    pts=[]
    eps=1e-9
    if abs(dx)>eps:
        for x in (0,w):
            t=(x-vx)/dx
            y=vy+t*dy
            if -1e6 <= y <= 1e6:
                pts.append((x,y))
    if abs(dy)>eps:
        for y in (0,h):
            t=(y-vy)/dy
            x=vx+t*dx
            if -1e6 <= x <= 1e6:
                pts.append((x,y))
    # unique and choose the two farthest useful points
    uniq=[]
    for p in pts:
        if all(math.hypot(p[0]-q[0],p[1]-q[1])>1e-6 for q in uniq):
            uniq.append(p)
    if len(uniq)>=2:
        best=max(((math.dist(a,b),a,b) for i,a in enumerate(uniq) for b in uniq[i+1:]), key=lambda z:z[0])
        return best[1],best[2]
    return None

def draw_overlay(im, cfg, perspective):
    w,h=im.size
    ov=Image.new("RGBA",(w,h),(0,0,0,0))
    d=ImageDraw.Draw(ov)
    def line(points,color,width=None):
        d.line(points,fill=hex_rgba(color,cfg["guide_alpha"] if color==cfg["guide_color"] else cfg["line_alpha"]),
               width=width or cfg["guide_width"])
    gc=cfg["guide_color"]; pc=cfg["perspective_color"]; ec=cfg["eye_color"]; vc=cfg["vp_color"]
    gw=cfg["guide_width"]; pw=cfg["perspective_width"]; ew=cfg["eye_width"]; vw=cfg["vp_width"]
    # Composition guides
    if cfg["guides"].get("thirds"):
        for x in (w/3,2*w/3): d.line([(x,0),(x,h)],fill=hex_rgba(gc,cfg["guide_alpha"]),width=gw)
        for y in (h/3,2*h/3): d.line([(0,y),(w,y)],fill=hex_rgba(gc,cfg["guide_alpha"]),width=gw)
    if cfg["guides"].get("golden"):
        phi=(1+math.sqrt(5))/2
        for x in (w/phi,w-w/phi): d.line([(x,0),(x,h)],fill=hex_rgba(gc,cfg["guide_alpha"]),width=gw)
        for y in (h/phi,h-h/phi): d.line([(0,y),(w,y)],fill=hex_rgba(gc,cfg["guide_alpha"]),width=gw)
    if cfg["guides"].get("center"):
        d.line([(w/2,0),(w/2,h)],fill=hex_rgba(gc,cfg["guide_alpha"]),width=gw)
        d.line([(0,h/2),(w,h/2)],fill=hex_rgba(gc,cfg["guide_alpha"]),width=gw)
    if cfg["guides"].get("diagonal"):
        d.line([(0,0),(w,h)],fill=hex_rgba(gc,cfg["guide_alpha"]),width=gw)
        d.line([(w,0),(0,h)],fill=hex_rgba(gc,cfg["guide_alpha"]),width=gw)
    if cfg["guides"].get("triangle"):
        d.line([(0,h),(w/2,0),(w,h)],fill=hex_rgba(gc,cfg["guide_alpha"]),width=gw)
    if cfg["guides"].get("symmetry"):
        d.line([(w/2,0),(w/2,h)],fill=hex_rgba(gc,cfg["guide_alpha"]),width=gw)
    if cfg["guides"].get("spiral"):
        phi=(1+math.sqrt(5))/2
        cx,cy=w/2,h/2
        a=min(w,h)*0.015
        b=math.log(phi)/(math.pi/2)
        pts=[]
        for i in range(1000):
            t=i/999*math.pi*4
            r=a*math.exp(b*t)
            x=cx+r*math.cos(t); y=cy+r*math.sin(t)
            if -w*.15<x<w*1.15 and -h*.15<y<h*1.15:
                pts.append((x,y))
        if len(pts)>1: d.line(pts,fill=hex_rgba(gc,cfg["guide_alpha"]),width=gw)

    # Perspective
    vps=perspective.get("vps",[])
    if cfg["show_perspective"]:
        for vp in vps:
            vx,vy=vp
            # Lines from VP to corners and center; visually useful perspective fan
            for p in ((0,0),(w,0),(0,h),(w,h),(w/2,h/2)):
                d.line([(vx,vy),p],fill=hex_rgba(pc,cfg["line_alpha"]),width=pw)
    if cfg["show_eye"] and vps:
        if len(vps)>=2:
            y=sum(v[1] for v in vps[:2])/2
        else:
            y=vps[0][1]
        d.line([(0,y),(w,y)],fill=hex_rgba(ec,cfg["eye_alpha"]),width=ew)
    if cfg["show_vp"]:
        r=cfg["vp_size"]
        for vx,vy in vps:
            # filled circle with optional contrasting outline
            d.ellipse((vx-r,vy-r,vx+r,vy+r),fill=hex_rgba(vc,cfg["vp_alpha"]),
                      outline=hex_rgba(vc,255),width=vw)
    return Image.alpha_composite(im.convert("RGBA"),ov).convert("RGB")

def estimate_from_vps(im, vps):
    w,h=im.size
    if not vps:
        return {"perspective":"未指定","focal":"推定不能","fov":"推定不能","eye":"推定不能"}
    if len(vps)>=3:
        ptype="3点透視"
    elif len(vps)>=2:
        ptype="2点透視"
    else:
        ptype="1点透視"
    eye_y = (sum(v[1] for v in vps[:2])/2) if len(vps)>=2 else vps[0][1]
    eye = f"{eye_y/h*100:.1f}% ({eye_y:.0f}px)"
    focal="推定不能"; fov="推定不能"
    if len(vps)>=2:
        # Orthogonal VP formula, assuming centered principal point and rectilinear projection.
        cx,cy=w/2,h/2
        f2=-(vps[0][0]-cx)*(vps[1][0]-cx)-(vps[0][1]-cy)*(vps[1][1]-cy)
        if f2>0:
            f=math.sqrt(f2)
            hfov=2*math.degrees(math.atan((w/2)/f))
            mm=36/(2*math.tan(math.radians(hfov/2)))
            if 10<=mm<=300 and 5<=hfov<=170:
                focal=f"約 {mm:.0f}mm相当"
                fov=f"約 {hfov:.0f}°"
    return {"perspective":ptype,"focal":focal,"fov":fov,"eye":eye}

class App:
    def __init__(self, root):
        self.root=root
        root.title("Movie Shot Analyzer V2")
        root.geometry("1250x820")
        root.minsize(1050,700)
        self.files=[]
        self.current_index=0
        self.current_image=None
        self.tkimg=None
        self.display_scale=1
        self.offset=(0,0)
        self.vp_lines=[[],[],[]]  # each VP: list of two image-coordinate line segments
        self.vps=[]
        self.active_family=0
        self.pending_points=[]
        self.guide_vars={}
        self.build_ui()
        self.setup_dnd()

    def build_ui(self):
        main=ttk.Frame(self.root,padding=10); main.pack(fill="both",expand=True)
        left=ttk.Frame(main,width=270); left.pack(side="left",fill="y",padx=(0,8))
        center=ttk.Frame(main); center.pack(side="left",fill="both",expand=True)
        right=ttk.Frame(main,width=280); right.pack(side="right",fill="y",padx=(8,0))

        ttk.Label(left,text="Movie Shot Analyzer V2",font=("Segoe UI",17,"bold")).pack(anchor="w")
        ttk.Label(left,text="画像・フォルダをドラッグ＆ドロップ").pack(anchor="w",pady=(2,8))
        self.drop_label=tk.Label(left,text="ここへドロップ\nまたは下のボタン",relief="ridge",height=4)
        self.drop_label.pack(fill="x",pady=4)
        ttk.Button(left,text="ファイル／フォルダを選択",command=self.pick).pack(fill="x")
        self.status=ttk.Label(left,text="0枚")
        self.status.pack(anchor="w",pady=4)

        gbox=ttk.LabelFrame(left,text="ガイド表示",padding=8); gbox.pack(fill="x",pady=6)
        names=[("thirds","三分割"),("golden","黄金比"),("spiral","黄金螺旋"),("triangle","三角構図"),
               ("diagonal","対角線"),("center","十字・中央"),("symmetry","対称構図")]
        for i,(k,n) in enumerate(names):
            v=tk.BooleanVar(value=k=="thirds"); self.guide_vars[k]=v
            ttk.Checkbutton(gbox,text=n,variable=v).grid(row=i//2,column=i%2,sticky="w",padx=2,pady=2)

        pbox=ttk.LabelFrame(left,text="パース",padding=8); pbox.pack(fill="x",pady=6)
        self.show_p=tk.BooleanVar(value=True); self.show_eye=tk.BooleanVar(value=True); self.show_vp=tk.BooleanVar(value=True)
        ttk.Checkbutton(pbox,text="パースライン",variable=self.show_p,command=self.refresh).pack(anchor="w")
        ttk.Checkbutton(pbox,text="アイレベル",variable=self.show_eye,command=self.refresh).pack(anchor="w")
        ttk.Checkbutton(pbox,text="消失点○",variable=self.show_vp,command=self.refresh).pack(anchor="w")
        ttk.Button(pbox,text="VP1を指定（2本の線）",command=lambda:self.start_vp(0)).pack(fill="x",pady=2)
        ttk.Button(pbox,text="VP2を指定（2本の線）",command=lambda:self.start_vp(1)).pack(fill="x",pady=2)
        ttk.Button(pbox,text="VP3を指定（2本の線）",command=lambda:self.start_vp(2)).pack(fill="x",pady=2)
        ttk.Button(pbox,text="指定をクリア",command=self.clear_vps).pack(fill="x",pady=2)
        self.vp_status=ttk.Label(pbox,text="VP未指定",wraplength=240)
        self.vp_status.pack(anchor="w",pady=3)

        ttk.Button(left,text="一括解析・保存",command=self.batch).pack(fill="x",pady=(10,3))
        ttk.Button(left,text="現在の画像を保存",command=self.save_current).pack(fill="x")
        self.progress=ttk.Progressbar(left,mode="determinate"); self.progress.pack(fill="x",pady=8)

        self.canvas=tk.Canvas(center,background="#151515",highlightthickness=0)
        self.canvas.pack(fill="both",expand=True)
        self.canvas.bind("<Button-1>",self.canvas_click)
        nav=ttk.Frame(center); nav.pack(fill="x",pady=4)
        ttk.Button(nav,text="◀",command=self.prev).pack(side="left")
        self.counter=ttk.Label(nav,text="0 / 0"); self.counter.pack(side="left",padx=10)
        ttk.Button(nav,text="▶",command=self.next).pack(side="left")
        ttk.Button(nav,text="全体表示",command=self.show_current).pack(side="right")

        rbox=ttk.LabelFrame(right,text="色・太さ・透明度",padding=8); rbox.pack(fill="x")
        self.cfg={"guide_color":"#35c96f","perspective_color":"#3485ff","eye_color":"#ff3d78","vp_color":"#ffd23f",
                  "guide_width":2,"perspective_width":2,"eye_width":3,"vp_width":2,
                  "guide_alpha":165,"line_alpha":120,"eye_alpha":210,"vp_alpha":255,"vp_size":9,
                  "show_perspective":True,"show_eye":True,"show_vp":True,
                  "guides":{k:False for k,_ in names}}
        for k,n in [("guide_color","ガイドの色"),("perspective_color","パースの色"),("eye_color","アイレベルの色"),("vp_color","消失点○の色")]:
            row=ttk.Frame(rbox); row.pack(fill="x",pady=2)
            ttk.Label(row,text=n,width=16).pack(side="left")
            b=tk.Button(row,width=3,text=" ",bg=self.cfg[k],command=lambda kk=k:self.choose_color(kk))
            b.pack(side="left"); setattr(self,k+"_button",b)
        self.add_scale(rbox,"ガイド太さ","guide_width",1,10)
        self.add_scale(rbox,"パース太さ","perspective_width",1,10)
        self.add_scale(rbox,"アイレベル太さ","eye_width",1,10)
        self.add_scale(rbox,"消失点○サイズ","vp_size",3,30)
        self.add_scale(rbox,"消失点○線太さ","vp_width",1,10)
        self.add_scale(rbox,"ガイド透明度","guide_alpha",20,255)
        self.add_scale(rbox,"パース透明度","line_alpha",20,255)
        self.add_scale(rbox,"アイレベル透明度","eye_alpha",20,255)
        self.add_scale(rbox,"消失点○透明度","vp_alpha",20,255)

        abox=ttk.LabelFrame(right,text="分析結果",padding=8); abox.pack(fill="x",pady=8)
        self.result=ttk.Label(abox,text="画像を読み込んでください",justify="left",wraplength=250)
        self.result.pack(anchor="w")
        ttk.Label(right,text="手動パース指定：ボタンを押した後、画像上で\n1本目の線の始点→終点→2本目の始点→終点\nの順に4回クリックしてください。",wraplength=270).pack(anchor="w")

    def add_scale(self,parent,label,key,lo,hi):
        row=ttk.Frame(parent); row.pack(fill="x",pady=1)
        ttk.Label(row,text=label,width=16).pack(side="left")
        var=tk.IntVar(value=self.cfg[key])
        sc=ttk.Scale(row,from_=lo,to=hi,variable=var,command=lambda x,k=key,v=var:self.set_scale(k,v.get()))
        sc.pack(side="left",fill="x",expand=True)
        lab=ttk.Label(row,text=str(self.cfg[key]),width=4); lab.pack(side="right")
        setattr(self,key+"_label",lab)
    def set_scale(self,key,value):
        value=int(round(float(value))); self.cfg[key]=value
        if hasattr(self,key+"_label"): getattr(self,key+"_label").config(text=str(value))
        self.refresh()

    def choose_color(self,key):
        c=colorchooser.askcolor(color=self.cfg[key],title="色を選択")
        if c and c[1]:
            self.cfg[key]=c[1]
            getattr(self,key+"_button").config(bg=c[1])
            self.refresh()

    def setup_dnd(self):
        if DND_FILES and hasattr(self.root,"drop_target_register"):
            for widget in (self.root,self.drop_label,self.canvas):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>",self.on_drop)
    def on_drop(self,event):
        paths=self.root.tk.splitlist(event.data)
        self.add_paths(paths)

    def add_paths(self,paths):
        found=[]
        for raw in paths:
            p=Path(raw)
            if p.is_dir():
                found += [x for x in sorted(p.rglob("*")) if x.is_file() and x.suffix.lower() in EXTS]
            elif p.is_file() and p.suffix.lower() in EXTS:
                found.append(p)
        seen=set(self.files)
        for p in found:
            if p not in seen:
                self.files.append(p); seen.add(p)
        self.files.sort()
        self.current_index=0
        self.status.config(text=f"{len(self.files)}枚")
        self.show_current()

    def pick(self):
        p=filedialog.askdirectory(title="画像フォルダを選択")
        if p: self.add_paths([p])

    def show_current(self):
        if not self.files:
            self.canvas.delete("all"); self.counter.config(text="0 / 0"); return
        p=self.files[self.current_index]
        try:
            self.current_image=Image.open(p).convert("RGB")
            self.fit_image()
            self.counter.config(text=f"{self.current_index+1} / {len(self.files)}")
            self.update_result()
        except Exception as e:
            messagebox.showerror("読み込みエラー",str(e))

    def fit_image(self):
        self.canvas.delete("all")
        cw=max(200,self.canvas.winfo_width()); ch=max(200,self.canvas.winfo_height())
        w,h=self.current_image.size
        self.display_scale=min(cw/w,ch/h)
        dw,dh=max(1,int(w*self.display_scale)),max(1,int(h*self.display_scale))
        x=(cw-dw)//2; y=(ch-dh)//2
        self.offset=(x,y)
        disp=self.current_image.resize((dw,dh),Image.LANCZOS)
        self.tkimg=ImageTk.PhotoImage(disp)
        self.canvas.create_image(x,y,image=self.tkimg,anchor="nw",tags="img")
        self.draw_preview_overlay()

    def image_xy(self,event):
        if not self.current_image:return None
        x=(event.x-self.offset[0])/self.display_scale
        y=(event.y-self.offset[1])/self.display_scale
        w,h=self.current_image.size
        if 0<=x<=w and 0<=y<=h:return x,y
        return None

    def canvas_click(self,event):
        pt=self.image_xy(event)
        if pt is None or not self.pending_points:return
        self.pending_points.append(pt)
        if len(self.pending_points)==4:
            fam=self.active_family
            l1=tuple(self.pending_points[:2]); l2=tuple(self.pending_points[2:])
            self.vp_lines[fam]=[l1,l2]
            vp=intersect(l1,l2)
            if vp:
                if len(self.vps)>fam:self.vps[fam]=vp
                else:
                    while len(self.vps)<fam:self.vps.append(None)
                    self.vps.append(vp)
            else:
                while len(self.vps)<=fam:self.vps.append(None)
                self.vps[fam]=None
            self.pending_points=[]
            self.vp_status.config(text=self.vp_text())
            self.update_result()
            self.draw_preview_overlay()
        else:
            self.draw_click_markers()

    def start_vp(self,fam):
        self.active_family=fam
        self.pending_points=[]
        self.vp_status.config(text=f"VP{fam+1}: 4点クリックしてください")
    def clear_vps(self):
        self.vp_lines=[[],[],[]]; self.vps=[]; self.pending_points=[]
        self.vp_status.config(text="VP未指定"); self.update_result(); self.draw_preview_overlay()
    def vp_text(self):
        parts=[]
        for i,v in enumerate(self.vps):
            if v: parts.append(f"VP{i+1}: ({v[0]:.0f}, {v[1]:.0f})")
        return " / ".join(parts) if parts else "VP未指定"

    def draw_click_markers(self):
        self.draw_preview_overlay()
        for i,(x,y) in enumerate(self.pending_points):
            sx=self.offset[0]+x*self.display_scale; sy=self.offset[1]+y*self.display_scale
            self.canvas.create_oval(sx-5,sy-5,sx+5,sy+5,fill="#ffffff",outline="#ff3d78",width=2,tags="click")
    def draw_preview_overlay(self):
        if self.current_image is None:return
        # Use a low-cost image overlay then display.
        cfg=dict(self.cfg); cfg["guides"]={k:v.get() for k,v in self.guide_vars.items()}
        cfg["show_p"]=self.show_p.get(); cfg["show_eye"]=self.show_eye.get(); cfg["show_vp"]=self.show_vp.get()
        cfg["show_perspective"]=cfg["show_p"]; cfg["show_eye"]=cfg["show_eye"]; cfg["show_vp"]=cfg["show_vp"]
        ana={"vps":[v for v in self.vps if v]}
        over=draw_overlay(self.current_image,cfg,ana)
        cw=max(200,self.canvas.winfo_width()); ch=max(200,self.canvas.winfo_height())
        w,h=over.size; sc=min(cw/w,ch/h)
        dw,dh=max(1,int(w*sc)),max(1,int(h*sc)); x=(cw-dw)//2;y=(ch-dh)//2
        self.display_scale=sc; self.offset=(x,y)
        self.tkimg=ImageTk.PhotoImage(over.resize((dw,dh),Image.LANCZOS))
        self.canvas.delete("all"); self.canvas.create_image(x,y,image=self.tkimg,anchor="nw",tags="img")
        for i,(x0,y0,x1,y1) in enumerate(sum(self.vp_lines,[])):
            self.canvas.create_line(x+(x0)*sc,y+(y0)*sc,x+(x1)*sc,y+(y1)*sc,fill="#ffffff",width=1,tags="click")
        for x0,y0 in self.pending_points:
            self.canvas.create_oval(x+x0*sc-5,y+y0*sc-5,x+x0*sc+5,y+y0*sc+5,fill="#ffffff",outline="#ff3d78",width=2,tags="click")

    def refresh(self):
        self.draw_preview_overlay()
        self.update_result()
    def update_result(self):
        if self.current_image is None:
            self.result.config(text="画像を読み込んでください"); return
        a=estimate_from_vps(self.current_image,self.vps)
        self.result.config(text=f"透視タイプ：{a['perspective']}\nアイレベル：{a['eye']}\n消失点：{self.vp_text()}\n推定焦点距離：{a['focal']}\n推定水平画角：{a['fov']}")

    def prev(self):
        if self.files:
            self.current_index=(self.current_index-1)%len(self.files)
            self.vp_lines=[[],[],[]]; self.vps=[]; self.pending_points=[]
            self.show_current()
    def next(self):
        if self.files:
            self.current_index=(self.current_index+1)%len(self.files)
            self.vp_lines=[[],[],[]]; self.vps=[]; self.pending_points=[]
            self.show_current()

    def cfg_for_save(self):
        c=dict(self.cfg); c["guides"]={k:v.get() for k,v in self.guide_vars.items()}
        c["show_perspective"]=self.show_p.get(); c["show_eye"]=self.show_eye.get(); c["show_vp"]=self.show_vp.get()
        return c

    def save_current(self):
        if self.current_image is None:return
        p=self.files[self.current_index]
        out=p.parent/"analyzed"; out.mkdir(exist_ok=True)
        cfg=self.cfg_for_save()
        over=draw_overlay(self.current_image,cfg,{"vps":[v for v in self.vps if v]})
        outp=out/(p.stem+"_guided.jpg"); over.save(outp,quality=95)
        messagebox.showinfo("保存完了",str(outp))

    def batch(self):
        if not self.files:
            messagebox.showwarning("画像なし","画像またはフォルダをドロップしてください。"); return
        cfg=self.cfg_for_save()
        out=self.files[0].parent/"analyzed"
        out.mkdir(exist_ok=True)
        # Batch uses the currently specified VP geometry for all images only when VP coordinates
        # are present. Composition guides are always applied independently per image.
        self.progress["maximum"]=len(self.files); self.progress["value"]=0
        rows=[]
        for i,p in enumerate(self.files,1):
            try:
                im=Image.open(p).convert("RGB")
                vps=[v for v in self.vps if v]
                over=draw_overlay(im,cfg,{"vps":vps})
                over.save(out/(p.stem+"_guided.jpg"),quality=95)
                a=estimate_from_vps(im,vps)
                rows.append([str(p),a["perspective"],a["focal"],a["fov"],a["eye"]])
            except Exception as e:
                rows.append([str(p),"ERROR",str(e),"",""])
            self.progress["value"]=i; self.root.update_idletasks()
        with open(out/"analysis.csv","w",newline="",encoding="utf-8-sig") as f:
            cw=csv.writer(f); cw.writerow(["file","perspective","focal_35mm_est","horizontal_fov","eye_level"]); cw.writerows(rows)
        messagebox.showinfo("完了",f"{len(self.files)}枚を処理しました。\n保存先：{out}")

if __name__=="__main__":
    if TkinterDnD:
        root=TkinterDnD.Tk()
    else:
        root=tk.Tk()
    App(root)
    root.mainloop()
