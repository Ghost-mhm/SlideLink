@echo off
title SlideLink server
cd /d "%~dp0"
rem Start the server in the background window, then open SlideLink as a desktop app window (Edge app mode).
start "SlideLink server" /min python server.py
timeout /t 2 /nobreak >nul
start "" msedge --app=http://localhost:8765/ --window-size=1400,900 || start "" http://localhost:8765/
