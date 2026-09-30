import logging

logger = logging.getLogger(__name__)
import io,json,os,sqlite3,time,tempfile,subprocess,zipfile,hmac,hashlib,secrets,threading
from pathlib import Path
import bcrypt
import requests
from fastapi import FastAPI,UploadFile,File,Form,Request,HTTPException
from fastapi.responses import StreamingResponse,HTMLResponse,JSONResponse,RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import URLSafeTimedSerializer
from PIL import Image,ImageEnhance
from pypdf import PdfReader,PdfWriter
from pdf2image import convert_from_bytes
from app.services import mixen,yookassa,text_ai,email as email_service

APP=FastAPI(title="FileForge",version="6.0")
DATA=Path(os.getenv("DATABASE","/data/fileforge.db"));DATA.parent.mkdir(parents=True,exist_ok=True)
SECRET=os.getenv("SECRET_KEY","dev-secret");SER=URLSafeTimedSerializer(SECRET)
ANON=int(os.getenv("ANON_DAILY_LIMIT","5"));USER=int(os.getenv("USER_DAILY_LIMIT","20"));PREM=int(os.getenv("PREMIUM_DAILY_LIMIT","200"));MAX=int(os.getenv("MAX_UPLOAD_MB","50"));PRICE=int(os.getenv("PREMIUM_PRICE_RUB","450"))
PREMIUM_VIDEO_SECONDS=int(os.getenv("PREMIUM_VIDEO_SECONDS","30"));FREE_VIDEO_TRIAL_SECONDS=int(os.getenv("FREE_VIDEO_TRIAL_SECONDS","5"));PREMIUM_IMAGE_MONTHLY=int(os.getenv("PREMIUM_IMAGE_MONTHLY","30"));FREE_IMAGE_MONTHLY=int(os.getenv("FREE_IMAGE_MONTHLY","2"));AI_VIDEO_MIXEN_RUB_PER_SECOND=float(os.getenv("AI_VIDEO_MIXEN_RUB_PER_SECOND","6.74"));AI_VIDEO_SERVICE_FEE_RUB=int(os.getenv("AI_VIDEO_SERVICE_FEE_RUB","30"))
EMAIL_VERIFICATION_ENABLED=os.getenv("EMAIL_VERIFICATION_ENABLED","false").lower()=="true";REQUIRE_EMAIL_VERIFICATION=os.getenv("REQUIRE_EMAIL_VERIFICATION","false").lower()=="true"
APP.mount("/static",StaticFiles(directory="app/static"),name="static")

def db():
 c=sqlite3.connect(DATA);c.row_factory=sqlite3.Row;return c
