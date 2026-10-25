@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 镜我 LifeSim 消费者孪生工作台：http://127.0.0.1:5173/
echo 启动后，在浏览器打开上面的地址。关闭此窗口将停止预览。
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\serve_consumer_workbench.py"
pause
