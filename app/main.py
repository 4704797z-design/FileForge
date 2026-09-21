import logging

logger = logging.getLogger(__name__)
import io,os,sqlite3,time,tempfile,subprocess,zipfile,hmac,hashlib,secrets
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
from app.services import seedance,yookassa,email as email_service

APP=FastAPI(title="FileForge",version="6.0")
DATA=Path(os.getenv("DATABASE","/data/fileforge.db"));DATA.parent.mkdir(parents=True,exist_ok=True)
SECRET=os.getenv("SECRET_KEY","dev-secret");SER=URLSafeTimedSerializer(SECRET)
ANON=int(os.getenv("ANON_DAILY_LIMIT","5"));USER=int(os.getenv("USER_DAILY_LIMIT","20"));PREM=int(os.getenv("PREMIUM_DAILY_LIMIT","200"));MAX=int(os.getenv("MAX_UPLOAD_MB","50"));PRICE=int(os.getenv("PREMIUM_PRICE_RUB","999"))
PREMIUM_VIDEO_SECONDS=int(os.getenv("PREMIUM_VIDEO_SECONDS","30"));FREE_VIDEO_TRIAL_SECONDS=int(os.getenv("FREE_VIDEO_TRIAL_SECONDS","5"))
EMAIL_VERIFICATION_ENABLED=os.getenv("EMAIL_VERIFICATION_ENABLED","false").lower()=="true";REQUIRE_EMAIL_VERIFICATION=os.getenv("REQUIRE_EMAIL_VERIFICATION","false").lower()=="true"
APP.mount("/static",StaticFiles(directory="app/static"),name="static")

def db():
 c=sqlite3.connect(DATA);c.row_factory=sqlite3.Row;return c