with db() as c:
 c.execute("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE,password_hash TEXT,premium INTEGER DEFAULT 0,premium_until INTEGER,created_at INTEGER)""")
 columns={r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
 for name,ddl in (("email_verified","INTEGER DEFAULT 0"),("verification_token_hash","TEXT"),("verification_expires_at","INTEGER"),("video_seconds_balance","INTEGER DEFAULT 0"),("video_trial_used","INTEGER DEFAULT 0"),("image_month","TEXT"),("image_count","INTEGER DEFAULT 0")):
  if name not in columns:c.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
 c.execute("""CREATE TABLE IF NOT EXISTS usage(subject TEXT,day TEXT,count INTEGER,PRIMARY KEY(subject,day))""")
 c.execute("""CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,user_id INTEGER,amount REAL,status TEXT,provider_id TEXT,payment_type TEXT DEFAULT 'premium',project_id INTEGER,created_at INTEGER,paid_at INTEGER)""")
 order_columns={r["name"] for r in c.execute("PRAGMA table_info(orders)").fetchall()}
 for name,ddl in (("payment_type","TEXT DEFAULT 'premium'"),("project_id","INTEGER")):
  if name not in order_columns:c.execute(f"ALTER TABLE orders ADD COLUMN {name} {ddl}")
 c.execute("""CREATE TABLE IF NOT EXISTS generations(id INTEGER PRIMARY KEY,user_id INTEGER,public_token TEXT UNIQUE NOT NULL,status TEXT NOT NULL,provider TEXT NOT NULL,provider_request_id TEXT,prompt TEXT,input_filename TEXT,duration TEXT,resolution TEXT,aspect_ratio TEXT,generate_audio INTEGER,created_at INTEGER,completed_at INTEGER,video_url TEXT,error TEXT)""")
 c.execute("""CREATE TABLE IF NOT EXISTS video_projects(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,title TEXT NOT NULL,idea TEXT NOT NULL,format TEXT NOT NULL,duration INTEGER NOT NULL,status TEXT NOT NULL,script TEXT,voice TEXT,subtitles TEXT,reference_data BLOB,reference_content_type TEXT,final_video TEXT,error TEXT,charged_seconds INTEGER DEFAULT 0,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)""")
 project_columns={r["name"] for r in c.execute("PRAGMA table_info(video_projects)").fetchall()}
 for name,ddl in (("charged_seconds","INTEGER DEFAULT 0"),("reference_data","BLOB"),("reference_content_type","TEXT")):
  if name not in project_columns:c.execute(f"ALTER TABLE video_projects ADD COLUMN {name} {ddl}")
 c.execute("""CREATE TABLE IF NOT EXISTS video_scenes(id INTEGER PRIMARY KEY,project_id INTEGER NOT NULL,scene_number INTEGER NOT NULL,duration INTEGER NOT NULL,narration TEXT NOT NULL,visual_prompt TEXT NOT NULL,video_prompt TEXT NOT NULL,status TEXT NOT NULL,provider_request_id TEXT,video_url TEXT,error TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(project_id,scene_number))""")
 c.execute("""CREATE TABLE IF NOT EXISTS video_project_messages(id INTEGER PRIMARY KEY,project_id INTEGER NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created_at INTEGER NOT NULL)""")
 c.execute("CREATE INDEX IF NOT EXISTS idx_video_projects_user_updated ON video_projects(user_id,updated_at DESC)")
 c.execute("CREATE INDEX IF NOT EXISTS idx_video_scenes_project_order ON video_scenes(project_id,scene_number)")

def user(req):
 t=req.cookies.get("ff_session")
 if not t:return None
 try:
  d=SER.loads(t,max_age=2592000)
  with db() as c:return c.execute("SELECT * FROM users WHERE id=?",(int(d["uid"]),)).fetchone()
 except:return None

def is_premium(u):
 return bool(u and u["premium"] and (not u["premium_until"] or u["premium_until"]>time.time()))

def limit(req):
 u=user(req); sub=f"user:{u['id']}" if u else f"ip:{req.client.host if req.client else 'unknown'}"
 lim=PREM if is_premium(u) else USER if u else ANON
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

def provider_error(e):
 """Map provider exceptions to user-friendly messages; internals stay in logs."""
 msg=str(e)
 if "402" in msg or "balance" in msg.lower() or "insufficient" in msg.lower():
  return "Сервис обработки временно недоступен — попробуйте повторить через несколько минут"
 if "401" in msg or "api key" in msg.lower() or "unauthorized" in msg.lower():
  return "Сервис обработки временно недоступен — попробуйте повторить позже"
 if "unavailable" in msg.lower() or "503" in msg or "overloaded" in msg.lower():
  return "AI-модель временно перегружена — попробуйте ещё раз через пару минут"
 return "Не удалось выполнить операцию. Попробуйте позже или обратитесь в поддержку"

def video_cost_seconds(duration,resolution):
 seconds=int(duration)
 multiplier=2 if resolution=="1080p" else 1
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
@APP.get("/requisites",response_class=HTMLResponse)
def requisites():return Path("app/static/requisites.html").read_text(encoding="utf8")
@APP.get("/api/site/requisites",include_in_schema=False)
def site_requisites():
 return {"name":os.getenv("SELLER_NAME",""),"inn":os.getenv("SELLER_INN",""),"email":os.getenv("SELLER_EMAIL","")}
@APP.get("/favicon.ico",include_in_schema=False)
def favicon():
 return RedirectResponse("/static/favicon.ico",status_code=302)
@APP.get("/robots.txt",include_in_schema=False)
def robots():
 return Response("User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: https://file-forge.ru/sitemap.xml\n",media_type="text/plain")
@APP.get("/sitemap.xml",include_in_schema=False)
def sitemap():
 return Response("""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
 <url><loc>https://file-forge.ru/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>
 <url><loc>https://file-forge.ru/ai-video</loc><changefreq>weekly</changefreq><priority>0.9</priority></url>
</urlset>""",media_type="application/xml")
@APP.get("/ai-video",response_class=HTMLResponse)
def ai_video_page():return Path("app/static/ai-video.html").read_text(encoding="utf8")

AI_VIDEO_SYSTEM="""You are FileForge AI Video Director. Create short social video plans in Russian. Return ONLY JSON with title, script, voice, subtitles and scenes. scenes must be an array of 3-6 objects with duration (integer seconds), narration, visual_prompt, video_prompt. Each video_prompt must be self-contained, cinematic and safe. The total duration must be close to the requested duration; each scene must be 2-10 seconds."""

def project_row(c,project_id,user_id):
 p=c.execute("SELECT * FROM video_projects WHERE id=? AND user_id=?",(project_id,user_id)).fetchone()
 if not p:raise HTTPException(404,"Проект не найден")
 return p

def mixen_scene_bill_seconds(seconds:int) -> int:
 """Mixen bills a minimum of 10 seconds per video job."""
 return max(10, seconds)

def project_data(c,p):
 scenes=c.execute("SELECT scene_number,duration,narration,visual_prompt,video_prompt,status,video_url,error FROM video_scenes WHERE project_id=? ORDER BY scene_number",(p["id"],)).fetchall()
 seconds=sum(mixen_scene_bill_seconds(s["duration"]) for s in scenes);provider_cost=round(seconds*AI_VIDEO_MIXEN_RUB_PER_SECOND,2);total=round(provider_cost+AI_VIDEO_SERVICE_FEE_RUB,2)
 return {"id":p["id"],"title":p["title"],"idea":p["idea"],"format":p["format"],"duration":p["duration"],"status":p["status"],"script":p["script"],"voice":p["voice"],"subtitles":p["subtitles"],"final_video":bool(p["final_video"]),"error":p["error"],"pricing":{"seconds":seconds,"mixen_rub_per_second":AI_VIDEO_MIXEN_RUB_PER_SECOND,"provider_cost_rub":provider_cost,"service_fee_rub":AI_VIDEO_SERVICE_FEE_RUB,"total_rub":total},"scenes":[dict(s) for s in scenes]}

def director_plan(idea,fmt,duration,revision=""):
 prompt=f"Idea: {idea}\nFormat: {fmt}\nTarget duration: {duration} seconds. {revision}".strip()
 raw=text_ai.chat([{"role":"system","content":AI_VIDEO_SYSTEM},{"role":"user","content":prompt}],{"type":"json_object"})
 try:plan=json.loads(raw)
 except ValueError as e:raise RuntimeError("AI director returned invalid plan") from e
 scenes=plan.get("scenes")
 if not isinstance(scenes,list) or not 3<=len(scenes)<=6:raise RuntimeError("AI director returned invalid scenes")
 clean=[]
 for scene in scenes:
  seconds=int(scene.get("duration",0))
  if not 2<=seconds<=10:raise RuntimeError("AI director returned invalid scene duration")
  clean.append({"duration":seconds,"narration":str(scene.get("narration","")).strip()[:1500],"visual_prompt":str(scene.get("visual_prompt","")).strip()[:4000],"video_prompt":str(scene.get("video_prompt","")).strip()[:4000]})
 if not all(s["narration"] and s["video_prompt"] for s in clean):raise RuntimeError("AI director returned incomplete scenes")
 return {"title":str(plan.get("title") or idea[:80]).strip()[:160],"script":str(plan.get("script") or "").strip()[:12000],"voice":str(plan.get("voice") or "").strip()[:12000],"subtitles":str(plan.get("subtitles") or "").strip()[:12000],"scenes":clean}

def launch_project_scene(project_id,scene_number):
 with db() as c:
  scene=c.execute("SELECT s.*,p.user_id,p.format,p.reference_data,p.reference_content_type FROM video_scenes s JOIN video_projects p ON p.id=s.project_id WHERE s.project_id=? AND s.scene_number=?",(project_id,scene_number)).fetchone()
  if not scene:return
  try:
   request_id=mixen.submit(scene["reference_data"],scene["reference_content_type"],scene["video_prompt"],str(scene["duration"]),"720p",scene["format"],False) if scene_number==1 and scene["reference_data"] else mixen.submit_text(scene["video_prompt"],str(scene["duration"]),"720p",scene["format"],False)
  except Exception as e:
   logger.exception("AI video project scene submit failed")
   c.execute("UPDATE video_scenes SET status='failed',error=?,updated_at=? WHERE id=?",(provider_error(e),int(time.time()),scene["id"]))
   c.execute("UPDATE video_projects SET status='failed',error=?,updated_at=? WHERE id=?",(f"Не удалось создать сцену {scene_number}. Повторите её.",int(time.time()),project_id));return
  c.execute("UPDATE video_scenes SET status='queued',provider_request_id=?,error=NULL,updated_at=? WHERE id=?",(request_id,int(time.time()),scene["id"]))

def assemble_project(project_id):
 with db() as c:
  rows=c.execute("SELECT video_url FROM video_scenes WHERE project_id=? ORDER BY scene_number",(project_id,)).fetchall()
  if not rows or any(not r["video_url"] for r in rows):return
 try:
  output_dir=DATA.parent/"ai-video";output_dir.mkdir(exist_ok=True)
  parts=[]
  for index,row in enumerate(rows):
   response=requests.get(row["video_url"],headers={"Authorization":f"Bearer {os.getenv('MIXEN_API_KEY','')}"},timeout=180);response.raise_for_status()
   part=output_dir/f"{project_id}-{index}.mp4";part.write_bytes(response.content);parts.append(part)
  manifest=output_dir/f"{project_id}.txt";manifest.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts),encoding="utf8")
  target=output_dir/f"{project_id}.mp4"
  result=subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(manifest),"-c","copy",str(target)],capture_output=True,timeout=300)
  if result.returncode:raise RuntimeError("ffmpeg montage failed")
  with db() as c:c.execute("UPDATE video_projects SET status='completed',final_video=?,updated_at=? WHERE id=?",(str(target),int(time.time()),project_id))
 except Exception as e:
  logger.exception("AI video project montage failed")
  with db() as c:c.execute("UPDATE video_projects SET status='failed',error=?,updated_at=? WHERE id=?",("Не удалось собрать финальный ролик. Попробуйте создать новую версию.",int(time.time()),project_id))

