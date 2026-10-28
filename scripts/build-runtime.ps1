# Готовит всё, что кладётся в установщик рядом с aya.exe:
#   src-tauri/runtime/python  — переносной Python 3.11 + базовый набор пакетов + uv.exe
#   src-tauri/stage/core      — код ядра без тестов, весов моделей и голоса автора
# Запуск: pwsh scripts/build-runtime.ps1   (нужны uv и git в PATH)
$ErrorActionPreference = "Stop"

# python-build-standalone: сборка, провисевшая не меньше двух недель
$PyUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/20261013/cpython-3.11.17%2B20261013-x86_64-pc-windows-msvc-install_only.tar.gz"
$PySha = "7616ab1f74aab975bd91eb0a657a74a092a63a46cac38c5f3f9415c79fd781f5"

$Root = Split-Path -Parent $PSScriptRoot
$Tauri = Join-Path $Root "src-tauri"
$Runtime = Join-Path $Tauri "runtime"
$Py = Join-Path $Runtime "python"
$Stage = Join-Path $Tauri "stage"
$Cache = Join-Path $Tauri "target\runtime-cache"

function Step($t) { Write-Host "==> $t" -ForegroundColor Cyan }

# 1. Python
New-Item -ItemType Directory -Force $Cache | Out-Null
$Tar = Join-Path $Cache "python-3.11.17-20261013.tar.gz"
if (-not (Test-Path $Tar) -or (Get-FileHash $Tar -Algorithm SHA256).Hash.ToLower() -ne $PySha) {
    Step "Скачиваю Python"
    Invoke-WebRequest -Uri $PyUrl -OutFile $Tar -UseBasicParsing
}
$Got = (Get-FileHash $Tar -Algorithm SHA256).Hash.ToLower()
if ($Got -ne $PySha) { throw "SHA-256 не совпал: $Got" }

Step "Распаковываю Python"
if (Test-Path $Runtime) { Remove-Item -Recurse -Force $Runtime }
New-Item -ItemType Directory -Force $Runtime | Out-Null
& (Join-Path $env:SystemRoot "System32/tar.exe") -xzf $Tar -C $Runtime   # системный tar: внутри архива — папка python/
if (-not (Test-Path (Join-Path $Py "python.exe"))) { throw "в архиве нет python/python.exe" }
Get-ChildItem $Py -Recurse -Filter "EXTERNALLY-MANAGED" | Remove-Item -Force

# 2. Базовый набор пакетов
Step "Ставлю базовый набор (core/requirements.txt)"
& uv pip install --python (Join-Path $Py "python.exe") --no-cache -r (Join-Path $Root "core\requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "uv pip install не удался" }

# 3. uv рядом с python.exe — им ядро докачивает наборы по кнопке
$Uv = (Get-Command uv).Source
Copy-Item $Uv (Join-Path $Py "uv.exe") -Force

# 4. Лишнее из Python: тесты стандартной библиотеки, кэш байткода
Step "Убираю лишнее"
foreach ($d in @("Lib\test", "Lib\idlelib\idle_test", "Lib\tkinter\test")) {
    $p = Join-Path $Py $d
    if (Test-Path $p) { Remove-Item -Recurse -Force $p }
}
Get-ChildItem $Py -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# 5. Код ядра: только отслеживаемые git файлы, без тестов, весов и голоса автора
Step "Собираю ядро"
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
$Skip = '^core/tests/|\.safetensors$|\.pth$|^core/voice/f5_ru/refs_user/'
Push-Location $Root
try {
    $files = git ls-files core | Where-Object { $_ -notmatch $Skip }
    foreach ($f in $files) {
        $dst = Join-Path $Stage $f
        New-Item -ItemType Directory -Force (Split-Path -Parent $dst) | Out-Null
        Copy-Item $f $dst -Force
    }
} finally { Pop-Location }

# 6. Проверка: ядро импортируется на этом Python
Step "Проверяю"
$env:AYA_DISABLE_TASKS = "1"
Push-Location $Stage
try {
    & (Join-Path $Py "python.exe") -X utf8 -c "import sys; sys.path.insert(0, '.'); import core.main, faster_whisper, edge_tts; print('RUNTIME_OK')"
    if ($LASTEXITCODE -ne 0) { throw "ядро не импортируется" }
} finally { Pop-Location }

$size = (Get-ChildItem $Runtime, $Stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Готово: {0:N0} МБ в runtime/ и stage/" -f $size) -ForegroundColor Green