with db() as c:
 c.execute("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE,password_hash TEXT,premium INTEGER DEFAULT 0,premium_until INTEGER,created_at INTEGER)""")
 columns={r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
 for name,ddl in (("email_verified","INTEGER DEFAULT 0"),("verification_token_hash","TEXT"),("verification_expires_at","INTEGER"),("video_seconds_balance","INTEGER DEFAULT 0"),("video_trial_used","INTEGER DEFAULT 0")):
  if name not in columns:c.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
 c.execute("""CREATE TABLE IF NOT EXISTS usage(subject TEXT,day TEXT,count INTEGER,PRIMARY KEY(subject,day))""")
 c.execute("""CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,user_id INTEGER,amount INTEGER,status TEXT,provider_id TEXT,created_at INTEGER,paid_at INTEGER)""")
 c.execute("""CREATE TABLE IF NOT EXISTS generations(id INTEGER PRIMARY KEY,user_id INTEGER,public_token TEXT UNIQUE NOT NULL,status TEXT NOT NULL,provider TEXT NOT NULL,provider_request_id TEXT,prompt TEXT,input_filename TEXT,duration TEXT,resolution TEXT,aspect_ratio TEXT,generate_audio INTEGER,created_at INTEGER,completed_at INTEGER,video_url TEXT,error TEXT)""")

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

def token_hash(token):
 return hashlib.sha256(token.encode("utf-8")).hexdigest()

def send_verification_email(email,token):
 if not EMAIL_VERIFICATION_ENABLED:return False
 base=os.getenv("PUBLIC_BASE_URL","").rstrip("/")
 if not base or not email_service.configured():raise RuntimeError("Email verification requires RESEND_API_KEY, RESEND_FROM_EMAIL and PUBLIC_BASE_URL")
 email_service.send_verification(email,token,base)
 return True

def premium_active(u):
 return bool(u and u["premium"] and (not u["premium_until"] or u["premium_until"]>time.time()))

def video_cost_seconds(duration,resolution):
 seconds=int(duration)
 multiplier=3 if resolution=="1080p" else 1
 return seconds*multiplier

def grant_premium(c,user_id):
 now=int(time.time())
 u=c.execute("SELECT premium_until FROM users WHERE id=?",(user_id,)).fetchone()
 base=max(now,int(u["premium_until"] or 0)) if u else now
 until=base+30*24*3600
 c.execute("UPDATE users SET premium=1,premium_until=?,video_seconds_balance=video_seconds_balance+? WHERE id=?",(until,PREMIUM_VIDEO_SECONDS,user_id))

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
 if not u:return {"authenticated":False,"premium":False,"limit":ANON,"email_verified":False,"video_seconds_remaining":0,"video_trial_remaining":FREE_VIDEO_TRIAL_SECONDS}
 p=premium_active(u)
 remaining=int(u["video_seconds_balance"] or 0) if p else 0
 return {"authenticated":True,"email":u["email"],"premium":p,"premium_until":u["premium_until"],"limit":PREM if p else USER,"email_verified":bool(u["email_verified"]),"email_verification_enabled":EMAIL_VERIFICATION_ENABLED,"video_seconds_remaining":remaining,"video_trial_remaining":0 if u["video_trial_used"] else FREE_VIDEO_TRIAL_SECONDS}
@APP.post("/api/auth/register")
def register(req:Request,email:str=Form(...),password:str=Form(...),password_confirm:str=Form(""),accept_terms:bool=Form(False)):
 email=email.strip().lower()
 if "@" not in email or len(password)<8:raise HTTPException(400,"Нужен корректный email и пароль от 8 символов")
 if password_confirm != password:raise HTTPException(400,"Пароли не совпадают")
 if not accept_terms:raise HTTPException(400,"Нужно принять условия сервиса")
 token=secrets.token_urlsafe(32);now=int(time.time())
 try:
  with db() as c:
   x=c.execute("INSERT INTO users(email,password_hash,created_at,email_verified,verification_token_hash,verification_expires_at) VALUES(?,?,?,?,?,?)",(email,hash_password(password),now,0,token_hash(token),now+24*3600));uid=x.lastrowid
 except sqlite3.IntegrityError:raise HTTPException(409,"Пользователь уже существует")
 verification_sent=False
 if EMAIL_VERIFICATION_ENABLED:
  try:verification_sent=send_verification_email(email,token)
  except Exception as e:
   with db() as c:c.execute("DELETE FROM users WHERE id=?",(uid,))
   raise HTTPException(502,"Не удалось отправить письмо подтверждения") from e
 r=JSONResponse({"ok":True,"email_verification_enabled":EMAIL_VERIFICATION_ENABLED,"verification_sent":verification_sent,"verification_required":REQUIRE_EMAIL_VERIFICATION});r.set_cookie("ff_session",SER.dumps({"uid":uid}),httponly=True,samesite="lax",secure=os.getenv("COOKIE_SECURE","false").lower()=="true",max_age=2592000);return r
@APP.get("/api/auth/verify",response_class=HTMLResponse)
def verify_email(token:str):
 h=token_hash(token);now=int(time.time())
 with db() as c:u=c.execute("SELECT id FROM users WHERE verification_token_hash=? AND verification_expires_at>?",(h,now)).fetchone()
 if not u:return HTMLResponse("<h2>Ссылка недействительна или истекла.</h2>",status_code=400)
 with db() as c:c.execute("UPDATE users SET email_verified=1,verification_token_hash=NULL,verification_expires_at=NULL WHERE id=?",(u["id"],))
 return HTMLResponse("<script>location.href='/?verified=1#account'</script><p>Email подтверждён. Вернитесь в FileForge.</p>")
@APP.post("/api/auth/resend-verification")
def resend_verification(req:Request):
 u=user(req)
 if not u:raise HTTPException(401,"Войдите в аккаунт")
 if u["email_verified"]:return {"ok":True,"already_verified":True}
 if not EMAIL_VERIFICATION_ENABLED:raise HTTPException(503,"Email verification is disabled")
 token=secrets.token_urlsafe(32);now=int(time.time())
 with db() as c:
  c.execute("UPDATE users SET verification_token_hash=?,verification_expires_at=? WHERE id=?",(token_hash(token),now+24*3600,u["id"]))
 try:send_verification_email(u["email"],token)
 except Exception as e:raise HTTPException(502,"Не удалось отправить письмо подтверждения") from e
 return {"ok":True,"verification_sent":True}

@APP.post("/api/auth/login")
def login(email:str=Form(...),password:str=Form(...)):
 with db() as c:u=c.execute("SELECT * FROM users WHERE email=?",(email.strip().lower(),)).fetchone()
 if not u or not verify_password(password,u["password_hash"]):raise HTTPException(401,"Неверные данные")
 if REQUIRE_EMAIL_VERIFICATION and not u["email_verified"]:raise HTTPException(403,"Сначала подтвердите email")
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
async def animate(req:Request,file:UploadFile=File(...),prompt:str=Form("Slow cinematic camera movement, natural motion, subtle depth and realistic lighting."),duration:str=Form("5"),resolution:str=Form("720p"),aspect_ratio:str=Form("auto"),generate_audio:bool=Form(True)):
 u=user(req)
 if not u:raise HTTPException(401,"Для AI-видео сначала создайте аккаунт")
 if REQUIRE_EMAIL_VERIFICATION and not u["email_verified"]:raise HTTPException(403,"Для AI-видео подтвердите email")
 if not seedance.configured():raise HTTPException(503,"Seedance provider is not configured")
 b=await file.read();check(b)
 if len(b)>30*1024*1024:raise HTTPException(413,"Seedance accepts images up to 30 MB")
 im(b)
 try:
  seedance.validate_options(prompt,duration,resolution,aspect_ratio);seconds=int(duration)
 except (ValueError,TypeError) as e:raise HTTPException(400,str(e))
 limit(req)
 p=premium_active(u);reserved=video_cost_seconds(duration,resolution) if p else 0;trial_reserved=False
 if not p:
  if resolution!="720p":raise HTTPException(400,"Для Free-доступа доступно только 720p")
  if seconds>FREE_VIDEO_TRIAL_SECONDS:raise HTTPException(402,f"Бесплатный пробный лимит — {FREE_VIDEO_TRIAL_SECONDS} секунд AI-видео")
  with db() as c:
   changed=c.execute("UPDATE users SET video_trial_used=1 WHERE id=? AND video_trial_used=0",(u["id"],)).rowcount
  if changed!=1:raise HTTPException(402,"Пробный AI-лимит уже использован")
  trial_reserved=True
 else:
  if u["video_seconds_balance"]<reserved:raise HTTPException(402,f"Недостаточно AI-секунд. Осталось {u['video_seconds_balance']} сек., нужно {reserved}")
  with db() as c:
   changed=c.execute("UPDATE users SET video_seconds_balance=video_seconds_balance-? WHERE id=? AND video_seconds_balance>=?",(reserved,u["id"],reserved)).rowcount
  if changed!=1:raise HTTPException(402,"Недостаточно AI-секунд")
 token=secrets.token_urlsafe(24);now=int(time.time())
 with db() as c:
  x=c.execute("""INSERT INTO generations(user_id,public_token,status,provider,prompt,input_filename,duration,resolution,aspect_ratio,generate_audio,created_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(u["id"],token,"submitting","seedance-2.0",prompt.strip(),file.filename or "photo",duration,resolution,aspect_ratio,int(generate_audio),now))
  gid=x.lastrowid
 try:
  request_id=seedance.submit(b,file.content_type,prompt,duration,resolution,aspect_ratio,generate_audio)
 except Exception as e:
  logger.exception("Seedance submit failed")
  with db() as c:
   c.execute("UPDATE generations SET status='failed',error=? WHERE id=?",(str(e)[:1000],gid))
   if p:c.execute("UPDATE users SET video_seconds_balance=video_seconds_balance+? WHERE id=?",(reserved,u["id"]))
   elif trial_reserved:c.execute("UPDATE users SET video_trial_used=0 WHERE id=?",(u["id"],))
  raise HTTPException(502,"Seedance request could not be submitted")
 with db() as c:c.execute("UPDATE generations SET status='queued',provider_request_id=? WHERE id=?",(request_id,gid))
 return {"generation_id":gid,"token":token,"status":"queued","provider_request_id":request_id,"video_seconds_charged":reserved if p else seconds}

@APP.get("/api/photo/animate/{token}")
def animation_status(token:str):
 with db() as c:g=c.execute("SELECT * FROM generations WHERE public_token=? AND provider='seedance-2.0'",(token,)).fetchone()
 if not g:raise HTTPException(404,"Generation not found")
 if g["status"] in ("queued","processing","submitting"):
  try:
   s=seedance.status(g["provider_request_id"],g["resolution"])
  except Exception as e:
   return {"generation_id":g["id"],"status":g["status"],"error":str(e)[:500]}
  if s["status"]=="processing":
   with db() as c:c.execute("UPDATE generations SET status='processing' WHERE id=?",(g["id"],))
   return {"generation_id":g["id"],"status":"processing"}
  if s["status"]=="completed":
   video=s["video"]
   with db() as c:c.execute("UPDATE generations SET status='completed',completed_at=?,video_url=? WHERE id=?",(int(time.time()),video["url"],g["id"]))
   return {"generation_id":g["id"],"status":"completed","video":video,"seed":s.get("seed")}
  if s["status"]=="failed":
   with db() as c:c.execute("UPDATE generations SET status='failed',error=? WHERE id=?",(s.get("error","Seedance generation failed"),g["id"]))
   return {"generation_id":g["id"],"status":"failed","error":s.get("error","Seedance generation failed")}
 if g["status"]=="completed":
  return {"generation_id":g["id"],"status":"completed","video":{"url":g["video_url"]}}
 return {"generation_id":g["id"],"status":g["status"],"error":g["error"]}
@APP.get("/api/photo/animate/{token}/download")
def animation_download(token:str):
 with db() as c:g=c.execute("SELECT * FROM generations WHERE public_token=? AND status='completed'",(token,)).fetchone()
 if not g or not g["video_url"]:raise HTTPException(404,"Video is not ready")
 try:
  r=requests.get(g["video_url"],timeout=120)
 except requests.RequestException:raise HTTPException(502,"Video download failed")
 if r.status_code>=400:raise HTTPException(502,"Video provider returned an error")
 return StreamingResponse(io.BytesIO(r.content),media_type="video/mp4",headers={"Content-Disposition":'attachment; filename="fileforge-animation.mp4"'})

@APP.post("/api/premium/create")
def premium_create(req:Request):
 u=user(req)
 if not u:raise HTTPException(401,"Войдите в аккаунт")
 if REQUIRE_EMAIL_VERIFICATION and not u["email_verified"]:raise HTTPException(403,"Для Premium подтвердите email")
 with db() as c:x=c.execute("INSERT INTO orders(user_id,amount,status,created_at) VALUES(?,?,?,?)",(u["id"],PRICE,"pending",int(time.time())));oid=x.lastrowid
 provider=os.getenv("PAYMENT_PROVIDER","manual").lower()
 if provider=="yookassa":
  if not yookassa.configured():return {"order_id":oid,"status":"pending","amount":PRICE,"checkout_url":None,"message":"YooKassa ещё не настроена"}
  try:j=yookassa.create_payment(oid,PRICE)
  except Exception:raise HTTPException(502,"Payment provider error")
  with db() as c:c.execute("UPDATE orders SET provider_id=? WHERE id=?",(str(j.get("id","")),oid))
  return {"order_id":oid,"status":j.get("status","pending"),"amount":PRICE,"checkout_url":j.get("checkout_url")}
 url,tok=os.getenv("PAYMENT_API_URL"),os.getenv("PAYMENT_API_TOKEN")
 if not(url and tok):return {"order_id":oid,"status":"pending","amount":PRICE,"checkout_url":None,"message":"Платёжный провайдер ещё не подключён"}
 r=requests.post(url,headers={"Authorization":f"Bearer {tok}"},json={"order_id":oid,"amount":PRICE,"currency":"RUB"},timeout=30)
 if r.status_code>=400:raise HTTPException(502,"Payment provider error")
 j=r.json()
 with db() as c:c.execute("UPDATE orders SET provider_id=? WHERE id=?",(str(j.get("id","")),oid))
 return {"order_id":oid,"status":"pending","amount":PRICE,"checkout_url":j.get("checkout_url")}

@APP.post("/api/payment/webhook")
async def webhook(req:Request):
 body=await req.body()
 try:j=__import__("json").loads(body)
 except:raise HTTPException(400,"Invalid JSON")
 provider=os.getenv("PAYMENT_PROVIDER","manual").lower()
 if provider=="yookassa":
  event=j.get("event","");obj=j.get("object") or {};payment_id=obj.get("id")
  if event!="payment.succeeded" or not payment_id:return {"ok":True}
  try:payment=yookassa.get_payment(str(payment_id))
  except Exception:raise HTTPException(502,"Unable to verify payment")
  if payment.get("status")!="succeeded":return {"ok":True}
  meta=payment.get("metadata") or {}
  try:oid=int(meta.get("order_id","0"))
  except ValueError:raise HTTPException(400,"Invalid order_id")
  paid_amount=payment.get("amount") or {}
  with db() as c:
   o=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
   if not o:raise HTTPException(404,"Order not found")
   if o["status"]=="paid":return {"ok":True,"idempotent":True}
   if str(paid_amount.get("currency"))!="RUB" or float(paid_amount.get("value",0)) != float(o["amount"]):
    raise HTTPException(400,"Payment amount mismatch")
   grant_premium(c,o["user_id"]);c.execute("UPDATE orders SET status='paid',paid_at=? WHERE id=?",(int(time.time()),oid))
  return {"ok":True}
 secret=os.getenv("PAYMENT_WEBHOOK_SECRET","");signature=req.headers.get("X-FileForge-Signature","")
 if secret:
  expected=hmac.new(secret.encode(),body,hashlib.sha256).hexdigest()
  if not hmac.compare_digest(expected,signature):raise HTTPException(401,"Invalid signature")
 oid=int(j.get("order_id",0));status=j.get("status")
 if status!="paid":return {"ok":True}
 with db() as c:
  o=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
  if not o:raise HTTPException(404,"Order not found")
  if o["status"]=="paid":return {"ok":True,"idempotent":True}
  grant_premium(o["user_id"]) ;c.execute("UPDATE orders SET status='paid',paid_at=? WHERE id=?",(int(time.time()),oid))
 return {"ok":True}

@APP.get("/api/config")
def config():return {"version":"6.0","price_rub":PRICE,"premium_video_seconds":PREMIUM_VIDEO_SECONDS,"free_video_trial_seconds":FREE_VIDEO_TRIAL_SECONDS,"ai_upscale":bool(os.getenv("AI_UPSCALE_URL") and os.getenv("AI_UPSCALE_TOKEN")),"animation":seedance.configured(),"payments":(yookassa.configured() if os.getenv("PAYMENT_PROVIDER","manual").lower()=="yookassa" else bool(os.getenv("PAYMENT_API_URL") and os.getenv("PAYMENT_API_TOKEN")))}