@APP.post("/api/ai-video/projects")
async def create_video_project(req:Request,idea:str=Form(...),format:str=Form("9:16"),duration:int=Form(30),reference:UploadFile|None=File(None)):
 u=user(req)
 if not u:raise HTTPException(401,"Для AI Видео войдите в аккаунт")
 if REQUIRE_EMAIL_VERIFICATION and not u["email_verified"]:raise HTTPException(403,"Для AI Видео подтвердите email")
 idea=idea.strip()
 if not 10<=len(idea)<=4000:raise HTTPException(400,"Опишите идею ролика подробнее (от 10 до 4000 символов)")
 if format not in ("9:16","16:9","1:1") or duration not in (15,30,45):raise HTTPException(400,"Недопустимые параметры видео")
 reference_data=None;reference_content_type=None
 if reference:
  reference_data=await reference.read();check(reference_data)
  reference_content_type=reference.content_type or ""
  if not reference_content_type.startswith("image/"):raise HTTPException(400,"Референсом может быть только изображение")
  im(reference_data)
  if len(reference_data)>30*1024*1024:raise HTTPException(413,"Референс для AI Видео — максимум 30 МБ")
 if not mixen.configured():raise HTTPException(503,"AI-сервис временно недоступен")
 try:plan=director_plan(idea,format,duration)
 except Exception as e:
  logger.exception("AI director plan failed");raise HTTPException(502,provider_error(e))
 now=int(time.time())
 with db() as c:
  x=c.execute("INSERT INTO video_projects(user_id,title,idea,format,duration,status,script,voice,subtitles,reference_data,reference_content_type,created_at,updated_at) VALUES(?,?,?,?,?,'planned',?,?,?,?,?,?,?)",(u["id"],plan["title"],idea,format,duration,plan["script"],plan["voice"],plan["subtitles"],reference_data,reference_content_type,now,now));pid=x.lastrowid
  for number,scene in enumerate(plan["scenes"],1):c.execute("INSERT INTO video_scenes(project_id,scene_number,duration,narration,visual_prompt,video_prompt,status,created_at,updated_at) VALUES(?,?,?,?,?,?, 'planned',?,?)",(pid,number,scene["duration"],scene["narration"],scene["visual_prompt"],scene["video_prompt"],now,now))
  c.execute("INSERT INTO video_project_messages(project_id,role,content,created_at) VALUES(?,?,?,?)",(pid,"user",idea,now));c.execute("INSERT INTO video_project_messages(project_id,role,content,created_at) VALUES(?,?,?,?)",(pid,"assistant","План ролика готов. Проверьте сцены и подтвердите создание видео.",now))
  return {**project_data(c,project_row(c,pid,u["id"])),"assistant_message":"Я подготовил сценарий и сцены. Можете попросить изменить стиль, темп или конкретную сцену — либо нажмите «Создать видео»."}

