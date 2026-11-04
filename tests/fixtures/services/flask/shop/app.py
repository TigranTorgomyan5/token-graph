from flask import Flask, Blueprint

app = Flask(__name__)
users = Blueprint("users", __name__, url_prefix="/users")


@users.route("/login", methods=["POST"])
def login():
    return check_password()


@users.get("/me")
def me():
    return {}


@app.route("/")
def index():
    return "ok"


app.register_blueprint(users)

if __name__ == "__main__":
    app.run(port=5000)
