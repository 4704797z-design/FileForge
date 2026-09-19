import os
import requests

RESEND_API="https://api.resend.com/emails"

def configured():
    return bool(os.getenv("RESEND_API_KEY","").strip() and os.getenv("RESEND_FROM_EMAIL","").strip())

def send_verification(email,token,base_url):
    key=os.getenv("RESEND_API_KEY","").strip()
    sender=os.getenv("RESEND_FROM_EMAIL","").strip()
    if not key or not sender or not base_url:
        raise RuntimeError("Email verification requires RESEND_API_KEY, RESEND_FROM_EMAIL and PUBLIC_BASE_URL")
    link=f"{base_url.rstrip('/')}/api/auth/verify?token={token}"
    html=f"""<!doctype html>
<html lang="ru"><body style="margin:0;background:#090a10;color:#f4f5f8;font-family:Arial,sans-serif">
<div style="max-width:560px;margin:40px auto;padding:32px;border:1px solid #24283a;border-radius:18px;background:#11141e">
<h1 style="margin:0 0 16px">FileForge</h1>
<p>Подтвердите email, чтобы завершить создание аккаунта.</p>
<p><a href="{link}" style="display:inline-block;padding:12px 18px;border-radius:10px;background:#9b7bff;color:#fff;text-decoration:none;font-weight:700">Подтвердить email</a></p>
<p style="color:#9da5b7;font-size:13px">Ссылка действует 24 часа. Если вы не регистрировались в FileForge, просто проигнорируйте это письмо.</p>
</div></body></html>"""
    text=f"Подтвердите email в FileForge: {link}\nСсылка действует 24 часа."
    r=requests.post(
        RESEND_API,
        headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
        json={"from":sender,"to":[email],"subject":"Подтверждение email — FileForge","html":html,"text":text},
        timeout=20,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Resend error {r.status_code}: {r.text[:500]}")
    return r.json()