@APP.get("/api/ai-video/projects/{project_id}")
def get_video_project(req:Request,project_id:int):
 u=user(req)
 if not u:raise HTTPException(401,"Войдите в аккаунт")
 with db() as c:
  p=project_row(c,project_id,u["id"])
  pending=c.execute("SELECT * FROM video_scenes WHERE project_id=? AND status IN ('queued','processing')",(project_id,)).fetchall()
  for scene in pending:
   state=mixen.status(scene["provider_request_id"],"720p")
   if state["status"]=='completed':c.execute("UPDATE video_scenes SET status='completed',video_url=?,updated_at=? WHERE id=?",(state["video"]["url"],int(time.time()),scene["id"]))
   elif state["status"]=='failed':c.execute("UPDATE video_scenes SET status='failed',error=?,updated_at=? WHERE id=?",(provider_error(RuntimeError(state.get("error","provider failed"))),int(time.time()),scene["id"]))
   elif state["status"]=='processing':c.execute("UPDATE video_scenes SET status='processing',updated_at=? WHERE id=?",(int(time.time()),scene["id"]))
  scenes=c.execute("SELECT status FROM video_scenes WHERE project_id=?",(project_id,)).fetchall()
  if scenes and all(s["status"]=='completed' for s in scenes) and p["status"]=='generating':
   c.execute("UPDATE video_projects SET status='editing',updated_at=? WHERE id=?",(int(time.time()),project_id));threading.Thread(target=assemble_project,args=(project_id,),daemon=True).start()
  elif p["status"]=='editing' and scenes and all(s["status"]=='completed' for s in scenes) and int(time.time())-p["updated_at"]>300:
   # Montage thread was lost (e.g. container restart): retry assembly on every poll after 5 minutes.
   c.execute("UPDATE video_projects SET updated_at=? WHERE id=?",(int(time.time()),project_id));threading.Thread(target=assemble_project,args=(project_id,),daemon=True).start()
  elif any(s["status"]=='failed' for s in scenes):c.execute("UPDATE video_projects SET status='failed',updated_at=? WHERE id=?",(int(time.time()),project_id))
  return project_data(c,project_row(c,project_id,u["id"]))

