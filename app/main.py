import io,os,sqlite3,time,tempfile,subprocess,zipfile,hmac,hashlib
from pathlib import Path
import bcrypt
import requests
from fastapi import FastAPI,UploadFile,File,Form,Request,HTTPException
from fastapi.responses import StreamingResponse,HTMLResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer
from PIL import Image,ImageEnhance
from pypdf import PdfReader,PdfWriter
from pdf2image import convert_from_bytes

APP=FastAPI(title="FileForge",version="6.0")
DATA=Path(os.getenv("DATABASE","/data/fileforge.db"));DATA.parent.mkdir(parents=True,exist_ok=True)
SECRET=os.getenv("SECRET_KEY","dev-secret");SER=URLSafeTimedSerializer(SECRET)
ANON=int(os.getenv("ANON_DAILY_LIMIT","5"));USER=int(os.getenv("USER_DAILY_LIMIT","20"));PREM=int(os.getenv("PREMIUM_DAILY_LIMIT","200"));MAX=int(os.getenv("MAX_UPLOAD_MB","50"));PRICE=int(os.getenv("PREMIUM_PRICE_RUB","299"))
APP.mount("/static",StaticFiles(directory="app/static"),name="static")

def db():
 c=sqlite3.connect(DATA);c.row_factory=sqlite3.Row;return c
with db() as c:
 c.execute("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE,password_hash TEXT,premium INTEGER DEFAULT 0,premium_until INTEGER,created_at INTEGER)""")
 c.execute("""CREATE TABLE IF NOT EXISTS usage(subject TEXT,day TEXT,count INTEGER,PRIMARY KEY(subject,day))""")
 c.execute("""CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,user_id INTEGER,amount INTEGER,status TEXT,provider_id TEXT,created_at INTEGER,paid_at INTEGER)""")

def user(req):
 t=req.cookies.get("ff_session")
 if not t:return None
 try:
  d=SER.loads(t,max_age=2592000)
  with db() as c:return c.execute("SELECT * FROM users WHERE id=?",(int(d["uid"]),)).fetchone()
 except:return None

def limit(req):
 u=user(req); sub=f"user:{u['id']}" if u else f"ip:{req.client.host if req.client else 'unknown'}"
 lim=PREM if u and u["premium"] and (not u["premium_until"] or u["premium_until"]>time.time()) else USER if u else ANON
 day=time.strftime("%Y-%m-%d",time.gmtime())
 with db() as c:
  r=c.execute("SELECT count FROM usage WHERE subject=? AND day=?",(sub,day)).fetchone();n=r["count"] if r else 0
  if n>=lim:raise HTTPException(429,f"Лимит {lim} операций/день исчерпан")
  if r:c.execute("UPDATE usage SET count=count+1 WHERE subject=? AND day=?",(sub,day))
  else:c.execute("INSERT INTO usage VALUES(?,?,1)",(sub,day))

def check(b):
 if len(b)>MAX*1024*1024:raise HTTPException(413,f"Максимум {MAX} МБ")
def out(b,name,mime):
 return StreamingResponse(io.BytesIO(b),media_type=mime,headers={"Content-Disposition":f'attachment; filename="{name}"'})
def im(b):
 try:return Image.open(io.BytesIO(b))
 except:raise HTTPException(400,"Некорректное изображение")

def hash_password(password):
 raw=password.encode("utf-8")
 if len(raw)>72:raise HTTPException(400,"Пароль не должен быть длиннее 72 байт")
 return bcrypt.hashpw(raw,bcrypt.gensalt()).decode("utf-8")

def verify_password(password,password_hash):
 raw=password.encode("utf-8")
 if len(raw)>72:return False
 try:return bcrypt.checkpw(raw,password_hash.encode("utf-8"))
 except (ValueError,TypeError):return False

@APP.get("/",response_class=HTMLResponse)
def home():return Path("app/static/index.html").read_text(encoding="utf8")
@APP.get("/robots.txt")
def robots():return "User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n"
@APP.get("/sitemap.xml",response_class=HTMLResponse)
def sitemap():return '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>/</loc></url><url><loc>/privacy</loc></url><url><loc>/terms</loc></url></urlset>'
@APP.get("/privacy",response_class=HTMLResponse)
def privacy():return Path("app/static/privacy.html").read_text(encoding="utf8")
@APP.get("/terms",response_class=HTMLResponse)
def terms():return Path("app/static/terms.html").read_text(encoding="utf8")
@APP.get("/health")
def health():return {"status":"ok","version":"6.0"}
@APP.get("/api/me")
def me(req:Request):
 u=user(req)
 if not u:return {"authenticated":False,"premium":False,"limit":ANON}
 p=bool(u["premium"] and (not u["premium_until"] or u["premium_until"]>time.time()))
 return {"authenticated":True,"email":u["email"],"premium":p,"premium_until":u["premium_until"],"limit":PREM if p else USER}
