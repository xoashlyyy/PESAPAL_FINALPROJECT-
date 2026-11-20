# How PesaPal answers each objective and research question

| Objective | Where in the code | How to see it in the app |
|---|---|---|
| General: M-Pesa integrated expense sharing | whole system, main.py with static/index.html | Run the demo, sign in, add an expense, pay with Mpesa |
| 1. Interface for recording and managing expenses | static/index.html, viewAdd, viewExpenses, saveExpense | Add tab, then Expenses tab |
| 2. Automated splitting algorithm | splitting.py calculate_shares, route POST /expenses/preview | Add tab, switch Equal, Percentage, Custom, type wrong numbers |
| 3. Daraja M-Pesa settlement | mpesa.py send_stk_push, main.py /settlements and /mpesa/callback | Home, press Pay, confirm, wait for the receipt |
| 4. Notifications and reminders | ledger.py auto_reminders, main.py reminder_loop, /notifications | Home Remind button, Alerts tab, Run reminder check now |
| 5. Secure transaction history | ledger.py hash chain, GET /history/verify | History tab, Verify log |

| Research question | Evidence in the system |
|---|---|
| 1. Challenges of existing methods | Objectives screen explains each manual pain point and the matching feature |
| 2. How an app improves management | One shared live ledger, one form to add, one tap to pay |
| 3. Automated splitting reduces errors | Server side maths, total check, invalid totals are refused |
| 4. M-Pesa integration improves settlement | STK push prompt, automatic receipt, balances update by themselves |
| 5. Effectiveness for transparency and accountability | Breakdown dialog, locked log, and the 1 to 5 survey whose averages come from GET /feedback/summary |

The Objectives button at the top of the app shows this same mapping with live numbers.

## Added money management features
| Feature | Where in the code | How to see it |
|---|---|---|
| Dharura (emergency) fund, round-ups | money.py /fund routes | Money tab |
| Monthly budget and payday countdown | money.py /budget | Money tab |
| Shared bills that split when paid | money.py /bills, ledger.py | House tab, I paid, split it |
| House kitties | money.py /kitties | House tab |
| Email and PIN sign in, lockout, reset | main.py /auth routes, auth.py, mailer.py | Sign out from the menu |
