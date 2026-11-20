#!/bin/sh
pip install -r requirements.txt
echo "PesaPal is starting. Open http://localhost:8000 in Chrome."
uvicorn main:app --host 0.0.0.0 --port 8000
