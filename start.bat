@echo off
rd /s /q routes\__pycache__ 2>nul
rd /s /q __pycache__ 2>nul
python app.py
```

앞으로는 서버 시작할 때 터미널에서 `python app.py` 대신:
```
.\start.bat