from flask import Flask, render_template, request, jsonify
import json
import os

app = Flask(__name__)

def load_guidelines():
    path = os.path.join("guidelines", "canonical_references.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

GUIDELINES = load_guidelines()

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify(status="ok")

@app.route("/guidelines")
def guidelines():
    return jsonify(GUIDELINES)

@app.route("/submit", methods=["POST"])
def submit():
    data = request.form.to_dict()
    return render_template("result.html", data=data, guidelines=GUIDELINES)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