@APP.post("/api/ai-video/projects/{project_id}/messages")
async def update_video_project(req:Request,project_id:int):
 u=user(req)
 if not u:raise HTTPException(401,"Войдите в аккаунт")
 try:payload=await req.json();message=str(payload.get("message","")).strip()
 except Exception:raise HTTPException(400,"Некорректное сообщение")
 if not 3<=len(message)<=4000:raise HTTPException(400,"Сообщение должно содержать от 3 до 4000 символов")
 with db() as c:p=project_row(c,project_id,u["id"])
 if p["status"]!='planned':raise HTTPException(409,"Сценарий можно редактировать до запуска генерации")
 try:plan=director_plan(p["idea"],p["format"],p["duration"],f"User requested this revision: {message}")
 except Exception as e:
  logger.exception("AI director revision failed");raise HTTPException(502,provider_error(e))
 now=int(time.time())
 with db() as c:
  project_row(c,project_id,u["id"]);c.execute("UPDATE video_projects SET title=?,script=?,voice=?,subtitles=?,updated_at=? WHERE id=?",(plan["title"],plan["script"],plan["voice"],plan["subtitles"],now,project_id));c.execute("DELETE FROM video_scenes WHERE project_id=?",(project_id,))
  for number,scene in enumerate(plan["scenes"],1):c.execute("INSERT INTO video_scenes(project_id,scene_number,duration,narration,visual_prompt,video_prompt,status,created_at,updated_at) VALUES(?,?,?,?,?,?, 'planned',?,?)",(project_id,number,scene["duration"],scene["narration"],scene["visual_prompt"],scene["video_prompt"],now,now))
  c.execute("INSERT INTO video_project_messages(project_id,role,content,created_at) VALUES(?,?,?,?)",(project_id,"user",message,now));c.execute("INSERT INTO video_project_messages(project_id,role,content,created_at) VALUES(?,?,?,?)",(project_id,"assistant","План обновлён по вашему запросу.",now))
  return {**project_data(c,project_row(c,project_id,u["id"])),"assistant_message":"План обновлён. Проверьте сцены и подтвердите создание видео."}

@APP.post("/api/ai-video/projects/{project_id}/payment")
def create_video_payment(req:Request,project_id:int):
 u=user(req)
 if not u:raise HTTPException(401,"Войдите в аккаунт")
 if REQUIRE_EMAIL_VERIFICATION and not u["email_verified"]:raise HTTPException(403,"Подтвердите email перед оплатой")
 with db() as c:
  p=project_row(c,project_id,u["id"])
  if p["status"]!='planned':raise HTTPException(409,"Оплата для этого проекта уже создана или завершена")
  scenes=c.execute("SELECT duration FROM video_scenes WHERE project_id=?",(project_id,)).fetchall()
  seconds=sum(mixen_scene_bill_seconds(s["duration"]) for s in scenes);amount=round(seconds*AI_VIDEO_MIXEN_RUB_PER_SECOND+AI_VIDEO_SERVICE_FEE_RUB,2)
  existing=c.execute("SELECT * FROM orders WHERE project_id=? AND payment_type='ai_video' AND status='pending' ORDER BY id DESC LIMIT 1",(project_id,)).fetchone()
  if existing and existing["provider_id"]:return {"order_id":existing["id"],"amount":amount,"status":"pending","checkout_url":None,"message":"Платёж уже создан"}
  if existing:oid=existing["id"]
  else:x=c.execute("INSERT INTO orders(user_id,amount,status,payment_type,project_id,created_at) VALUES(?,?,?,?,?,?)",(u["id"],amount,"pending","ai_video",project_id,int(time.time())));oid=x.lastrowid
 if os.getenv("PAYMENT_PROVIDER","manual").lower()!="yookassa" or not yookassa.configured():
  raise HTTPException(503,"Оплата AI Видео временно недоступна")
 try:j=yookassa.create_payment(oid,amount,description=f"FileForge AI Видео, проект №{project_id}",return_path="/ai-video",payment_type="ai_video",project_id=project_id)
 except Exception as e:
  logger.exception("AI video payment creation failed")
  with db() as c:c.execute("UPDATE orders SET status='failed' WHERE id=?",(oid,))
  raise HTTPException(502,"Не удалось создать платёж. Попробуйте позже") from e
 with db() as c:c.execute("UPDATE orders SET provider_id=? WHERE id=?",(str(j.get("id","")),oid))
 return {"order_id":oid,"amount":amount,"status":j.get("status","pending"),"checkout_url":j.get("checkout_url")}

@APP.post("/api/ai-video/projects/{project_id}/start")
def start_video_project(req:Request,project_id:int):
 u=user(req)
 if not u:raise HTTPException(401,"Войдите в аккаунт")
 with db() as c:
  p=project_row(c,project_id,u["id"])
  if p["status"]!='paid':raise HTTPException(402,"Сначала оплатите создание видео")
  total=sum(r["duration"] for r in c.execute("SELECT duration FROM video_scenes WHERE project_id=?",(project_id,)).fetchall())
  c.execute("UPDATE video_projects SET status='generating',error=NULL,charged_seconds=?,updated_at=? WHERE id=?",(total,int(time.time()),project_id));numbers=[r["scene_number"] for r in c.execute("SELECT scene_number FROM video_scenes WHERE project_id=?",(project_id,)).fetchall()]
 for number in numbers:threading.Thread(target=launch_project_scene,args=(project_id,number),daemon=True).start()
 with db() as c:return project_data(c,project_row(c,project_id,u["id"]))

