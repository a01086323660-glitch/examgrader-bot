# نشر مجاني تقريبي على Fly.io
# متطلبات: حساب Fly + تثبيت flyctl من https://fly.io/docs/hands-on/install-flyctl/

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command fly -ErrorAction SilentlyContinue) -and -not (Get-Command flyctl -ErrorAction SilentlyContinue)) {
    Write-Host "ثبّت flyctl أولاً: https://fly.io/docs/hands-on/install-flyctl/" -ForegroundColor Yellow
    exit 1
}
$fly = if (Get-Command fly -ErrorAction SilentlyContinue) { "fly" } else { "flyctl" }

if (-not (Test-Path .env)) {
    Write-Host "أنشئ ملف .env من .env.example أولاً." -ForegroundColor Yellow
    exit 1
}

Write-Host "1) تسجيل الدخول (إن لزم)..." -ForegroundColor Cyan
& $fly auth whoami 2>$null
if ($LASTEXITCODE -ne 0) { & $fly auth login }

Write-Host "2) إنشاء التطبيق والحجم إن لم يكونا موجودين..." -ForegroundColor Cyan
& $fly apps create examgrader-bot --org personal 2>$null
& $fly volumes create examdata --region fra --size 1 --yes 2>$null

Write-Host "3) رفع الأسرار من .env ..." -ForegroundColor Cyan
Get-Content .env | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { return }
    $k, $v = $line.Split("=", 2)
    $k = $k.Trim(); $v = $v.Trim().Trim('"').Trim("'")
    if ($k -in @("BOT_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY", "ALLOWED_USER_IDS", "LLM_PROVIDER", "GEMINI_MODEL")) {
        & $fly secrets set "$k=$v" --app examgrader-bot
    }
}

Write-Host "4) النشر..." -ForegroundColor Cyan
& $fly deploy --app examgrader-bot

Write-Host "تم. راقب السجلات: fly logs -a examgrader-bot" -ForegroundColor Green
