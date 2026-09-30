const $=id=>document.getElementById(id);let project=null,watcher=null,watchOrder=null;
function esc(v){return String(v||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}
function say(role,text){const el=document.createElement("div");el.className="message "+role;el.textContent=text;$("messages").append(el);el.scrollIntoView({block:"end",behavior:"smooth"})}
function statusLabel(status){return {draft:"Идея",planned:"Сценарий готов",paid:"Оплачен — можно запускать",generating:"Генерация",editing:"Монтаж",completed:"Готово",failed:"Требуется внимание"}[status]||status}
function render(data){project=data;$("title").textContent=data.title||"Новый проект";$("projectState").textContent=statusLabel(data.status);$("create").disabled=!(data.status==="planned"||data.status==="paid");$("create").innerHTML=data.status==="paid"?'Запустить генерацию <b>→</b>':'Оплатить и создать видео <b>↗</b>';const q=data.pricing,$quote=$("quote");if(q){$quote.hidden=false;$quote.innerHTML=`<b>${q.seconds} AI-сек. · ${q.total_rub.toFixed(2)} ₽</b><span>Mixen: ${q.provider_cost_rub.toFixed(2)} ₽ (${q.mixen_rub_per_second.toFixed(2)} ₽/сек)</span><span>Сервисный сбор FileForge: ${q.service_fee_rub.toFixed(2)} ₽</span>`}else $quote.hidden=true;document.querySelectorAll("#steps li").forEach(el=>el.classList.remove("active"));const step=data.status==="planned"?"scenes":data.status==="paid"?"generation":data.status==="generating"?"generation":data.status==="editing"?"editing":data.status==="completed"?"completed":"idea";const active=document.querySelector(`[data-step="${step}"]`);if(active)active.classList.add("active");$("scenes").innerHTML=(data.scenes||[]).map(s=>`<div class="scene"><b>Сцена ${s.scene_number} · ${s.duration} сек · ${statusLabel(s.status)}</b><span>${esc(s.narration)}</span>${s.video_url?`<video controls preload="metadata" src="/api/ai-video/projects/${data.id}/scenes/${s.scene_number}/preview"></video>`:""}${s.error?`<button data-retry="${s.scene_number}">Повторить сцену</button>`:""}</div>`).join("");document.querySelectorAll("[data-retry]").forEach(b=>b.onclick=()=>retryScene(b.dataset.retry));const final=$("final");if(data.final_video){final.hidden=false;final.innerHTML=`<video controls src="/api/ai-video/projects/${data.id}/download"></video><a href="/api/ai-video/projects/${data.id}/download">Скачать MP4</a>`}else final.hidden=true;if(["generating","editing"].includes(data.status))watch(data.id)}
async function request(url,options={}){const r=await fetch(url,options),j=await r.json().catch(()=>({}));if(!r.ok)throw Error(j.detail||"Не удалось выполнить запрос");return j}
$("chatForm").addEventListener("submit",async e=>{e.preventDefault();const text=$("prompt").value.trim();if(!text)return;say("user",text);$("prompt").value="";try{if(!project){const data=new FormData();data.append("idea",text);data.append("format",$("format").value);data.append("duration",$("duration").value);const ref=$("reference").files[0];if(ref)data.append("reference",ref);const p=await request("/api/ai-video/projects",{method:"POST",body:data});render(p);say("assistant",p.assistant_message)}else{const p=await request(`/api/ai-video/projects/${project.id}/messages`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:text})});render(p);say("assistant",p.assistant_message)}}catch(err){say("assistant",err.message)}});
$("create").onclick=async()=>{if(!project)return;try{
 if(project.status==="paid"){render(await request(`/api/ai-video/projects/${project.id}/start`,{method:"POST"}));say("assistant","Отлично, запускаю генерацию сцен. Следите за статусами справа — вернусь с результатом.");return}
 const payment=await request(`/api/ai-video/projects/${project.id}/payment`,{method:"POST"});if(!payment.checkout_url){say("assistant",payment.message||"Платёж уже создан. Откройте ссылку из заказа или дождитесь подтверждения.");return}window.location.href=payment.checkout_url
}catch(err){say("assistant",err.message)}};
async function retryScene(number){try{render(await request(`/api/ai-video/projects/${project.id}/scenes/${number}/retry`,{method:"POST"}));say("assistant",`Повторно запускаю сцену ${number}.`)}catch(err){say("assistant",err.message)}}
function watch(id){if(watcher)return;watcher=setInterval(async()=>{try{const p=await request(`/api/ai-video/projects/${id}`);render(p);if(!["generating","editing"].includes(p.status)){clearInterval(watcher);watcher=null}}catch{}},5000)}
async function restoreOrder(orderId){
 try{
  const o=await request(`/api/payment/order/${orderId}`);
  if(!o.project_id)return;
  const p=await request(`/api/ai-video/projects/${o.project_id}`);
  render(p);
  if(o.status==="paid"){say("assistant","Оплата прошла — спасибо! Нажмите «Запустить генерацию», и я соберу ваш ролик.");history.replaceState(null,"","/ai-video");return}
  if(o.status==="canceled"){say("assistant","Оплата не была завершена. Вы можете попробовать снова — кнопка «Оплатить и создать видео» под сценами.");return}
  say("assistant","Платёж ещё не подтверждён. Обычно это занимает пару минут — страница обновится автоматически.");
  if(watchOrder)return;watchOrder=setInterval(async()=>{
   try{
    const s=await request(`/api/payment/order/${orderId}`);
    if(s.status==="paid"){clearInterval(watchOrder);watchOrder=null;const p2=await request(`/api/ai-video/projects/${s.project_id}`);render(p2);say("assistant","Оплата прошла — спасибо! Нажмите «Запустить генерацию», и я соберу ваш ролик.");history.replaceState(null,"","/ai-video")}
    else if(s.status==="canceled"){clearInterval(watchOrder);watchOrder=null;say("assistant","Оплата не была завершена. Вы можете попробовать снова.")}
   }catch{}
  },5000)
 }catch(err){say("assistant",err.message||"Не удалось проверить заказ")}
}
say("assistant","Расскажите, какой ролик хотите создать. Я подготовлю сценарий и план сцен, а вы подтвердите генерацию.");
(function(){const params=new URLSearchParams(location.search);if(params.get("payment")==="return"){const orderId=parseInt(params.get("order_id")||"0",10);if(orderId)restoreOrder(orderId)}})();
