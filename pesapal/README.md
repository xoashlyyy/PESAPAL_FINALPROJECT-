# PesaPal

Money management for Kenyan students and housemates: split bills, settle with M-Pesa, build a Dharura (emergency) fund, keep a monthly budget, share rent and house kitties, and keep a tamper-evident history.

**Stack:** FastAPI (Python), SQLite, plain HTML/CSS/JavaScript, Safaricom Daraja STK Push.

## Features
- Split expenses equally, by percentage or by custom amounts, with the maths done on the server
- Pay a housemate through an M-Pesa STK push (simulated until you add Daraja keys)
- Dharura fund with goal, runway in months, quick stashes, round-ups and emergency withdrawals
- Monthly budget with category limits and a payday countdown
- Shared bills (rent, wifi, KPLC tokens, water) that split automatically when one person pays
- House kitties for shared pots
- Alerts and automatic reminders
- Hash-chained history with a Verify button

## Run it on your laptop (Windows)
1. Install **Python 3.10 or newer** from https://www.python.org/downloads/ and tick **Add Python to PATH**.
2. Install **VS Code** (https://code.visualstudio.com) and **Git** (https://git-scm.com/downloads).
3. Open the project folder in VS Code, then open a terminal (Terminal > New Terminal) and run:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --port 8000
```
On macOS or Linux, use `source .venv/bin/activate` instead of the second line.

4. Open http://localhost:8000 in Chrome.
5. Sign in with `amina@pesapal.demo`, `brian@pesapal.demo`, `wanjiku@pesapal.demo` or `kevin@pesapal.demo` and PIN `1234`, or tap Create account.

Run the tests with `pytest tests`.

## Put it on GitHub
1. Create a free account at https://github.com, click **New repository**, name it `pesapal`, leave it empty (no README), and click **Create**.
2. In the VS Code terminal, inside the project folder:

```
git init
git add .
git commit -m "PesaPal: first version"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/pesapal.git
git push -u origin main
```
3. Sign in when the browser opens. Refresh the GitHub page and your code is there.
4. Later changes: `git add .`, `git commit -m "what you changed"`, `git push`.

`pesapal.db`, `.venv` and `.env` are in `.gitignore`, so your database and secrets are never uploaded.

## Email and PIN sign in
- Create account sends a 6 digit code to your email. Entering it verifies the address.
- PINs are 4 to 6 digits and stored only as salted PBKDF2 hashes. Five wrong PINs lock the account for 5 minutes.
- Forgot PIN sends another code by email.
- **No email server?** The code prints in the terminal and, in demo mode, also shows on screen. To send real emails set `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD` and `SMTP_FROM`. A Gmail account with an app password works. Set `PESAPAL_DEMO=0` so codes never show on screen.

## Real M-Pesa (optional)
Create a Daraja sandbox app at https://developer.safaricom.co.ke, then set these before starting the server:
`DARAJA_CONSUMER_KEY`, `DARAJA_CONSUMER_SECRET`, `DARAJA_PASSKEY`, `DARAJA_SHORTCODE`, `DARAJA_CALLBACK_URL` (public, for example from ngrok) and `DARAJA_CALLBACK_SECRET`.

## Presenting it
Set `PESAPAL_DEMO=1` (the default) and open http://localhost:8000/docs to call `/demo/tamper`, then press Verify log on the History screen to show tampering being caught. `/demo/repair` restores it.