@APP.post("/api/ai-video/projects/{project_id}/scenes/{scene_number}/retry")
def retry_video_scene(req:Request,project_id:int,scene_number:int):
 u=user(req)
 if not u:raise HTTPException(401,"Войдите в аккаунт")
 with db() as c:
  project_row(c,project_id,u["id"]);scene=c.execute("SELECT id FROM video_scenes WHERE project_id=? AND scene_number=?",(project_id,scene_number)).fetchone()
  if not scene:raise HTTPException(404,"Сцена не найдена")
  c.execute("UPDATE video_projects SET status='generating',error=NULL,updated_at=? WHERE id=?",(int(time.time()),project_id));c.execute("UPDATE video_scenes SET status='submitting',error=NULL,updated_at=? WHERE id=?",(int(time.time()),scene["id"]))
 threading.Thread(target=launch_project_scene,args=(project_id,scene_number),daemon=True).start()
 with db() as c:return project_data(c,project_row(c,project_id,u["id"]))

@APP.get("/api/ai-video/projects/{project_id}/scenes/{scene_number}/preview")
def preview_video_scene(req:Request,project_id:int,scene_number:int):
 u=user(req)
 if not u:raise HTTPException(401,"Войдите в аккаунт")
 with db() as c:
  project_row(c,project_id,u["id"]);scene=c.execute("SELECT video_url FROM video_scenes WHERE project_id=? AND scene_number=? AND status='completed'",(project_id,scene_number)).fetchone()
 if not scene or not scene["video_url"]:raise HTTPException(404,"Сцена ещё не готова")
 try:r=requests.get(scene["video_url"],headers={"Authorization":f"Bearer {os.getenv('MIXEN_API_KEY','')}"},timeout=180)
 except requests.RequestException:raise HTTPException(502,"Не удалось загрузить сцену")
 if r.status_code>=400:raise HTTPException(502,"Видео-сервис не отдал сцену")
 return StreamingResponse(io.BytesIO(r.content),media_type="video/mp4")

@APP.get("/api/ai-video/projects/{project_id}/download")
def download_video_project(req:Request,project_id:int):
 u=user(req)
 if not u:raise HTTPException(401,"Войдите в аккаунт")
 with db() as c:p=project_row(c,project_id,u["id"])
 path=Path(p["final_video"] or "")
 if p["status"]!='completed' or not path.is_file():raise HTTPException(404,"Видео ещё не готово")
 return StreamingResponse(path.open("rb"),media_type="video/mp4",headers={"Content-Disposition":f'attachment; filename="fileforge-ai-video-{project_id}.mp4"'})

@APP.get("/health")
def health():return {"status":"ok","version":"6.0"}
@APP.get("/api/me")
def me(req:Request):
 u=user(req)
 if not u:return {"authenticated":False,"premium":False,"limit":ANON,"email_verified":False,"video_seconds_remaining":0,"video_trial_remaining":FREE_VIDEO_TRIAL_SECONDS}
 p=premium_active(u)
 remaining=int(u["video_seconds_balance"] or 0) if p else 0
 return {"authenticated":True,"email":u["email"],"premium":p,"premium_until":u["premium_until"],"limit":PREM if p else USER,"email_verified":bool(u["email_verified"]),"email_verification_enabled":EMAIL_VERIFICATION_ENABLED,"video_seconds_remaining":remaining,"video_trial_remaining":0 if u["video_trial_used"] else FREE_VIDEO_TRIAL_SECONDS,"image_monthly_limit":PREMIUM_IMAGE_MONTHLY if p else FREE_IMAGE_MONTHLY,"image_used":u["image_count"] if u["image_month"]==time.strftime("%Y-%m") else 0}
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
   logger.error("[ERROR] verification email failed for %s: %s",email,e)
   logger.error("[ERROR] Account created anyway; user can resend verification from profile")
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
def image_quota(req,u):
 """Monthly AI-image quota. Returns 402 when exhausted."""
 month=time.strftime("%Y-%m")
 cap=PREMIUM_IMAGE_MONTHLY if is_premium(u) else FREE_IMAGE_MONTHLY
 with db() as c:
  row=c.execute("SELECT image_month,image_count FROM users WHERE id=?",(u["id"],)).fetchone()
  count=row["image_count"] if row and row["image_month"]==month else 0
  if count>=cap:raise HTTPException(402,f"Лимит AI-изображений исчерпан ({cap}/мес). Premium: {PREMIUM_IMAGE_MONTHLY}/мес")
  c.execute("UPDATE users SET image_month=?,image_count=? WHERE id=?",(month,count+1,u["id"]))
