from flask import Flask, render_template, request, redirect, send_file, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, pandas as pd
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from reportlab.pdfgen import canvas
import pandas as pd
from flask import send_file
from reportlab.pdfgen import canvas
from datetime import datetime


app = Flask(__name__)
app.secret_key = "supersecretkey"

login_manager = LoginManager(app)
login_manager.login_view = "login"

# --- Database Helper ---
def db():
    return sqlite3.connect("finance.db")

# --- Login ---
class User(UserMixin):
    def __init__(self,id): self.id=id

@login_manager.user_loader
def load_user(id):
    return User(id)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = db().execute(
            "SELECT id, password FROM users WHERE email=?",
            (request.form["email"],)
        ).fetchone()

        if user and check_password_hash(user["password"], request.form["password"]):
            login_user(User(user["id"]))
            return redirect(url_for("dashboard"))

        return "Invalid credentials"

    return render_template("login.html")


@app.route("/logout")
#@login_required
def logout():
    logout_user()
    return redirect("/login")

# --- Compound Interest ---
def compound_interest(p, r, start, end):
    if not r or not end: return p
    years = (datetime.strptime(end,"%Y-%m-%d") - datetime.strptime(start,"%Y-%m-%d")).days / 365
    return round(p * ((1 + r/100) ** years),2)

# --- Routes ---
@app.route("/")
#@login_required
def home():
    return render_template("home.html")

@app.route("/expenses", methods=["GET","POST"])
#@login_required
def expenses():
    if request.method=="POST":
        db().execute("""INSERT INTO expenses
        (title,description,date,country,currency,mode,amount)
        VALUES (?,?,?,?,?,?,?)""",
        (request.form["title"], request.form["description"],
         request.form["date"], request.form["country"],
         request.form["currency"], request.form["mode"],
         request.form["amount"]))
        db().commit()

    rows = db().execute("SELECT * FROM expenses ORDER BY date DESC").fetchall()
    return render_template("expenses.html", expenses=rows)


@app.route("/investments", methods=["GET","POST"])
#@login_required
def investments():
    con = db()
    if request.method=="POST":
        maturity = compound_interest(float(request.form["principal"]),
                                      float(request.form.get("interest",0)),
                                      request.form["invest_date"],
                                      request.form.get("maturity_date"))
        con.execute("""INSERT INTO investments VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?)""",
            (request.form["instrument"],request.form.get("instrument_other"),
             request.form["title"],request.form["description"],
             request.form["country"],request.form["currency"],
             request.form["invest_date"],request.form.get("maturity_date"),
             request.form["principal"],request.form.get("interest"),maturity))
        con.commit()

    rows = db().execute("SELECT * FROM investments ORDER BY invest_date DESC").fetchall()
    return render_template("investments.html", investments=rows)
    
@app.route("/payments", methods=["GET","POST"])
#@login_required
def payments():
    con = db()
    if request.method=="POST":
        con.execute("""INSERT INTO payments VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?)""",
            (request.form["title"],request.form["description"],request.form["country"],
             request.form["currency"],request.form["interval"],request.form["due_date"],
             request.form["mode"],request.form.get("mode_other"),
             request.form["reminder_value"],request.form["reminder_unit"],request.form["email"]))
        con.commit()
    return render_template("payments.html")




#################

@app.route("/reports")
#@login_required
def reports():
    return render_template("reports.html")

# --- Chart API ---
@app.route("/api/expense-summary")
#@login_required
def expense_summary():
    country = request.args.get("country")
    con = db()
    rows = con.execute("""SELECT mode, SUM(amount) FROM expenses WHERE country=? GROUP BY mode""",
                        (country,)).fetchall()
    return {"labels":[r[0] for r in rows],"values":[r[1] for r in rows]}

# --- Export ---
@app.route("/export/<table>/<fmt>")
#@login_required
def export_data(table, fmt):
    df = pd.read_sql(f"SELECT * FROM {table}", db())
    path = f"{table}.{ 'csv' if fmt=='csv' else 'xlsx' }"
    if fmt=="csv": df.to_csv(path,index=False)
    else: df.to_excel(path,index=False)
    return send_file(path, as_attachment=True)

@app.route("/export/pdf/<table>")
#@login_required
def export_pdf(table):
    rows = db().execute(f"SELECT * FROM {table}").fetchall()
    path = f"{table}.pdf"
    c = canvas.Canvas(path)
    y=800
    for r in rows:
        c.drawString(40,y,str(r))
        y-=20
    c.save()
    return send_file(path, as_attachment=True)

##################

@app.route("/generate-report")
#@login_required
def generate_report():
    entity = request.args.get("entity")
    country = request.args.get("country")
    period = request.args.get("period")
    fmt = request.args.get("format")

    query = f"SELECT * FROM {entity} WHERE country=?"
    df = pd.read_sql(query, db(), params=(country,))

    # --- Period filtering ---
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        now = datetime.now()

        if period == "monthly":
            df = df[df["date"].dt.month == now.month]
        elif period == "quarterly":
            df = df[df["date"].dt.quarter == now.quarter]
        elif period == "half-yearly":
            df = df[df["date"].dt.month <= 6]
        elif period == "annual":
            df = df[df["date"].dt.year == now.year]

    filename = f"{entity}_{country}_{period}"

    # --- CSV ---
    if fmt == "csv":
        path = f"{filename}.csv"
        df.to_csv(path, index=False)
        return send_file(path, as_attachment=True)

    # --- Excel ---
    if fmt == "excel":
        path = f"{filename}.xlsx"
        df.to_excel(path, index=False)
        return send_file(path, as_attachment=True)

    # --- PDF ---
    if fmt == "pdf":
        path = f"{filename}.pdf"
        c = canvas.Canvas(path)
        y = 800

        for _, row in df.iterrows():
            c.drawString(40, y, str(row.to_dict()))
            y -= 20
            if y < 50:
                c.showPage()
                y = 800

        c.save()
        return send_file(path, as_attachment=True)


##################
# --- Email reminders (print demo) ---
def send_reminders():
    rows = db().execute("""SELECT title,email FROM payments
                           WHERE due_date=date('now','+1 day')""").fetchall()
    for r in rows:
        print("Email reminder:",r)

scheduler = BackgroundScheduler()
scheduler.add_job(send_reminders, "interval", hours=24)
scheduler.start()

if __name__=="__main__":
#    app.run()
     app.run(
          host="0.0.0.0",
          port=int(os.environ.get("PORT", 8080))
     )