@APP.post("/api/auth/register")
def register(req:Request,email:str=Form(...),password:str=Form(...)):
 email=email.strip().lower()
 if "@" not in email or len(password)<8:raise HTTPException(400,"Нужен email и пароль от 8 символов")
 try:
  with db() as c:
   x=c.execute("INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)",(email,hash_password(password),int(time.time())));uid=x.lastrowid
 except sqlite3.IntegrityError:raise HTTPException(409,"Пользователь уже существует")
 r=JSONResponse({"ok":True});r.set_cookie("ff_session",SER.dumps({"uid":uid}),httponly=True,samesite="lax",secure=os.getenv("COOKIE_SECURE","false").lower()=="true",max_age=2592000);return r
@APP.post("/api/auth/login")
def login(email:str=Form(...),password:str=Form(...)):
 with db() as c:u=c.execute("SELECT * FROM users WHERE email=?",(email.strip().lower(),)).fetchone()
 if not u or not verify_password(password,u["password_hash"]):raise HTTPException(401,"Неверные данные")
 r=JSONResponse({"ok":True});r.set_cookie("ff_session",SER.dumps({"uid":u["id"]}),httponly=True,samesite="lax",secure=os.getenv("COOKIE_SECURE","false").lower()=="true",max_age=2592000);return r
@APP.post("/api/auth/logout")
def logout():
 r=JSONResponse({"ok":True});r.delete_cookie("ff_session");return r
@APP.post("/api/image/upscale")
async def upscale(req:Request,file:UploadFile=File(...),scale:int=Form(2)):
 limit(req);b=await file.read();check(b);url,tok=os.getenv("AI_UPSCALE_URL"),os.getenv("AI_UPSCALE_TOKEN")
 if scale not in (2,4):raise HTTPException(400,"Scale должен быть 2 или 4")
 if url and tok:
  r=requests.post(url,headers={"Authorization":f"Bearer {tok}"},files={"file":("input",b,file.content_type or "application/octet-stream")},data={"scale":str(scale)},timeout=180)
  if r.status_code>=400:raise HTTPException(502,"AI upscale provider error")
  return out(r.content,"ai-upscaled.webp","image/webp")
 x=im(b).convert("RGB").resize((im(b).width*scale,im(b).height*scale),Image.Resampling.LANCZOS);x=ImageEnhance.Sharpness(ImageEnhance.Contrast(x).enhance(1.04)).enhance(1.35);z=io.BytesIO();x.save(z,"WEBP",quality=94);return out(z.getvalue(),"upscaled.webp","image/webp")
@APP.post("/api/image/convert")
async def convert(req:Request,file:UploadFile=File(...),fmt:str=Form("webp")):
 limit(req);b=await file.read();check(b);x=im(b);fmt=fmt.lower()
 if fmt not in ("jpg","jpeg","png","webp"):raise HTTPException(400,"Формат не поддерживается")
 if fmt in ("jpg","jpeg"):x=x.convert("RGB")
 z=io.BytesIO();x.save(z,"JPEG" if fmt in ("jpg","jpeg") else fmt.upper(),quality=92);return out(z.getvalue(),f"converted.{fmt}","image/jpeg" if fmt in ("jpg","jpeg") else f"image/{fmt}")
@APP.post("/api/image/compress")
async def compress(req:Request,file:UploadFile=File(...),quality:int=Form(80)):
 limit(req);b=await file.read();check(b);z=io.BytesIO();im(b).convert("RGB").save(z,"JPEG",quality=max(10,min(100,quality)),optimize=True);return out(z.getvalue(),"compressed.jpg","image/jpeg")
@APP.post("/api/pdf/to-images")
async def pdf_images(req:Request,file:UploadFile=File(...),fmt:str=Form("png")):
 limit(req);b=await file.read();check(b)
 if fmt not in ("png","jpg"):raise HTTPException(400,"Формат не поддерживается")
 pages=convert_from_bytes(b,dpi=150,fmt="png" if fmt=="png" else "jpeg")
 if len(pages)==1:
  z=io.BytesIO();pages[0].save(z,"PNG" if fmt=="png" else "JPEG");return out(z.getvalue(),f"page-1.{fmt}","image/png" if fmt=="png" else "image/jpeg")
 z=io.BytesIO()
 with zipfile.ZipFile(z,"w",zipfile.ZIP_DEFLATED) as q:
  for i,p in enumerate(pages,1):
   x=io.BytesIO();p.save(x,"PNG" if fmt=="png" else "JPEG");q.writestr(f"page-{i}.{fmt}",x.getvalue())
 return out(z.getvalue(),"pdf-pages.zip","application/zip")
@APP.post("/api/images/to-pdf")
async def images_pdf(req:Request,files:list[UploadFile]=File(...)):
 limit(req);a=[]
 for f in files:b=await f.read();check(b);a.append(im(b).convert("RGB"))
 if not a:raise HTTPException(400,"Нет изображений")
 z=io.BytesIO();a[0].save(z,"PDF",save_all=True,append_images=a[1:]);return out(z.getvalue(),"images.pdf","application/pdf")