@APP.post("/api/image/generate")
def image_generate(req:Request,prompt:str=Form(...),size:str=Form("1024x1024")):
 u=user(req)
 if not u:raise HTTPException(401,"Для AI-генерации сначала создайте аккаунт")
 limit(req)
 prompt=prompt.strip()
 if len(prompt)<3:raise HTTPException(400,"Опишите, что нужно сгенерировать")
 if len(prompt)>4000:raise HTTPException(400,"Описание слишком длинное")
 if not mixen.configured():raise HTTPException(503,"Mixen provider is not configured")
 image_quota(req,u)
 try:
  png=mixen.generate_image(prompt,size)
 except RuntimeError as e:
  logger.error("[ERROR] AI image generation failed: %s",e)
  with db() as c:c.execute("UPDATE users SET image_count=MAX(image_count-1,0) WHERE id=?",(u["id"],))
  raise HTTPException(502,provider_error(e))
 return out(png,"generated.png","image/png")
@APP.post("/api/image/ai-edit")
async def image_ai_edit(req:Request,file:UploadFile=File(...),prompt:str=Form(...)):
 u=user(req)
 if not u:raise HTTPException(401,"Для AI-редактирования сначала создайте аккаунт")
 limit(req)
 prompt=prompt.strip()
 if len(prompt)<3:raise HTTPException(400,"Опишите, что нужно изменить")
 if len(prompt)>4000:raise HTTPException(400,"Описание слишком длинное")
 if not mixen.configured():raise HTTPException(503,"Mixen provider is not configured")
 b=await file.read();check(b);im(b)
 if len(b)>20*1024*1024:raise HTTPException(413,"Изображение для AI-редактирования — максимум 20 МБ")
 image_quota(req,u)
 try:
  png=mixen.edit_image(b,file.content_type,prompt)
 except RuntimeError as e:
  logger.error("[ERROR] AI image edit failed: %s",e)
  with db() as c:c.execute("UPDATE users SET image_count=MAX(image_count-1,0) WHERE id=?",(u["id"],))
  raise HTTPException(502,provider_error(e))
 return out(png,"edited.png","image/png")
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
 if not mixen.configured():raise HTTPException(503,"Mixen provider is not configured")
 b=await file.read();check(b)
 if len(b)>30*1024*1024:raise HTTPException(413,"Mixen accepts images up to 30 MB")
 im(b)
 try:
  mixen.validate_options(prompt,duration,resolution,aspect_ratio);seconds=int(duration)
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
                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(u["id"],token,"submitting","mixen-wan-3.0",prompt.strip(),file.filename or "photo",duration,resolution,aspect_ratio,int(generate_audio),now))
  gid=x.lastrowid
 try:
  request_id=mixen.submit(b,file.content_type,prompt,duration,resolution,aspect_ratio,generate_audio)
 except Exception as e:
  logger.exception("Mixen submit failed")
  with db() as c:
   c.execute("UPDATE generations SET status='failed',error=? WHERE id=?",(str(e)[:1000],gid))
   if p:c.execute("UPDATE users SET video_seconds_balance=video_seconds_balance+? WHERE id=?",(reserved,u["id"]))
   elif trial_reserved:c.execute("UPDATE users SET video_trial_used=0 WHERE id=?",(u["id"],))
  raise HTTPException(502,provider_error(e) or "Mixen video request could not be submitted")
 with db() as c:c.execute("UPDATE generations SET status='queued',provider_request_id=? WHERE id=?",(request_id,gid))
 return {"generation_id":gid,"token":token,"status":"queued","provider_request_id":request_id,"video_seconds_charged":reserved if p else seconds}

@APP.post("/api/video/create")
def video_create(req:Request,prompt:str=Form(...),duration:str=Form("5"),resolution:str=Form("720p"),aspect_ratio:str=Form("16:9"),generate_audio:bool=Form(True)):
 u=user(req)
 if not u:raise HTTPException(401,"Для AI-видео сначала создайте аккаунт")
 if REQUIRE_EMAIL_VERIFICATION and not u["email_verified"]:raise HTTPException(403,"Для AI-видео подтвердите email")
 if not mixen.configured():raise HTTPException(503,"Mixen provider is not configured")
 try:
  mixen.validate_options(prompt,duration,resolution,aspect_ratio);seconds=int(duration)
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
                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(u["id"],token,"submitting","mixen-wan-3.0",prompt.strip(),"(text-to-video)",duration,resolution,aspect_ratio,int(generate_audio),now))
  gid=x.lastrowid
 try:
  request_id=mixen.submit_text(prompt,duration,resolution,aspect_ratio,generate_audio)
 except Exception as e:
  logger.exception("Mixen text-video submit failed")
  with db() as c:
   c.execute("UPDATE generations SET status='failed',error=? WHERE id=?",(str(e)[:1000],gid))
   if p:c.execute("UPDATE users SET video_seconds_balance=video_seconds_balance+? WHERE id=?",(reserved,u["id"]))
   elif trial_reserved:c.execute("UPDATE users SET video_trial_used=0 WHERE id=?",(u["id"],))
  raise HTTPException(502,provider_error(e) or "Mixen video request could not be submitted")
 with db() as c:c.execute("UPDATE generations SET status='queued',provider_request_id=? WHERE id=?",(request_id,gid))
 return {"generation_id":gid,"token":token,"status":"queued","provider_request_id":request_id,"video_seconds_charged":reserved if p else seconds}

