@echo off
echo Installing requirements, this only takes a moment...
pip install -r requirements.txt
echo.
echo PesaPal is starting. Open http://localhost:8000 in Chrome.
uvicorn main:app --host 0.0.0.0 --port 8000
