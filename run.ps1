# تشغيل مجاني ومستقر على جهازك مع إعادة تشغيل تلقائية عند التوقف
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .env)) {
    Write-Host "انسخ .env.example إلى .env واملأ المفاتيح أولاً." -ForegroundColor Yellow
    exit 1
}

Write-Host "تشغيل البوت (مجاني محلياً). أبقِ النافذة مفتوحة. Ctrl+C للإيقاف." -ForegroundColor Green
while ($true) {
    python bot.py
    $code = $LASTEXITCODE
    Write-Host "توقف البوت (code=$code) — إعادة التشغيل بعد 5 ثوانٍ..." -ForegroundColor Yellow
    Start-Sleep -Seconds 5
}
