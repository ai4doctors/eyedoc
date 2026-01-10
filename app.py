
from flask import Flask, render_template, request
import PyPDF2

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static"
)

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/process", methods=["POST"])
def process_pdf():
    file = request.files.get("pdf")
    if not file:
        return "No file uploaded", 400

    reader = PyPDF2.PdfReader(file)
    text = ""
    for page in reader.pages:
        text += page.extract_text() or ""

    return render_template("result.html", extracted=text)

if __name__ == "__main__":
    app.run(debug=True)
