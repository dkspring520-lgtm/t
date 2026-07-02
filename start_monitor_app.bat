@echo off
chcp 65001 >nul
cd /d "C:\Users\dkspr\Desktop\dabao"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "C:\Users\dkspr\Desktop\dabao\launcher.py"
) else (
  start "" python "C:\Users\dkspr\Desktop\dabao\launcher.py"
)
