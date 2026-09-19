const $=x=>document.getElementById(x);
function toast(x){let t=$("toast");t.textContent=x;t.style.display="block";clearTimeout(window.__toast);window.__toast=setTimeout(()=>t.style.display="none",4200)}
function sizeLabel(n){if(n<1024)return `${n} Б`;if(n<1024*1024)return `${(n/1024).toFixed(1)} КБ`;return `${(n/1024/1024).toFixed(2)} МБ`}
async function run(url,id,p){let f=$(id)?.files?.[0];if(!f)return toast("Сначала выберите файл");let d=new FormData();d.append("file",f);for(let[k,v]of Object.entries(p||{}))d.append(k,v);toast("Обрабатываем файл…");try{let r=await fetch(url,{method:"POST",body:d});if(!r.ok){let j=await r.json().catch(()=>({}));throw Error(j.detail||"Ошибка обработки")};let b=await r.blob(),a=document.createElement("a");a.href=URL.createObjectURL(b);a.download=(r.headers.get("content-disposition")||"").match(/filename="([^"]+)"/)?.[1]||"result";document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000);if(url==="/api/image/compress"){let diff=Math.round((1-b.size/f.size)*100);toast(diff>0?`✓ Сжато: ${sizeLabel(f.size)} → ${sizeLabel(b.size)} (−${diff}%)`:`✓ Готово: ${sizeLabel(f.size)} → ${sizeLabel(b.size)}`)}else{toast("✓ Готово — файл подготовлен к скачиванию")}}catch(e){toast(e.message)}}
async function animatePhoto(){
 const f=$("anim")?.files?.[0]; if(!f)return toast("Сначала выберите фото");
 const prompt=$("animPrompt")?.value?.trim(); if(!prompt)return toast("Опишите, какое движение должно быть на видео");
 const d=new FormData(); d.append("file",f); d.append("prompt",prompt);
 d.append("duration",$("animDuration").value); d.append("resolution",$("animResolution").value);
 d.append("aspect_ratio",$("animAspect").value); d.append("generate_audio",$("animAudio").checked?"true":"false");
 toast("Отправляем запрос в Seedance…");
 try{
  let r=await fetch("/api/photo/animate",{method:"POST",body:d}),j=await r.json().catch(()=>({}));
  if(!r.ok)throw Error(j.detail||"Не удалось запустить генерацию");
  const token=j.token;
  let last="";
  for(let i=0;i<180;i++){
   await new Promise(x=>setTimeout(x,3000));
   const s=await (await fetch("/api/photo/animate/"+encodeURIComponent(token))).json();
   if(s.status!==last){last=s.status;toast(s.status==="processing"?"Seedance генерирует видео…":s.status==="queued"?"Запрос в очереди…":"Проверяем результат…");}
   if(s.status==="completed"){
    const b=await (await fetch("/api/photo/animate/"+encodeURIComponent(token)+"/download")).blob();
    const a=document.createElement("a");a.href=URL.createObjectURL(b);a.download="fileforge-animation.mp4";
    document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000);toast("✓ Видео готово — скачивание началось");return;
   }
   if(s.status==="failed")throw Error(s.error||"Seedance generation failed");
  }
  throw Error("Генерация выполняется слишком долго. Попробуйте проверить позже.");
 }catch(e){toast(e.message)}
}
async function auth(url){let d=new FormData();d.append("email",$("email").value);d.append("password",$("pass").value);try{let r=await fetch(url,{method:"POST",body:d}),j=await r.json();if(!r.ok)throw Error(j.detail||"Ошибка");toast("✓ Готово");load()}catch(e){toast(e.message)}}
async function load(){try{let j=await(await fetch("/api/me")).json();$("me").textContent=j.authenticated?`Вы вошли как ${j.email} · ${j.premium?"Premium":"Free"} · лимит ${j.limit}/день`:`Гость · доступно ${j.limit} операций в день`}catch{$("me").textContent="Не удалось проверить статус"}}
async function buy(){try{let r=await fetch("/api/premium/create",{method:"POST"}),j=await r.json();if(!r.ok)throw Error(j.detail||"Ошибка");if(j.checkout_url)location.href=j.checkout_url;else toast("Платёжный провайдер пока не подключён")}catch(e){toast(e.message)}}
const observer=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add("visible");observer.unobserve(e.target)}}),{threshold:.08});document.querySelectorAll(".reveal").forEach(e=>observer.observe(e));
["up","conv","cmp","pdf","djvu","pdjvu","anim"].forEach(id=>{let el=$(id);if(!el)return;el.addEventListener("change",()=>{if(el.files?.[0])el.closest(".tool-card")?.classList.add("has-file")})});
const q=$("quality"),qv=$("qualityValue");if(q&&qv){q.addEventListener("input",()=>qv.textContent=`${q.value}%`)}
load();