@APP.post("/api/pdf/merge")
async def merge(req:Request,files:list[UploadFile]=File(...)):
 limit(req);w=PdfWriter()
 for f in files:
  b=await f.read();check(b)
  for p in PdfReader(io.BytesIO(b)).pages:w.add_page(p)
 z=io.BytesIO();w.write(z);return out(z.getvalue(),"merged.pdf","application/pdf")
@APP.post("/api/pdf/to-djvu")
async def pdf_to_djvu(req:Request,file:UploadFile=File(...)):
 limit(req);b=await file.read();check(b)
 with tempfile.TemporaryDirectory() as td:
  src=Path(td)/"input.pdf";dst=Path(td)/"output.djvu";src.write_bytes(b)
  try:p=subprocess.run(["pdf2djvu","-o",str(dst),str(src)],capture_output=True,timeout=180)
  except FileNotFoundError:raise HTTPException(503,"PDF→DJVU provider is not installed")
  if p.returncode or not dst.exists():raise HTTPException(500,"PDF to DJVU conversion failed")
  return out(dst.read_bytes(),"converted.djvu","image/vnd.djvu")
@APP.post("/api/djvu/to-pdf")
async def djvu(req:Request,file:UploadFile=File(...)):
 limit(req);b=await file.read();check(b)
 with tempfile.TemporaryDirectory() as td:
  src=Path(td)/"a.djvu";dst=Path(td)/"a.pdf";src.write_bytes(b);p=subprocess.run(["ddjvu","-format=pdf",str(src),str(dst)],capture_output=True)
  if p.returncode:raise HTTPException(500,"DJVU conversion failed")
  return out(dst.read_bytes(),"converted.pdf","application/pdf")
@APP.post("/api/photo/animate")
async def animate(req:Request,file:UploadFile=File(...)):
 limit(req);b=await file.read();check(b);url,tok=os.getenv("ANIMATION_API_URL"),os.getenv("ANIMATION_API_TOKEN")
 if not(url and tok):raise HTTPException(503,"Photo animation provider is not configured")
 r=requests.post(url,headers={"Authorization":f"Bearer {tok}"},files={"file":("photo",b,file.content_type or "image/jpeg")},timeout=60)
 if r.status_code>=400:raise HTTPException(502,"Animation provider error")
 return JSONResponse({"provider_response":r.json() if "json" in r.headers.get("content-type","") else r.text})
@APP.post("/api/premium/create")
def premium_create(req:Request):
 u=user(req)
 if not u:raise HTTPException(401,"Войдите в аккаунт")
 with db() as c:x=c.execute("INSERT INTO orders(user_id,amount,status,created_at) VALUES(?,?,?,?)",(u["id"],PRICE,"pending",int(time.time())));oid=x.lastrowid
 url,tok=os.getenv("PAYMENT_API_URL"),os.getenv("PAYMENT_API_TOKEN")
 if not(url and tok):return {"order_id":oid,"status":"pending","amount":PRICE,"checkout_url":None,"message":"Платёжный провайдер ещё не подключён"}
 r=requests.post(url,headers={"Authorization":f"Bearer {tok}"},json={"order_id":oid,"amount":PRICE,"currency":"RUB"},timeout=30)
 if r.status_code>=400:raise HTTPException(502,"Payment provider error")
 j=r.json()
 with db() as c:c.execute("UPDATE orders SET provider_id=? WHERE id=?",(str(j.get("id","")),oid))
 return {"order_id":oid,"status":"pending","amount":PRICE,"checkout_url":j.get("checkout_url")}
@APP.post("/api/payment/webhook")
async def webhook(req:Request):
 body=await req.body();secret=os.getenv("PAYMENT_WEBHOOK_SECRET","");signature=req.headers.get("X-FileForge-Signature","")
 if secret:
  expected=hmac.new(secret.encode(),body,hashlib.sha256).hexdigest()
  if not hmac.compare_digest(expected,signature):raise HTTPException(401,"Invalid signature")
 try:j=__import__("json").loads(body)
 except:raise HTTPException(400,"Invalid JSON")
 oid=int(j.get("order_id",0));status=j.get("status")
 if status!="paid":return {"ok":True}
 with db() as c:
  o=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
  if not o:raise HTTPException(404,"Order not found")
  until=int(time.time())+30*24*3600;c.execute("UPDATE orders SET status='paid',paid_at=? WHERE id=?",(int(time.time()),oid));c.execute("UPDATE users SET premium=1,premium_until=? WHERE id=?",(until,o["user_id"]))
 return {"ok":True}
@APP.get("/api/config")
def config():return {"version":"6.0","price_rub":PRICE,"ai_upscale":bool(os.getenv("AI_UPSCALE_URL") and os.getenv("AI_UPSCALE_TOKEN")),"animation":bool(os.getenv("ANIMATION_API_URL") and os.getenv("ANIMATION_API_TOKEN")),"payments":bool(os.getenv("PAYMENT_API_URL") and os.getenv("PAYMENT_API_TOKEN"))}
