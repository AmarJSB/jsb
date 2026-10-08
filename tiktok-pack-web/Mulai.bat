@echo off
title TikTok Pack - jangan tutup jendela ini
cd /d "%~dp0"
if not exist "app.py" goto belum_ekstrak
set "UV_SYSTEM_CERTS=1"
set "UV=uv"
where uv >nul 2>nul
if not errorlevel 1 goto jalan
set "UV=%USERPROFILE%\.local\bin\uv.exe"
if exist "%UV%" goto jalan
echo Memasang uv, sekali saja...
powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
if not exist "%UV%" goto uv_gagal
:jalan
echo Menyiapkan TikTok Pack. Pertama kali download sekitar 350 MB, bisa 5-15 menit.
echo Browser kebuka sendiri kalau sudah siap.
echo.
"%UV%" run app.py
echo.
echo Aplikasi berhenti. Kalau ada tulisan error di atas, foto layar jendela ini.
pause
exit /b
:belum_ekstrak
echo Zip belum diekstrak. Klik kanan file zip, pilih Extract All, lalu buka Mulai.bat dari folder hasilnya.
pause
exit /b
:uv_gagal
echo Gagal memasang uv. Cek internet lalu klik Mulai.bat lagi. Cara manual ada di BACA-DULU.txt.
pause
exit /b