@APP.get("/api/photo/animate/{token}")
def animation_status(token:str):
 with db() as c:g=c.execute("SELECT * FROM generations WHERE public_token=? AND provider='mixen-wan-3.0'",(token,)).fetchone()
 if not g:raise HTTPException(404,"Generation not found")
 if g["status"] in ("queued","processing","submitting"):
  try:
   s=mixen.status(g["provider_request_id"],g["resolution"])
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
   err=s.get("error","Mixen generation failed")
   with db() as c:c.execute("UPDATE generations SET status='failed',error=? WHERE id=?",(err,g["id"]))
   return {"generation_id":g["id"],"status":"failed","error":provider_error(RuntimeError(err))}
 if g["status"]=="completed":
  return {"generation_id":g["id"],"status":"completed","video":{"url":g["video_url"]}}
 return {"generation_id":g["id"],"status":g["status"],"error":g["error"]}
@APP.get("/api/photo/animate/{token}/download")
def animation_download(token:str):
 with db() as c:g=c.execute("SELECT * FROM generations WHERE public_token=? AND status='completed'",(token,)).fetchone()
 if not g or not g["video_url"]:raise HTTPException(404,"Video is not ready")
 headers={}
 if g["provider"]=="mixen-wan-3.0":
  key=os.getenv("MIXEN_API_KEY","")
  if not key:raise HTTPException(503,"Mixen provider is not configured")
  headers["Authorization"]=f"Bearer {key}"
 try:
  r=requests.get(g["video_url"],headers=headers,timeout=180)
 except requests.RequestException:raise HTTPException(502,"Video download failed")
 if r.status_code>=400:raise HTTPException(502,"Video provider returned an error")
 return StreamingResponse(io.BytesIO(r.content),media_type="video/mp4",headers={"Content-Disposition":'attachment; filename="fileforge-animation.mp4"'})

@APP.get("/api/photo/history")
def photo_history(req:Request):
 u=user(req)
 if not u:raise HTTPException(401,"Войдите в аккаунт")
 with db() as c:
  # Refresh stuck jobs: ask Mixen once for any job still pending >90s
  stuck=c.execute("""SELECT id,public_token,provider_request_id,resolution,created_at FROM generations
                     WHERE user_id=? AND status IN ('queued','processing','submitting') AND created_at<?""",
                  (u["id"],int(time.time())-90)).fetchall()
  for g in stuck:
   try:s=mixen.status(g["provider_request_id"],g["resolution"])
   except Exception:s={"status":g["status"]}
   if s["status"]=="completed":
    c.execute("UPDATE generations SET status='completed',completed_at=?,video_url=? WHERE id=?",(int(time.time()),s["video"]["url"],g["id"]))
   elif s["status"]=="failed":
    c.execute("UPDATE generations SET status='failed',error=? WHERE id=?",(s.get("error") or "provider failed",g["id"]))
   elif int(time.time())-g["created_at"]>3600:
    c.execute("UPDATE generations SET status='failed',error=? WHERE id=?",(provider_error(RuntimeError("timeout")),g["id"]))
  rows=c.execute("SELECT public_token,status,prompt,created_at,completed_at,error FROM generations WHERE user_id=? ORDER BY id DESC LIMIT 20",(u["id"],)).fetchall()
 return {"items":[{"token":r["public_token"],"status":r["status"],"prompt":(r["prompt"] or "")[:120],"created_at":r["created_at"],"completed_at":r["completed_at"],"error":(r["error"] or "")[:200]} for r in rows]}

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

@APP.get("/api/payment/order/{order_id}")
def payment_order(req:Request,order_id:int):
 u=user(req)
 if not u:raise HTTPException(401,"Войдите в аккаунт")
 with db() as c:
  o=c.execute("SELECT id,payment_type,project_id,status,amount FROM orders WHERE id=? AND user_id=?",(order_id,u["id"])).fetchone()
  if not o:raise HTTPException(404,"Заказ не найден")
  return {"order_id":o["id"],"payment_type":o["payment_type"],"project_id":o["project_id"],"status":o["status"],"amount":o["amount"]}

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
   if o["payment_type"]=="ai_video":
    if not o["project_id"]:raise HTTPException(400,"AI Video order has no project")
    c.execute("UPDATE video_projects SET status='paid',updated_at=? WHERE id=? AND user_id=?",(int(time.time()),o["project_id"],o["user_id"]))
   else:grant_premium(c,o["user_id"])
   c.execute("UPDATE orders SET status='paid',paid_at=? WHERE id=?",(int(time.time()),oid))
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
def config():return {"version":"6.0","price_rub":PRICE,"premium_video_seconds":PREMIUM_VIDEO_SECONDS,"free_video_trial_seconds":FREE_VIDEO_TRIAL_SECONDS,"ai_upscale":bool(os.getenv("AI_UPSCALE_URL") and os.getenv("AI_UPSCALE_TOKEN")),"animation":mixen.configured(),"image_ai":mixen.configured(),"payments":(yookassa.configured() if os.getenv("PAYMENT_PROVIDER","manual").lower()=="yookassa" else bool(os.getenv("PAYMENT_API_URL") and os.getenv("PAYMENT_API_TOKEN")))}
