const $=x=>document.getElementById(x);
function toast(x){let t=$("toast");t.textContent=x;t.style.display="block";clearTimeout(window.__toast);window.__toast=setTimeout(()=>t.style.display="none",4200)}
function sizeLabel(n){if(n<1024)return `${n} Б`;if(n<1024*1024)return `${(n/1024).toFixed(1)} КБ`;return `${(n/1024/1024).toFixed(2)} МБ`}
async function run(url,id,p){let f=$(id)?.files?.[0];if(!f)return toast("Сначала выберите файл");let d=new FormData();d.append("file",f);for(let[k,v]of Object.entries(p||{}))d.append(k,v);toast("Обрабатываем файл…");try{let r=await fetch(url,{method:"POST",body:d});if(!r.ok){let j=await r.json().catch(()=>({}));throw Error(j.detail||"Ошибка обработки")};let b=await r.blob(),a=document.createElement("a");a.href=URL.createObjectURL(b);a.download=(r.headers.get("content-disposition")||"").match(/filename="([^"]+)"/)?.[1]||"result";document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000);if(url==="/api/image/compress"){let diff=Math.round((1-b.size/f.size)*100);toast(diff>0?`✓ Сжато: ${sizeLabel(f.size)} → ${sizeLabel(b.size)} (−${diff}%)`:`✓ Готово: ${sizeLabel(f.size)} → ${sizeLabel(b.size)}`)}else{toast("✓ Готово — файл подготовлен к скачиванию")}}catch(e){toast(e.message)}}
/* === Tasks panel (bottom-right popup) === */
const tasks=new Map();let tasksMinimized=false;
function esc(s){
  return String(s)
    .split("&").join("\u0026amp;")
    .split("<").join("\u003Clt;")
    .split(">").join("\u003Egt;")
    .split('"').join("\u0022quot;")
    .split("'").join("\u0027#39;");
}
function addTask(id,label){tasks.set(id,{label,status:"processing",progress:null,started:Date.now()});renderTasks();if(tasksMinimized)toggleTasks()}
function setTask(id,st,progress){const t=tasks.get(id);if(!t)return;t.status=st;if(progress!=null)t.progress=progress;renderTasks()}
function removeTask(id){tasks.delete(id);renderTasks()}
function toggleTasks(){tasksMinimized=!tasksMinimized;$("tasksList").style.display=tasksMinimized?"none":"block";$("tasksToggle").textContent=tasksMinimized?"+":"—"}
function renderTasks(){
 const list=$("tasksList"),empty=$("tasksEmpty");if(!list)return;
 list.querySelectorAll(".task").forEach(e=>e.remove());
 const arr=[...tasks.entries()];
 $("tasksCount").textContent=arr.length?`(${arr.filter(([,t])=>t.status==="processing").length} актив.)`:"";
 empty.style.display=arr.length?"none":"block";
 for(const[id,t]of arr){
  const el=document.createElement("div");el.className="task";el.id="task-"+id;
  const icon=t.status==="processing"?"<span class=\"task-spin\"></span>":t.status==="completed"?"<span class=\"task-ok\">✓</span>":"<span class=\"task-fail\">✕</span>";
  const prog=t.progress!=null?`<div class=\"task-bar\"><i style=\"width:${Math.round(t.progress)}%\"></i></div>`:"";
  el.innerHTML=`<div class="task-top">${icon}<span class="task-label">${esc(t.label)}</span><button class="task-x" onclick="removeTask('${id}')">✕</button></div>${prog}`;
  list.insertBefore(el,empty);
 }
}
async function downloadBlob(b,fname){const a=document.createElement("a");a.href=URL.createObjectURL(b);a.download=fname;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
async function loadHistory(){
 try{
  const r=await fetch("/api/photo/history");
  if(r.status===401)return;
  const j=await r.json();
  if(!j.items)return;
  const w=$("historyWrap"),l=$("historyList");
  if(!w)return;
  w.style.display=j.items.length?"block":"none";
  l.innerHTML=j.items.map(it=>`<div class="history-item st-${esc(it.status)}"><span class="hi-status">${it.status==="completed"?"✓":it.status==="failed"?"✕":"◌"}</span><div class="hi-body"><span class="hi-prompt">${esc(it.prompt||"Без описания")}</span><span class="hi-date">${new Date(it.created_at*1000).toLocaleString("ru-RU",{day:"2-digit",month:"short",hour:"2-digit",minute:"2-digit"})}</span></div>${it.status==="completed"?`<a class="hi-dl" href="/api/photo/animate/${encodeURIComponent(it.token)}/download">↓</a>`:""}</div>`).join("");
 }catch{}
}
setInterval(loadHistory,10000);loadHistory();
async function generateImage(){
 const p=$("genPrompt")?.value?.trim(); if(!p)return toast("Опишите, что нужно сгенерировать");
 const id="gen-"+Date.now(); addTask(id,"AI-генерация: "+p.slice(0,40));
 try{
  let d=new FormData(); d.append("prompt",p); d.append("size","1000x1000");
  let r=await fetch("/api/image/generate",{method:"POST",body:d});
  if(!r.ok){let j=await r.json().catch(()=>({}));throw Error(j.detail||"Ошибка генерации")}
  await downloadBlob(await r.blob(),"fileforge-generated.png");
  setTask(id,"completed");setTimeout(()=>removeTask(id),6000);
  toast("✓ Изображение сгенерировано — скачивание началось");
 }catch(e){setTask(id,"failed");setTimeout(()=>removeTask(id),8000);toast(e.message)}
}
async function editImage(){
 const f=$("edit")?.files?.[0]; if(!f)return toast("Сначала выберите изображение");
 const p=$("editPrompt")?.value?.trim(); if(!p)return toast("Опишите, что нужно изменить");
 const id="edit-"+Date.now(); addTask(id,"AI-редактирование: "+p.slice(0,40));
 try{
  let d=new FormData(); d.append("file",f); d.append("prompt",p);
  let r=await fetch("/api/image/ai-edit",{method:"POST",body:d});
  if(!r.ok){let j=await r.json().catch(()=>({}));throw Error(j.detail||"Ошибка редактирования")}
  await downloadBlob(await r.blob(),"fileforge-edited.png");
  setTask(id,"completed");setTimeout(()=>removeTask(id),6000);
  toast("✓ Готово — файл подготовлен к скачиванию");
 }catch(e){setTask(id,"failed");setTimeout(()=>removeTask(id),8000);toast(e.message)}
}
async function animatePhoto(){
 const f=$("anim")?.files?.[0]; if(!f)return toast("Сначала выберите фото");
 const prompt=$("animPrompt")?.value?.trim(); if(!prompt)return toast("Опишите, какое движение должно быть на видео");
 const id="anim-"+Date.now(); addTask(id,"Оживление фото: "+prompt.slice(0,40));
 const d=new FormData(); d.append("file",f); d.append("prompt",prompt);
 d.append("duration",$("animDuration").value); d.append("resolution",$("animResolution").value);
 d.append("aspect_ratio",$("animAspect").value); d.append("generate_audio",$("animAudio").checked?"true":"false");
 toast("Отправляем запрос в Wan 3.0…");
 try{
  let r=await fetch("/api/photo/animate",{method:"POST",body:d}),j=await r.json().catch(()=>({}));
  if(!r.ok)throw Error(j.detail||"Не удалось запустить генерацию");
  const token=j.token;let last="";
  for(let i=0;i<240;i++){
   await new Promise(x=>setTimeout(x,3000));
   const s=await (await fetch("/api/photo/animate/"+encodeURIComponent(token))).json();
   if(s.status!==last){last=s.status;toast(s.status==="processing"?"Wan 3.0 генерирует видео…":s.status==="queued"?"Запрос в очереди…":"Проверяем результат…")}
   if(typeof s.progress==="number")setTask(id,s.status,s.progress);else setTask(id,s.status);
   if(s.status==="completed"){
    const b=await (await fetch("/api/photo/animate/"+encodeURIComponent(token)+"/download")).blob();
    await downloadBlob(b,"fileforge-animation.mp4");
    setTask(id,"completed");setTimeout(()=>removeTask(id),6000);loadHistory();
    toast("✓ Видео готово — скачивание началось");return;
   }
   if(s.status==="failed")throw Error(s.error||"Wan 3.0 generation failed");
  }
  throw Error("Генерация выполняется слишком долго. Попробуйте проверить позже.");
 }catch(e){setTask(id,"failed");setTimeout(()=>removeTask(id),10000);toast(e.message)}
}
let authMode="login";
function setAuthMode(mode){
 authMode=mode;
 $("loginTab").classList.toggle("active",mode==="login");
 $("registerTab").classList.toggle("active",mode==="register");
 $("passConfirm").style.display=mode==="register"?"block":"none";
 $("termsWrap").style.display=mode==="register"?"flex":"none";
 $("authSubmit").innerHTML=mode==="register"?"Создать аккаунт <b>→</b>":"Войти <b>→</b>";
}
async function submitAuth(){
 let d=new FormData(),email=$("email").value.trim(),password=$("pass").value;
 if(!email||!password)return toast("Заполните email и пароль");
 d.append("email",email);d.append("password",password);
 const url=authMode==="register"?"/api/auth/register":"/api/auth/login";
 if(authMode==="register"){d.append("password_confirm",$("passConfirm").value);d.append("accept_terms",$("terms").checked?"true":"false");}
 try{
  let r=await fetch(url,{method:"POST",body:d}),j=await r.json().catch(()=>({}));
  if(!r.ok)throw Error(j.detail||"Ошибка");
  if(authMode==="register"&&j.verification_sent)toast("✓ Аккаунт создан. Проверьте почту и подтвердите email.");
  else toast(authMode==="register"?"✓ Аккаунт создан":"✓ Вы вошли");
  setAuthMode("login");load();
 }catch(e){toast(e.message)}
}
async function logout(){
 try{let r=await fetch("/api/auth/logout",{method:"POST"});if(!r.ok)throw Error("Не удалось выйти");toast("Вы вышли из аккаунта");load()}catch(e){toast(e.message)}
}
async function load(){
 try{
  let j=await(await fetch("/api/me")).json();
  if(!j.authenticated){$("me").textContent=`Гость · ${j.limit} обычных операций/день · AI-пробник ${j.video_trial_remaining} сек.`;$("verifyHint").textContent="";return;}
  const video=j.premium?`AI-секунды: ${j.video_seconds_remaining} сек.`:`Пробник AI-видео: ${j.video_trial_remaining} сек.`;
  $("me").textContent=`${j.email} · ${j.premium?"Premium":"Free"} · ${j.email_verified?"email подтверждён":"email не подтверждён"} · ${video}`;
  $("verifyHint").textContent=!j.email_verified?"Письмо с подтверждением нужно открыть в вашей почте. Если письмо не пришло, запросите его повторно.":"Email подтверждён.";
  $("resendVerify").style.display=(!j.email_verified&&j.email_verification_enabled!==false)?"block":"none";
 }catch{$("me").textContent="Не удалось проверить статус"}
}
async function resendVerification(){
 try{
  let r=await fetch("/api/auth/resend-verification",{method:"POST"}),j=await r.json().catch(()=>({}));
  if(!r.ok)throw Error(j.detail||"Не удалось отправить письмо");
  toast(j.already_verified?"Email уже подтверждён":"✓ Письмо отправлено повторно");
 }catch(e){toast(e.message)}
}
const observer=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add("visible");observer.unobserve(e.target)}}),{threshold:.08});document.querySelectorAll(".reveal").forEach(e=>observer.observe(e));
["up","conv","cmp","pdf","djvu","pdjvu","anim"].forEach(id=>{let el=$(id);if(!el)return;el.addEventListener("change",()=>{if(el.files?.[0])el.closest(".tool-card")?.classList.add("has-file")})});
const q=$("quality"),qv=$("qualityValue");if(q&&qv){const upd=()=>{q.style.setProperty("--fill",q.value+"%");qv.textContent=`${q.value}%`};q.addEventListener("input",upd);upd()}
load();
