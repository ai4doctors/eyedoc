
import os
from flask import Flask, render_template, request, redirect, url_for, session, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me")

@app.route("/")
def home():
    paywall_on = os.environ.get("PAYWALL_ENABLED", "false").lower() in ("1","true","yes")
    if paywall_on and not session.get("pw_ok"):
        return redirect(url_for("login", next="/"))
    return render_template("index.html")

@app.route("/login", methods=["GET","POST"])
def login():
    next_url = request.args.get("next") or request.form.get("next") or "/"
    if request.method == "POST":
        password = request.form.get("password","")
        expected = os.environ.get("PAYWALL_PASSWORD","")
        if expected and password == expected:
            session["pw_ok"] = True
            return redirect(next_url)
        return render_template("paywall_login.html", error="Wrong password", next_url=next_url)
    return render_template("paywall_login.html", error="", next_url=next_url)

@app.route("/logout")
def logout():
    session.pop("pw_ok", None)
    return redirect(url_for("login"))

@app.route("/submit", methods=["POST"])
def submit():
    return jsonify(summary="ok", diagnosis="ok", plan="ok", references=["ref1","ref2"])

@app.route("/healthz")
def healthz():
    return jsonify(ok=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
